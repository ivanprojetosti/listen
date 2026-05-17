"""GTK4 GUI for Listen voice-to-text application."""

import os
import struct
import threading
from pathlib import Path
from typing import Optional

from .runtime_env import ensure_utf8_runtime

ensure_utf8_runtime()

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk, Gio

from .audio_sources import is_meeting_capture_available
from .clipboard_copy import copy_plain_text
from .hotkey import GlobalHotkeyListener, HotkeyBinding, format_hotkey_examples
from .recorder import AudioRecorder
from .transcriber import Transcriber, ModelSize
from .settings import ListenSettings
from .transcription_store import (
    SavedTranscription,
    list_saved_transcriptions,
    read_transcription_text,
    save_transcription_text,
)
from .meeting_analysis import (
    analyze_video_to_topics,
    analyze_youtube_to_topics,
    ensure_youtube_download_url,
    ffmpeg_available,
    yt_dlp_available,
)
from .meeting_summary_ai import MeetingAISummaryOptions


class WaveformDrawingArea(Gtk.DrawingArea):
    """Custom widget for displaying audio waveform."""

    def __init__(self):
        super().__init__()
        self._samples = []
        self._max_samples = 100
        self.set_draw_func(self._draw)
        self.set_content_width(380)
        self.set_content_height(100)

    def _draw(self, area, cr, width, height):
        """Draw the waveform."""
        # Background
        cr.set_source_rgb(0.1, 0.1, 0.15)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        if not self._samples:
            # Draw center line when idle
            cr.set_source_rgb(0.3, 0.3, 0.4)
            cr.set_line_width(1)
            cr.move_to(0, height / 2)
            cr.line_to(width, height / 2)
            cr.stroke()
            return

        # Draw waveform
        cr.set_source_rgb(0.4, 0.8, 0.4)
        cr.set_line_width(2)

        sample_width = width / self._max_samples
        center_y = height / 2

        cr.move_to(0, center_y)
        for i, sample in enumerate(self._samples):
            x = i * sample_width
            # Scale amplitude to fit height
            amplitude = sample * (height / 2) * 0.9
            cr.line_to(x, center_y - amplitude)

        cr.stroke()

        # Draw mirror (bottom half)
        cr.set_source_rgba(0.4, 0.8, 0.4, 0.5)
        cr.move_to(0, center_y)
        for i, sample in enumerate(self._samples):
            x = i * sample_width
            amplitude = sample * (height / 2) * 0.9
            cr.line_to(x, center_y + amplitude)
        cr.stroke()

    def add_samples(self, audio_data: bytes):
        """Add audio samples to the waveform display."""
        # Convert bytes to normalized amplitude values
        samples = struct.unpack(f"{len(audio_data) // 2}h", audio_data)

        # Calculate RMS amplitude for this chunk
        if samples:
            rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
            normalized = min(rms / 32768.0 * 3, 1.0)  # Amplify for visibility
            self._samples.append(normalized)

            # Keep only recent samples
            if len(self._samples) > self._max_samples:
                self._samples = self._samples[-self._max_samples :]

        self.queue_draw()

    def clear(self):
        """Clear the waveform display."""
        self._samples = []
        self.queue_draw()


_NAV_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("record", "Gravar", "audio-input-microphone-symbolic"),
    ("history", "Histórico", "document-open-recent-symbolic"),
    ("video", "Vídeo", "video-x-generic-symbolic"),
    ("youtube", "YouTube", "internet-news-reader-symbolic"),
    ("settings", "Configurações", "emblem-system-symbolic"),
)


class ListenGUI(Adw.Application):
    """GTK4 GUI for the Listen voice-to-text application."""

    # States for the button cycle
    STATE_READY = "ready"  # Ready to record
    STATE_RECORDING = "recording"  # Currently recording
    STATE_TRANSCRIBING = "transcribing"  # Processing audio
    STATE_RESULT = "result"  # Showing result with copy option

    def __init__(
        self,
        model_size: Optional[ModelSize] = None,
        auto_copy: bool = True,
        *,
        corner_mode: bool = False,
        auto_start_recording: bool = False,
        save_transcriptions: Optional[bool] = None,
        save_directory: Optional[Path] = None,
        start_hidden: bool = False,
        global_hotkey: Optional[str] = None,
        window_width: int = 420,
        window_height: int = 560,
        screen_margin: int = 16,
        meeting_mode: Optional[bool] = None,
    ):
        super().__init__(application_id="com.listen.app")
        self.model_size = model_size
        self.auto_copy = auto_copy
        self.corner_mode = corner_mode
        self.auto_start_recording = auto_start_recording
        self.save_transcriptions = save_transcriptions
        self.save_directory = save_directory
        self.start_hidden = start_hidden
        self.global_hotkey = global_hotkey
        self.window_width = window_width
        self.window_height = window_height
        self.screen_margin = screen_margin

        settings = ListenSettings.load()
        if self.save_directory is None:
            self.save_directory = settings.resolved_save_directory()
        self.save_transcriptions = (
            save_transcriptions
            if save_transcriptions is not None
            else settings.save_transcriptions
        )
        self.meeting_mode = (
            meeting_mode if meeting_mode is not None else settings.meeting_mode
        )
        self._settings = settings

        self._recorder: Optional[AudioRecorder] = None
        self._transcriber: Optional[Transcriber] = None
        self._state = self.STATE_READY
        self._last_transcription = ""
        self._last_language = ""
        self._last_saved_path: Optional[Path] = None
        self._model_ready = False
        self._pending_auto_record = False
        self._hotkey_listener: Optional[GlobalHotkeyListener] = None
        self._saved_items: list[SavedTranscription] = []
        self._saved_list_lock = threading.Lock()
        self._pending_saved_items: Optional[list[SavedTranscription]] = None
        self._video_processing = False
        self._selected_video_path: Optional[Path] = None
        self._media_job_kind: str = "video"
        self._native_file_chooser: Optional[Gtk.FileChooserNative] = None

        # Transcription result must reach the GTK thread without passing str through
        # GLib.idle_add(..., user_data): GObject marshalling can use ASCII and break PT/AR text.
        self._transcription_delivery_lock = threading.Lock()
        self._pending_transcription: Optional[
            tuple[str, str, int, str | None, str | None, str | None]
        ] = None

        self.connect("activate", self._on_activate)

    def _on_activate(self, app):
        """Initialize the main window with menu lateral e páginas."""
        self.window = Adw.ApplicationWindow(application=app)
        self.window.set_title("Listen")
        if not self.corner_mode:
            self.window.set_default_size(max(self.window_width, 880), max(self.window_height, 620))
        else:
            self.window.set_default_size(self.window_width, self.window_height)
        if self.corner_mode:
            self.window.connect("realize", self._on_window_realize_corner)

        self._page_titles = {page_id: title for page_id, title, _ in _NAV_ITEMS}
        self._nav_rows: dict[str, Gtk.ListBoxRow] = {}

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        header = Adw.HeaderBar()
        self.header_title = Gtk.Label(label="Gravar")
        self.header_title.add_css_class("title")
        header.set_title_widget(self.header_title)

        model_options = ["tiny", "base", "small", "medium", "large-v3"]
        self._model_strings = Gtk.StringList.new(model_options)
        self.model_dropdown = Gtk.DropDown(model=self._model_strings)
        self.model_dropdown.set_tooltip_text(
            "Modelo Whisper para transcrição de áudio (não é o modelo OpenRouter)"
        )
        default_idx = (
            model_options.index(self.model_size)
            if self.model_size in model_options
            else 2
        )
        self.model_dropdown.set_selected(default_idx)
        self.model_dropdown.connect("notify::selected", self._on_model_changed)
        header.pack_end(self.model_dropdown)
        main_box.append(header)

        self.content_stack = Gtk.Stack()
        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.content_stack.set_vexpand(True)

        self._build_page_record()
        self._build_page_history()
        self._build_page_video()
        self._build_page_youtube()
        self._build_page_settings()

        content_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content_column.set_vexpand(True)
        content_column.append(self.content_stack)

        self.status_label = Gtk.Label(label="A carregar modelo…")
        self.status_label.add_css_class("dim-label")
        self.status_label.set_margin_start(20)
        self.status_label.set_margin_end(20)
        self.status_label.set_margin_top(8)
        self.status_label.set_margin_bottom(12)
        self.status_label.set_wrap(True)
        content_column.append(self.status_label)

        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sidebar.set_size_request(200, -1)
        sidebar.add_css_class("navigation-sidebar")

        brand = Gtk.Label(label="Listen")
        brand.add_css_class("title-2")
        brand.set_halign(Gtk.Align.START)
        brand.set_margin_start(16)
        brand.set_margin_end(16)
        brand.set_margin_top(16)
        brand.set_margin_bottom(8)
        sidebar.append(brand)

        self.nav_list = Gtk.ListBox()
        self.nav_list.add_css_class("navigation-sidebar")
        self.nav_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        for page_id, title, icon_name in _NAV_ITEMS:
            row = self._make_nav_row(page_id, title, icon_name)
            self._nav_rows[page_id] = row
            self.nav_list.append(row)
        self.nav_list.connect("row-selected", self._on_nav_row_selected)
        sidebar.append(self.nav_list)

        try:
            split = Adw.NavigationSplitView()
            split.set_sidebar_width(220)
            split.set_min_sidebar_width(180)
            split.set_sidebar(sidebar)
            split.set_content(content_column)
            split.set_vexpand(True)
            main_box.append(split)
        except AttributeError:
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            hbox.set_vexpand(True)
            hbox.append(sidebar)
            content_column.set_hexpand(True)
            hbox.append(content_column)
            main_box.append(hbox)

        self.window.set_content(main_box)
        self._apply_css()
        self._show_page("record")

        self._recorder = AudioRecorder(
            on_status_change=self._on_recording_status,
            meeting_mode=self.meeting_mode,
        )
        threading.Thread(target=self._load_model, daemon=True).start()

        if self.global_hotkey:
            self._hotkey_listener = GlobalHotkeyListener(
                self.global_hotkey,
                on_activate=self._on_global_hotkey,
            )
            self._hotkey_listener.start()

        if not self.start_hidden:
            self.window.present()
            if self.corner_mode:
                GLib.idle_add(self._position_bottom_right)

        self._refresh_saved_list()

    @staticmethod
    def _make_nav_row(page_id: str, title: str, icon_name: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.page_id = page_id  # type: ignore[attr-defined]
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row_box.set_margin_start(12)
        row_box.set_margin_end(12)
        row_box.set_margin_top(10)
        row_box.set_margin_bottom(10)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(20)
        label = Gtk.Label(label=title)
        label.set_xalign(0)
        label.set_hexpand(True)
        row_box.append(icon)
        row_box.append(label)
        row.set_child(row_box)
        return row

    def _new_page_box(self, page_id: str) -> Gtk.Box:
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        inner.set_margin_start(20)
        inner.set_margin_end(20)
        inner.set_margin_top(16)
        inner.set_margin_bottom(16)
        scroll.set_child(inner)
        self.content_stack.add_named(scroll, page_id)
        return inner

    @staticmethod
    def _labeled_field(title: str, widget: Gtk.Widget) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        label = Gtk.Label(label=title)
        label.set_xalign(0)
        label.add_css_class("heading")
        box.append(label)
        box.append(widget)
        return box

    @staticmethod
    def _dim_label(text: str) -> Gtk.Label:
        label = Gtk.Label(label=text)
        label.set_xalign(0)
        label.set_wrap(True)
        label.add_css_class("dim-label")
        return label

    @staticmethod
    def _switch_field(title: str, switch: Gtk.Switch) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        label = Gtk.Label(label=title)
        label.set_xalign(0)
        label.set_hexpand(True)
        row.append(label)
        row.append(switch)
        return row

    def _show_page(self, page_id: str) -> None:
        self.content_stack.set_visible_child_name(page_id)
        self.header_title.set_label(self._page_titles.get(page_id, "Listen"))
        row = self._nav_rows.get(page_id)
        if row is not None:
            self.nav_list.select_row(row)

    def _on_nav_row_selected(self, _list_box, row: Optional[Gtk.ListBoxRow]) -> None:
        if row is None:
            return
        page_id = getattr(row, "page_id", None)
        if page_id:
            self.content_stack.set_visible_child_name(page_id)
            self.header_title.set_label(self._page_titles.get(page_id, "Listen"))

    def _build_page_record(self) -> None:
        page = self._new_page_box("record")

        self.device_info_frame = Gtk.Frame()
        self.device_info_frame.add_css_class("device-info-frame")
        device_info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        device_info_box.set_margin_start(12)
        device_info_box.set_margin_end(12)
        device_info_box.set_margin_top(8)
        device_info_box.set_margin_bottom(8)
        self.device_info_label = Gtk.Label(label="⏳ A inicializar…")
        self.device_info_label.set_xalign(0)
        self.device_info_label.set_wrap(True)
        self.device_info_label.add_css_class("device-info-label")
        self.device_info_label.set_use_markup(True)
        device_info_box.append(self.device_info_label)
        self.device_info_frame.set_child(device_info_box)
        page.append(self.device_info_frame)

        meeting_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        meeting_label = Gtk.Label(label="Modo reunião")
        meeting_label.set_xalign(0)
        meeting_row.append(meeting_label)
        self.meeting_switch = Gtk.Switch()
        self.meeting_switch.set_active(self.meeting_mode)
        self.meeting_switch.set_sensitive(is_meeting_capture_available())
        self.meeting_switch.connect("notify::active", self._on_meeting_mode_toggled)
        meeting_row.append(self.meeting_switch)
        page.append(meeting_row)

        self.meeting_hint_label = Gtk.Label(
            label="Captura microfone + áudio do sistema (vozes da chamada)"
        )
        self.meeting_hint_label.set_xalign(0)
        self.meeting_hint_label.set_wrap(True)
        self.meeting_hint_label.add_css_class("dim-label")
        self.meeting_hint_label.set_visible(self.meeting_mode)
        page.append(self.meeting_hint_label)

        if not is_meeting_capture_available():
            unavailable = Gtk.Label(
                label="Modo reunião indisponível (requer PipeWire/PulseAudio)."
            )
            unavailable.set_xalign(0)
            unavailable.set_wrap(True)
            unavailable.add_css_class("dim-label")
            page.append(unavailable)

        self.waveform = WaveformDrawingArea()
        page.append(Gtk.Frame(child=self.waveform))

        self.action_button = Gtk.Button(label="🎤 Gravar")
        self.action_button.add_css_class("suggested-action")
        self.action_button.add_css_class("pill")
        self.action_button.set_size_request(-1, 50)
        self.action_button.connect("clicked", self._on_action_clicked)
        self.action_button.set_sensitive(False)
        page.append(self.action_button)

        self.result_label = Gtk.Label(label="")
        self.result_label.set_wrap(True)
        self.result_label.set_selectable(True)
        page.append(self.result_label)

    def _build_page_history(self) -> None:
        page = self._new_page_box("history")
        hint = Gtk.Label(
            label="Toque numa entrada para ver o texto e copiar para a área de transferência."
        )
        hint.set_xalign(0)
        hint.set_wrap(True)
        hint.add_css_class("dim-label")
        page.append(hint)

        self.saved_list_box = Gtk.ListBox()
        self.saved_list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.saved_list_box.add_css_class("saved-transcription-list")
        self.saved_list_box.connect("row-activated", self._on_saved_row_activated)

        self.saved_list_scroll = Gtk.ScrolledWindow()
        self.saved_list_scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        self.saved_list_scroll.set_min_content_height(200)
        self.saved_list_scroll.set_vexpand(True)
        self.saved_list_scroll.set_child(self.saved_list_box)

        history_frame = Gtk.Frame()
        history_frame.add_css_class("saved-history-frame")
        history_frame.set_child(self.saved_list_scroll)
        page.append(history_frame)

        self.saved_empty_label = Gtk.Label(label="Nenhuma transcrição salva ainda")
        self.saved_empty_label.add_css_class("dim-label")
        page.append(self.saved_empty_label)

        self.history_preview_label = Gtk.Label(label="")
        self.history_preview_label.set_wrap(True)
        self.history_preview_label.set_selectable(True)
        self.history_preview_label.set_xalign(0)
        page.append(self.history_preview_label)

    def _build_page_video(self) -> None:
        page = self._new_page_box("video")
        intro = Gtk.Label(
            label="Transcreve um vídeo local, separa por tópicos (pausas) e grava na pasta configurada."
        )
        intro.set_xalign(0)
        intro.set_wrap(True)
        intro.add_css_class("dim-label")
        page.append(intro)

        video_pick_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.video_path_entry = Gtk.Entry()
        self.video_path_entry.set_placeholder_text("Clique para escolher o arquivo de vídeo…")
        self.video_path_entry.set_editable(False)
        self.video_path_entry.set_hexpand(True)
        video_entry_click = Gtk.GestureClick()
        video_entry_click.connect("released", self._on_video_path_entry_clicked)
        self.video_path_entry.add_controller(video_entry_click)
        video_pick_row.append(self.video_path_entry)

        self.video_browse_button = Gtk.Button(label="📁 Escolher vídeo")
        self.video_browse_button.connect("clicked", self._on_video_browse_clicked)
        video_pick_row.append(self.video_browse_button)
        page.append(video_pick_row)

        self.video_topics_button = Gtk.Button(label="▶ Gerar tópicos")
        self.video_topics_button.add_css_class("pill")
        self.video_topics_button.add_css_class("suggested-action")
        self.video_topics_button.connect("clicked", self._on_video_topics_clicked)
        self.video_topics_button.set_sensitive(False)
        page.append(self.video_topics_button)

        self.video_result_label = Gtk.Label(label="")
        self.video_result_label.set_xalign(0)
        self.video_result_label.set_wrap(True)
        page.append(self.video_result_label)

    def _build_page_youtube(self) -> None:
        page = self._new_page_box("youtube")
        intro = Gtk.Label(
            label="Cola um link do YouTube. O download usa yt-dlp; é necessário ffmpeg no sistema."
        )
        intro.set_xalign(0)
        intro.set_wrap(True)
        intro.add_css_class("dim-label")
        page.append(intro)

        self.youtube_url_entry = Gtk.Entry()
        self.youtube_url_entry.set_placeholder_text("https://www.youtube.com/watch?v=…")
        self.youtube_url_entry.set_sensitive(False)
        self.youtube_url_entry.connect("activate", self._on_youtube_analyze_clicked)
        page.append(self.youtube_url_entry)

        self.youtube_analyze_button = Gtk.Button(label="▶ Analisar YouTube")
        self.youtube_analyze_button.add_css_class("pill")
        self.youtube_analyze_button.add_css_class("suggested-action")
        self.youtube_analyze_button.connect("clicked", self._on_youtube_analyze_clicked)
        self.youtube_analyze_button.set_sensitive(False)
        page.append(self.youtube_analyze_button)

        self.youtube_result_label = Gtk.Label(label="")
        self.youtube_result_label.set_xalign(0)
        self.youtube_result_label.set_wrap(True)
        page.append(self.youtube_result_label)

    def _build_page_settings(self) -> None:
        page = self._new_page_box("settings")
        s = self._settings

        hotkey_examples = ", ".join(format_hotkey_examples())
        self.prefs_hotkey_entry = Gtk.Entry()
        self.prefs_hotkey_entry.set_text(s.hotkey)
        page.append(self._labeled_field("Atalho global", self.prefs_hotkey_entry))
        page.append(
            self._dim_label(f"Ex.: {hotkey_examples}. Modo daemon: listen --daemon")
        )

        save_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.prefs_save_dir_entry = Gtk.Entry()
        self.prefs_save_dir_entry.set_text(s.save_directory)
        self.prefs_save_dir_entry.set_hexpand(True)
        save_row.append(self.prefs_save_dir_entry)
        browse_btn = Gtk.Button(label="Pasta…")
        browse_btn.connect("clicked", self._on_prefs_browse_save_dir)
        save_row.append(browse_btn)
        page.append(self._labeled_field("Pasta para salvar .txt", save_row))

        self.prefs_corner_switch = Gtk.Switch(active=s.corner_mode)
        page.append(self._switch_field("Janela no canto inferior direito", self.prefs_corner_switch))
        self.prefs_auto_record_switch = Gtk.Switch(active=s.auto_record)
        page.append(self._switch_field("Gravar ao abrir", self.prefs_auto_record_switch))
        self.prefs_save_transcriptions_switch = Gtk.Switch(active=s.save_transcriptions)
        page.append(
            self._switch_field("Salvar transcrições", self.prefs_save_transcriptions_switch)
        )
        self.prefs_auto_copy_switch = Gtk.Switch(active=s.auto_copy)
        page.append(self._switch_field("Copiar automaticamente", self.prefs_auto_copy_switch))

        self.prefs_whisper_force_entry = Gtk.Entry()
        force = s.resolved_whisper_force_language()
        if force:
            self.prefs_whisper_force_entry.set_text(force)
        self.prefs_whisper_force_entry.set_placeholder_text("vazio = detectar na gravação")
        page.append(
            self._labeled_field(
                "Forçar idioma Whisper (só vídeo/YouTube)",
                self.prefs_whisper_force_entry,
            )
        )

        self.prefs_translation_target_entry = Gtk.Entry()
        if s.translation_target:
            self.prefs_translation_target_entry.set_text(s.translation_target)
        self.prefs_translation_target_entry.set_placeholder_text("pt-br (vazio = sem tradução)")
        page.append(
            self._labeled_field(
                "Traduzir para (abaixo do original)",
                self.prefs_translation_target_entry,
            )
        )

        ai_sep = Gtk.Separator(margin_top=8, margin_bottom=4)
        page.append(ai_sep)
        ai_title = Gtk.Label(label="Índice com IA")
        ai_title.set_xalign(0)
        ai_title.add_css_class("title-4")
        page.append(ai_title)

        self.prefs_meeting_ai_switch = Gtk.Switch(active=s.meeting_ai_summary)
        page.append(
            self._switch_field(
                "A IA gera o indice.txt (resumo + índice detalhado)",
                self.prefs_meeting_ai_switch,
            )
        )

        self.prefs_cursor_mode_switch = Gtk.Switch(active=s.cursor_mode)
        self.prefs_cursor_mode_switch.connect(
            "notify::active", self._on_cursor_mode_prefs_toggled
        )
        page.append(
            self._switch_field(
                "Modo Cursor (Cursor Agent local em vez de OpenRouter)",
                self.prefs_cursor_mode_switch,
            )
        )

        self.prefs_cursor_model_entry = Gtk.Entry()
        if s.cursor_model:
            self.prefs_cursor_model_entry.set_text(s.cursor_model)
        self.prefs_cursor_model_entry.set_placeholder_text("predefinido do CLI")
        page.append(
            self._labeled_field("Modelo Cursor (opcional)", self.prefs_cursor_model_entry)
        )

        page.append(
            self._dim_label(
                "Requer o comando cursor no PATH (cursor agent login). "
                "Gera o índice na máquina, sem chave OpenRouter."
            )
        )

        test_cursor_btn = Gtk.Button(label="Testar Cursor")
        test_cursor_btn.connect("clicked", self._on_test_cursor_clicked)
        page.append(test_cursor_btn)

        or_sep = Gtk.Separator(margin_top=8, margin_bottom=4)
        page.append(or_sep)
        or_title = Gtk.Label(label="OpenRouter (nuvem)")
        or_title.set_xalign(0)
        or_title.add_css_class("title-4")
        page.append(or_title)

        self.prefs_openrouter_key_entry = Gtk.Entry()
        self.prefs_openrouter_key_entry.set_visibility(False)
        self.prefs_openrouter_key_entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        if s.openrouter_api_key:
            self.prefs_openrouter_key_entry.set_text(s.openrouter_api_key)
        self.prefs_openrouter_key_entry.set_placeholder_text("sk-or-v1-...")
        page.append(self._labeled_field("Chave API OpenRouter", self.prefs_openrouter_key_entry))

        self.prefs_openrouter_model_entry = Gtk.Entry()
        self.prefs_openrouter_model_entry.set_text(s.resolved_ai_model())
        self.prefs_openrouter_model_entry.set_placeholder_text("openai/gpt-4o-mini")
        page.append(
            self._labeled_field("Modelo (OpenRouter)", self.prefs_openrouter_model_entry)
        )

        self.prefs_ai_base_url_entry = Gtk.Entry()
        self.prefs_ai_base_url_entry.set_text(s.resolved_ai_base_url())
        page.append(
            self._labeled_field("URL da API (avançado)", self.prefs_ai_base_url_entry)
        )

        page.append(
            self._dim_label(
                "Requer pacote openai (incluído em pip install -e .). "
                "Chave: Configurações ou LISTEN_OPENROUTER_API_KEY."
            )
        )

        test_ai_btn = Gtk.Button(label="Testar OpenRouter")
        test_ai_btn.connect("clicked", self._on_test_openrouter_clicked)
        page.append(test_ai_btn)

        self._openrouter_prefs_widgets = (
            self.prefs_openrouter_key_entry,
            self.prefs_openrouter_model_entry,
            self.prefs_ai_base_url_entry,
            test_ai_btn,
        )
        self._update_openrouter_prefs_sensitivity()

        save_btn = Gtk.Button(label="Guardar configurações")
        save_btn.add_css_class("suggested-action")
        save_btn.add_css_class("pill")
        save_btn.connect("clicked", self._on_save_settings_clicked)
        page.append(save_btn)

    def _on_prefs_browse_save_dir(self, _button) -> None:
        chooser = Gtk.FileChooserNative(
            title="Escolher pasta de salvamento",
            transient_for=self.window,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
            accept_label="_Selecionar",
            cancel_label="_Cancelar",
        )
        current = self.prefs_save_dir_entry.get_text().strip()
        if current:
            path = Path(current).expanduser()
            if path.is_dir():
                chooser.set_file(Gio.File.new_for_path(str(path)))
        chooser.connect("response", self._on_prefs_save_dir_response)
        self._present_native_file_chooser(chooser)

    def _on_prefs_save_dir_response(
        self, native: Gtk.FileChooserNative, response_id: int
    ) -> None:
        try:
            if response_id == Gtk.ResponseType.ACCEPT:
                gfile = native.get_file()
                if gfile is not None:
                    path = gfile.get_path()
                    if path:
                        self.prefs_save_dir_entry.set_text(path)
        finally:
            native.destroy()
            self._release_native_file_chooser()

    def _on_cursor_mode_prefs_toggled(self, switch, _pspec) -> None:
        self._update_openrouter_prefs_sensitivity()
        if switch.get_active() and hasattr(self, "prefs_meeting_ai_switch"):
            self.prefs_meeting_ai_switch.set_active(True)
        self._settings.cursor_mode = switch.get_active()
        try:
            self._settings.save()
        except OSError:
            pass

    def _update_openrouter_prefs_sensitivity(self) -> None:
        if not hasattr(self, "prefs_cursor_mode_switch"):
            return
        use_cursor = self.prefs_cursor_mode_switch.get_active()
        widgets = getattr(self, "_openrouter_prefs_widgets", ())
        for widget in widgets:
            widget.set_sensitive(not use_cursor)

    def _on_test_cursor_clicked(self, _button) -> None:
        from .meeting_summary_cursor import test_cursor_connection

        self._sync_openrouter_prefs_to_settings()
        opts = self._meeting_ai_options()
        opts.enabled = True
        opts.provider = "cursor"
        ok, msg = test_cursor_connection(opts)
        if ok:
            self.status_label.set_text(f"✓ {msg}")
        else:
            self.status_label.set_text(f"✗ {msg}")

    def _on_test_openrouter_clicked(self, _button) -> None:
        from .meeting_summary_ai import test_openrouter_connection

        self._sync_openrouter_prefs_to_settings()
        opts = self._meeting_ai_options()
        opts.enabled = True
        opts.provider = "openrouter"
        ok, msg = test_openrouter_connection(opts)
        if ok:
            self.status_label.set_text(f"✓ {msg}")
        else:
            self.status_label.set_text(f"✗ {msg}")

    def _on_save_settings_clicked(self, _button) -> None:
        hotkey = self.prefs_hotkey_entry.get_text().strip()
        if not hotkey:
            self._show_error_dialog("Atalho obrigatório", "Indique um atalho global.")
            return
        try:
            HotkeyBinding.parse(hotkey)
        except ValueError as exc:
            self._show_error_dialog("Atalho inválido", str(exc))
            return

        save_dir = self.prefs_save_dir_entry.get_text().strip()
        if not save_dir:
            self._show_error_dialog(
                "Pasta obrigatória", "Escolha onde guardar transcrições e tópicos."
            )
            return

        whisper_force = self.prefs_whisper_force_entry.get_text().strip() or None
        self._settings.hotkey = hotkey
        self._settings.save_directory = save_dir
        self._settings.corner_mode = self.prefs_corner_switch.get_active()
        self._settings.auto_record = self.prefs_auto_record_switch.get_active()
        self._settings.save_transcriptions = self.prefs_save_transcriptions_switch.get_active()
        self._settings.auto_copy = self.prefs_auto_copy_switch.get_active()
        self._settings.language = None
        self._settings.whisper_force_language = whisper_force
        trans_tgt = self.prefs_translation_target_entry.get_text().strip()
        self._settings.translation_target = trans_tgt or None
        self._settings.meeting_ai_summary = self.prefs_meeting_ai_switch.get_active()
        self._settings.cursor_mode = self.prefs_cursor_mode_switch.get_active()
        cursor_model = self.prefs_cursor_model_entry.get_text().strip()
        self._settings.cursor_model = cursor_model or None
        raw_key = self.prefs_openrouter_key_entry.get_text().strip()
        key = self._read_openrouter_key_from_prefs()
        if raw_key and not key:
            self._show_error_dialog(
                "Chave invalida",
                "A chave OpenRouter parece corrompida ou incompleta.\n"
                "Cole apenas a chave (ex.: sk-or-v1-...) sem outros textos.",
            )
            return
        self._settings.openrouter_api_key = key
        self._settings.openrouter_model = (
            self.prefs_openrouter_model_entry.get_text().strip()
            or "openai/gpt-4o-mini"
        )
        base_url = self.prefs_ai_base_url_entry.get_text().strip()
        self._settings.openai_base_url = base_url or "https://openrouter.ai/api/v1"

        config_path = self._settings.save()
        self._apply_settings(self._settings, config_path)

    def _show_error_dialog(self, heading: str, body: str) -> None:
        err = Adw.MessageDialog(transient_for=self.window, heading=heading, body=body)
        err.add_response("ok", "OK")
        err.set_default_response("ok")
        err.set_close_response("ok")
        err.present()

    def _apply_settings(self, settings: ListenSettings, config_path: Path) -> None:
        self._settings = settings
        self.save_directory = settings.resolved_save_directory()
        self.save_transcriptions = settings.save_transcriptions
        self.auto_copy = settings.auto_copy
        self.corner_mode = settings.corner_mode

        if self.global_hotkey is not None and settings.hotkey != self.global_hotkey:
            self.global_hotkey = settings.hotkey
            if self._hotkey_listener is not None:
                self._hotkey_listener.stop()
            self._hotkey_listener = GlobalHotkeyListener(
                settings.hotkey,
                on_activate=self._on_global_hotkey,
            )
            self._hotkey_listener.start()

        if self.corner_mode:
            GLib.idle_add(self._position_bottom_right)

        self._refresh_saved_list()
        self.status_label.set_text(f"✓ Configuração guardada em {config_path}")

    def _clipboard_set_text(self, text: str) -> None:
        """Copy UTF-8 text to the clipboard (main thread only)."""
        display = Gdk.Display.get_default()
        if display is None:
            copy_plain_text(text)
            return
        try:
            clipboard = display.get_clipboard()
            gbytes = GLib.Bytes.new(text.encode("utf-8"))
            provider = Gdk.ContentProvider.new_for_bytes(
                "text/plain;charset=utf-8", gbytes
            )
            clipboard.set_content(provider)
        except Exception:
            copy_plain_text(text)

    def _schedule_transcription_ui(
        self,
        text: str,
        language: str = "",
        *,
        audio_bytes: int = 0,
        original_text: str | None = None,
        translation: str | None = None,
        translation_language: str | None = None,
    ) -> None:
        """Queue transcription for the main thread; avoids GObject UTF-8/ASCII bugs."""
        with self._transcription_delivery_lock:
            self._pending_transcription = (
                text,
                language,
                audio_bytes,
                original_text,
                translation,
                translation_language,
            )
        GLib.idle_add(self._deliver_pending_transcription)

    def _deliver_pending_transcription(self, *_args) -> bool:
        with self._transcription_delivery_lock:
            pending = self._pending_transcription
            self._pending_transcription = None
        if pending is not None:
            self._on_transcription_complete(
                pending[0],
                pending[1],
                pending[2],
                original_text=pending[3],
                translation=pending[4],
                translation_language=pending[5],
            )
        return False

    def _on_window_realize_corner(self, _window):
        self._position_bottom_right()

    def _get_active_monitor_geometry(self) -> tuple[int, int, int, int]:
        display = Gdk.Display.get_default()
        if display is None:
            return 0, 0, 1920, 1080

        monitor = None

        if getattr(self, "window", None):
            surface = self.window.get_surface()
            if surface is not None:
                monitor = display.get_monitor_at_surface(surface)

        if monitor is None:
            seat = display.get_default_seat()
            if seat is not None:
                pointer = seat.get_pointer()
                if pointer is not None and hasattr(pointer, "get_position"):
                    try:
                        _, px, py, _ = pointer.get_position()
                        monitor = display.get_monitor_at_point(px, py)
                    except (AttributeError, TypeError, ValueError):
                        pass

        if monitor is None:
            monitor = display.get_primary_monitor()

        if monitor is None:
            return 0, 0, 1920, 1080

        geometry = monitor.get_geometry()
        return geometry.x, geometry.y, geometry.width, geometry.height

    def _position_bottom_right(self, *_args) -> bool:
        if not self.corner_mode:
            return False

        try:
            origin_x, origin_y, monitor_width, monitor_height = (
                self._get_active_monitor_geometry()
            )
            width = self.window_width
            height = self.window_height
            margin = self.screen_margin

            x = origin_x + monitor_width - width - margin
            y = origin_y + monitor_height - height - margin

            surface = self.window.get_surface()
            if surface is not None:
                surface.move_to_rect(
                    Gdk.Rectangle.new(int(x), int(y), 1, 1),
                    Gdk.SubpixelLayout.UNKNOWN,
                )
        except Exception:
            pass

        return False

    def _on_global_hotkey(self) -> None:
        GLib.idle_add(self._handle_global_hotkey_on_main_thread)

    def _handle_global_hotkey_on_main_thread(self, *_args) -> bool:
        if not getattr(self, "window", None):
            return False

        if not self.window.is_visible():
            self.window.present()
            if self.corner_mode:
                self._position_bottom_right()

        self._show_page("record")

        if self._state == self.STATE_READY and self._model_ready:
            self._start_recording()
        elif self._state == self.STATE_RECORDING:
            self._stop_and_transcribe()
        elif self._state == self.STATE_TRANSCRIBING:
            self.status_label.set_text("Transcrevendo... aguarde a conclusão")
        elif self._state == self.STATE_RESULT and self._model_ready:
            self._copy_and_reset()
            self._start_recording()
        elif not self._model_ready:
            self._pending_auto_record = True
            self.status_label.set_text("Carregando modelo... gravará ao terminar")

        return False

    def _maybe_auto_start_recording(self) -> None:
        if (
            self.auto_start_recording
            or self._pending_auto_record
        ) and self._model_ready and self._state == self.STATE_READY:
            self._pending_auto_record = False
            self._start_recording()

    def _apply_css(self):
        """Apply custom styling."""
        css = b"""
        .recording-button {
            background: linear-gradient(to bottom, #e53935, #c62828);
            color: white;
        }
        .device-info-frame {
            background: alpha(@card_bg_color, 0.5);
            border-radius: 8px;
        }
        .device-info-label {
            font-size: 11px;
            font-family: monospace;
        }
        .gpu-active {
            color: #76b900;
        }
        .cpu-active {
            color: #0071c5;
        }
        .saved-history-frame {
            background: alpha(@card_bg_color, 0.35);
            border-radius: 8px;
        }
        .saved-transcription-list row {
            padding: 6px 10px;
        }
        .saved-transcription-date {
            font-size: 11px;
            opacity: 0.75;
        }
        .saved-transcription-preview {
            font-size: 13px;
        }
        .navigation-sidebar {
            background: alpha(@sidebar_bg_color, 1);
            border-right: 1px solid alpha(@borders, 0.4);
        }
        .navigation-sidebar row:selected {
            background: alpha(@accent_bg_color, 0.35);
            border-radius: 8px;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _load_model(self, new_model_size: Optional[ModelSize] = None):
        """Load the transcription model in background."""
        try:
            # Use new_model_size if provided, otherwise use instance default
            model_to_load = new_model_size if new_model_size else self.model_size
            self._transcriber = Transcriber(model_size=model_to_load)
            self.model_size = model_to_load  # Update instance variable
            info = self._transcriber.get_model_info()

            # Format device info for display
            device_text = self._format_device_info(info)
            # Do not pass non-ASCII str as GLib.idle_add user_data (GObject marshals as ASCII on some setups).
            GLib.idle_add(
                lambda t=device_text, dev=info["device"]: self._update_device_info(t, dev)
                or False
            )

            GLib.idle_add(
                lambda m=f"Ready • {info['model_size'].upper()} model": self._update_status(
                    m
                )
                or False
            )
            GLib.idle_add(self.action_button.set_sensitive, True)
            GLib.idle_add(self.model_dropdown.set_sensitive, True)
            GLib.idle_add(self._set_model_ready)
            GLib.idle_add(self._update_video_topics_button)
        except Exception as e:
            GLib.idle_add(lambda err=e: self._update_status(f"Error: {err}") or False)
            GLib.idle_add(
                lambda: self._update_device_info(
                    "<span color='#e53935'>⚠ Error loading model</span>", "error"
                )
                or False
            )
            GLib.idle_add(self.action_button.set_sensitive, True)
            GLib.idle_add(self.model_dropdown.set_sensitive, True)
            GLib.idle_add(self._update_video_topics_button)

    def _set_model_ready(self, *_args) -> bool:
        self._model_ready = True
        self._maybe_auto_start_recording()
        return False

    def _on_model_changed(self, dropdown, _pspec):
        """Handle model dropdown selection change."""
        if self._state != self.STATE_READY:
            # Don't change model while recording or processing
            return

        model_options = ["tiny", "base", "small", "medium", "large-v3"]
        selected_idx = dropdown.get_selected()
        new_model = model_options[selected_idx]

        if new_model == self.model_size:
            return  # No change

        # Disable controls while loading
        self.action_button.set_sensitive(False)
        self.model_dropdown.set_sensitive(False)
        self.video_topics_button.set_sensitive(False)
        self.video_browse_button.set_sensitive(False)
        self.video_path_entry.set_sensitive(False)
        self.youtube_analyze_button.set_sensitive(False)
        self.youtube_url_entry.set_sensitive(False)
        self.status_label.set_text(f"Loading {new_model.upper()} model...")
        self.device_info_label.set_markup("⏳ Switching model...")

        # Load new model in background
        threading.Thread(
            target=self._load_model, args=(new_model,), daemon=True
        ).start()

    def _format_device_info(self, info: dict) -> str:
        """Format device info for display."""
        if info["device"] == "cuda" and info.get("gpu_name"):
            # GPU mode - show detailed info
            gpu_name = info["gpu_name"]
            memory = info.get("gpu_memory_mb", 0)
            cuda_ver = info.get("cuda_version", "N/A")
            compute = info["compute_type"]
            model = info["model_size"].upper()

            return (
                f"<span color='#76b900'>🟢 GPU</span>  <b>{gpu_name}</b>\n"
                f"    Memory: {memory} MB │ CUDA: {cuda_ver}\n"
                f"    Model: {model} │ Precision: {compute}"
            )
        else:
            # CPU mode
            compute = info["compute_type"]
            model = info["model_size"].upper()

            return (
                f"<span color='#0071c5'>🔵 CPU</span>  Inference Mode\n"
                f"    Model: {model} │ Precision: {compute}\n"
                f"    <span color='#888'>Tip: Install CUDA for faster processing</span>"
            )

    def _update_device_info(self, text: str, device: str):
        """Update device info display (thread-safe)."""
        self.device_info_label.set_markup(text)
        # Update CSS class based on device
        self.device_info_label.remove_css_class("gpu-active")
        self.device_info_label.remove_css_class("cpu-active")
        if device == "cuda":
            self.device_info_label.add_css_class("gpu-active")

    def _update_status(self, text: str):
        """Update status label (thread-safe)."""
        self.status_label.set_text(text)

    def _resolved_save_directory(self) -> Optional[Path]:
        if self.save_directory is None:
            return None
        directory = Path(self.save_directory).expanduser().resolve()
        if directory.is_dir():
            return directory

        fallback = ListenSettings.load().resolved_save_directory()
        if fallback.is_dir():
            return fallback

        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _update_video_topics_button(self) -> None:
        idle_ok = self._state in (self.STATE_READY, self.STATE_RESULT)
        can_browse = not self._video_processing and idle_ok
        can_process = (
            can_browse
            and self._model_ready
            and self._transcriber is not None
        )
        has_video = self._selected_video_path is not None
        self.video_browse_button.set_sensitive(can_browse)
        self.video_path_entry.set_sensitive(can_browse)
        self.video_topics_button.set_sensitive(can_process and has_video)
        self.youtube_analyze_button.set_sensitive(can_process)
        self.youtube_url_entry.set_sensitive(can_process)

    def _release_native_file_chooser(self) -> None:
        self._native_file_chooser = None

    def _present_native_file_chooser(
        self, chooser: Gtk.FileChooserNative
    ) -> None:
        """Mantém referência viva até fechar (GTK4 deixa de abrir se o objeto for coletado)."""
        self._native_file_chooser = chooser
        chooser.show()

    def _refresh_saved_list(self) -> None:
        directory = self._resolved_save_directory()
        if directory is None:
            return

        def worker() -> None:
            items = list_saved_transcriptions(directory)
            with self._saved_list_lock:
                self._pending_saved_items = items
            GLib.idle_add(self._deliver_pending_saved_list)

        threading.Thread(target=worker, daemon=True).start()

    def _deliver_pending_saved_list(self, *_args) -> bool:
        with self._saved_list_lock:
            items = self._pending_saved_items
            self._pending_saved_items = None
        if items is not None:
            self._populate_saved_list(items)
        return False

    def _populate_saved_list(self, items: list[SavedTranscription]) -> None:
        self._saved_items = items

        while child := self.saved_list_box.get_first_child():
            self.saved_list_box.remove(child)

        for item in items:
            self.saved_list_box.append(self._build_saved_row(item))

        has_items = bool(items)
        self.saved_list_scroll.set_visible(has_items)
        self.saved_empty_label.set_visible(not has_items)

    def _build_saved_row(self, item: SavedTranscription) -> Gtk.ListBoxRow:
        row_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        date_label = Gtk.Label(
            label=item.created_at.strftime("%d/%m/%Y %H:%M")
        )
        date_label.set_xalign(0)
        date_label.add_css_class("saved-transcription-date")
        row_box.append(date_label)

        preview_label = Gtk.Label(label=item.preview)
        preview_label.set_xalign(0)
        preview_label.set_wrap(True)
        preview_label.set_max_width_chars(48)
        preview_label.add_css_class("saved-transcription-preview")
        row_box.append(preview_label)

        if item.language:
            lang_label = Gtk.Label(label=item.language.upper())
            lang_label.set_xalign(0)
            lang_label.add_css_class("dim-label")
            row_box.append(lang_label)

        row = Gtk.ListBoxRow()
        row.set_child(row_box)
        row.saved_item = item  # type: ignore[attr-defined]
        return row

    def _on_saved_row_activated(self, _list_box, row: Gtk.ListBoxRow) -> None:
        item: Optional[SavedTranscription] = getattr(row, "saved_item", None)
        if item is None:
            return

        try:
            text = read_transcription_text(item.path)
        except OSError as exc:
            self.status_label.set_text(f"Erro ao abrir arquivo: {exc}")
            return

        preview = f'"{text}"' if len(text) < 500 else f'"{text[:500]}…"'
        self.history_preview_label.set_text(preview)
        if self.auto_copy and text:
            self._clipboard_set_text(text)
            self.status_label.set_text(
                f"✓ Copiado • {item.created_at.strftime('%d/%m/%Y %H:%M')}"
            )
        else:
            self.status_label.set_text(
                f"Transcrição de {item.created_at.strftime('%d/%m/%Y %H:%M')}"
            )

    def _on_meeting_mode_toggled(self, switch, _pspec) -> None:
        if self._state != self.STATE_READY:
            switch.set_active(self.meeting_mode)
            return

        self.meeting_mode = switch.get_active()
        self._recorder.meeting_mode = self.meeting_mode
        self.meeting_hint_label.set_visible(self.meeting_mode)

        self._settings.meeting_mode = self.meeting_mode
        self._settings.save()

    def _read_openrouter_key_from_prefs(self) -> str | None:
        from .meeting_summary_ai import sanitize_api_key

        if not hasattr(self, "prefs_openrouter_key_entry"):
            return self._settings.openrouter_api_key
        return sanitize_api_key(self.prefs_openrouter_key_entry.get_text())

    def _sync_openrouter_prefs_to_settings(self) -> None:
        """Lê widgets GTK na thread principal e grava em _settings (antes de worker)."""
        if not hasattr(self, "prefs_meeting_ai_switch"):
            return
        self._settings.meeting_ai_summary = self.prefs_meeting_ai_switch.get_active()
        self._settings.cursor_mode = self.prefs_cursor_mode_switch.get_active()
        cursor_model = self.prefs_cursor_model_entry.get_text().strip()
        self._settings.cursor_model = cursor_model or None
        self._settings.openrouter_api_key = self._read_openrouter_key_from_prefs()
        model = self.prefs_openrouter_model_entry.get_text().strip()
        if model:
            self._settings.openrouter_model = model
        base_url = self.prefs_ai_base_url_entry.get_text().strip()
        if base_url:
            self._settings.openai_base_url = base_url

    def _describe_ai_backend(self, opts: MeetingAISummaryOptions) -> str:
        if not opts.enabled:
            return ""
        if (opts.provider or "").strip().lower() == "cursor":
            model = (opts.cursor_model or "").strip() or "predefinido"
            return f"Cursor Agent ({model})"
        return f"OpenRouter ({opts.model})"

    def _meeting_ai_options(self) -> MeetingAISummaryOptions:
        """Opções só a partir de _settings — chamar após _sync_openrouter_prefs_to_settings."""
        s = self._settings
        return MeetingAISummaryOptions(
            enabled=s.resolved_meeting_ai_enabled(),
            provider=s.resolved_ai_provider(),
            model=s.resolved_ai_model(),
            api_key=s.openrouter_api_key,
            base_url=s.resolved_ai_base_url(),
            cursor_cli=s.cursor_cli or "cursor",
            cursor_model=s.cursor_model,
        )

    def _ensure_ai_ready_for_media(self) -> bool:
        """Bloqueia processamento se IA activa mas dependências em falta."""
        self._sync_openrouter_prefs_to_settings()
        opts = self._meeting_ai_options()
        if not opts.enabled:
            return True
        if opts.provider == "cursor":
            from .meeting_summary_cursor import cursor_cli_available

            if cursor_cli_available(opts.cursor_cli):
                return True
            self._show_error_dialog(
                "Cursor CLI em falta",
                f"O comando '{opts.cursor_cli}' não está no PATH.\n\n"
                "Instale o Cursor CLI e faça login:\n"
                "  cursor agent login",
            )
            return False
        from .meeting_summary_ai import ai_extra_install_hint, openai_sdk_available

        if openai_sdk_available():
            return True
        self._show_error_dialog(
            "Pacote openai em falta",
            "A IA (OpenRouter) está activa mas este ambiente Python não tem o pacote openai.\n\n"
            + ai_extra_install_hint(),
        )
        return False

    def _set_meeting_switch_sensitive(self, enabled: bool) -> None:
        if is_meeting_capture_available() and self._state == self.STATE_READY:
            self.meeting_switch.set_sensitive(enabled)
        elif not enabled:
            self.meeting_switch.set_sensitive(False)

    def _on_recording_status(self, status: str):
        """Handle recording status changes from AudioRecorder."""
        pass  # Status updates handled in button callback

    def _on_action_clicked(self, button):
        """Handle main action button click based on current state."""
        if self._state == self.STATE_READY:
            self._start_recording()
        elif self._state == self.STATE_RECORDING:
            self._stop_and_transcribe()
        elif self._state == self.STATE_RESULT:
            self._copy_and_reset()

    def _start_recording(self):
        """Start recording audio."""
        self._state = self.STATE_RECORDING
        self._last_transcription = ""

        self.action_button.set_label("⏹️ Transcribe")
        self.action_button.remove_css_class("suggested-action")
        self.action_button.add_css_class("destructive-action")
        if self.meeting_mode:
            self.status_label.set_text(
                "Gravando reunião (mic + sistema)... Clique para transcrever"
            )
        elif self.global_hotkey:
            self.status_label.set_text(
                "Gravando... pressione o atalho de novo para parar e salvar"
            )
        else:
            self.status_label.set_text("Recording... Click to transcribe")
        self.result_label.set_text("")
        self.waveform.clear()

        # Start recording with callback for waveform
        self._recorder._on_audio_chunk = self._on_audio_chunk
        try:
            self._recorder.start()
        except RuntimeError as exc:
            self._state = self.STATE_READY
            self.action_button.set_label("🎤 Record")
            self.action_button.remove_css_class("destructive-action")
            self.action_button.add_css_class("suggested-action")
            self.status_label.set_text(str(exc))
            return

        self._set_meeting_switch_sensitive(False)
        self._update_video_topics_button()

    def _on_audio_chunk(self, data: bytes):
        """Handle incoming audio chunk for waveform."""
        GLib.idle_add(lambda d=data: self.waveform.add_samples(d) or False)

    def _stop_and_transcribe(self):
        """Stop recording and transcribe."""
        self._state = self.STATE_TRANSCRIBING

        self.action_button.set_label("⏳ Transcribing...")
        self.action_button.remove_css_class("destructive-action")
        self.action_button.set_sensitive(False)
        self._set_meeting_switch_sensitive(False)
        self._update_video_topics_button()
        self.status_label.set_text("Processing audio...")

        # Stop and transcribe in background
        threading.Thread(target=self._transcribe_audio, daemon=True).start()

    def _transcribe_audio(self):
        """Transcribe recorded audio (runs in background thread)."""
        audio_data = self._recorder.stop()
        audio_bytes = len(audio_data)

        if audio_bytes < 1000:
            self._schedule_transcription_ui("(no audio captured)", "", audio_bytes=0)
            return

        if self._transcriber is None:
            self._schedule_transcription_ui(
                "Error: modelo de transcrição não carregou", "", audio_bytes=audio_bytes
            )
            return

        try:
            from .transcription_capture import transcribe_capture

            result = transcribe_capture(
                self._transcriber,
                audio_data,
                whisper_language=None,
                translation_target=self._settings.resolved_translation_target(),
            )
            language = result.language or ""
            if not result.text.strip():
                display_text = "[sem fala detectada]"
            else:
                display_text = result.display_text()
            self._schedule_transcription_ui(
                display_text,
                language,
                audio_bytes=audio_bytes,
                original_text=result.text.strip(),
                translation=result.translation,
                translation_language=result.translation_language,
            )
        except Exception as e:
            self._schedule_transcription_ui(
                f"Error: {e}", "", audio_bytes=audio_bytes
            )

    def _on_transcription_complete(
        self,
        text: str,
        language: str = "",
        audio_bytes: int = 0,
        *,
        original_text: str | None = None,
        translation: str | None = None,
        translation_language: str | None = None,
    ):
        """Handle transcription completion (runs on main thread)."""
        is_error = text.startswith("Error:")
        is_no_audio = text == "(no audio captured)"
        had_audio = audio_bytes >= 1000
        body_to_save = (original_text if original_text is not None else text).strip()

        saved_path: Optional[Path] = None
        save_dir = self._resolved_save_directory()
        if (
            self.save_transcriptions
            and save_dir is not None
            and had_audio
            and body_to_save
            and not is_error
        ):
            try:
                saved_path = save_transcription_text(
                    body_to_save,
                    save_dir,
                    language=language,
                    translation=translation,
                    translation_language=translation_language,
                    meeting_mode=self.meeting_mode,
                )
            except OSError as exc:
                text = f"Error: não foi possível salvar o arquivo ({exc})"
                is_error = True

        self._last_saved_path = saved_path

        if saved_path is not None:
            self._refresh_saved_list()

        if (
            self.auto_copy
            and text
            and not is_error
            and not is_no_audio
            and not text.startswith("[sem fala detectada]")
        ):
            self._clipboard_set_text(text)

        if getattr(self, "window", None):
            self.window.present()
            if self.corner_mode:
                self._position_bottom_right()

        self._last_transcription = text
        self._last_language = language
        self._state = self.STATE_RESULT

        self.action_button.set_label("📋 Copy & New Recording")
        self.action_button.remove_css_class("destructive-action")
        self.action_button.add_css_class("suggested-action")
        self.action_button.set_sensitive(True)
        self._set_meeting_switch_sensitive(True)
        self._update_video_topics_button()

        # Language display mapping
        lang_names = {
            "ar": "Arabic",
            "en": "English",
            "fr": "French",
            "es": "Spanish",
            "de": "German",
            "zh": "Chinese",
            "ja": "Japanese",
            "ko": "Korean",
            "ru": "Russian",
            "pt": "Portuguese",
        }
        lang_display = lang_names.get(language, language.upper() if language else "")

        if text and not is_error and not is_no_audio:
            if text.startswith("[sem fala detectada]"):
                status_msg = "Áudio gravado, mas nenhuma fala foi reconhecida"
                if saved_path is not None:
                    status_msg += f" • salvo em {saved_path.name}"
            elif saved_path is not None:
                status_msg = f"✓ Salvo em {saved_path.name}"
                if self.auto_copy:
                    status_msg += " • copiado"
                if lang_display:
                    status_msg += f" • {lang_display} detectado"
            elif self.auto_copy:
                status_msg = "✓ Copiado para a área de transferência!"
                if lang_display:
                    status_msg += f" • {lang_display} detectado"
            else:
                status_msg = "✓ Transcrição concluída"
                if lang_display:
                    status_msg += f" • {lang_display} detectado"
            self.status_label.set_text(status_msg)
            self.result_label.set_text(f'"{text}"')
        else:
            if is_no_audio:
                self.status_label.set_text(
                    "Nenhum áudio capturado — verifique o microfone no sistema"
                )
            else:
                self.status_label.set_text("Ready • Click to start new recording")
            self.result_label.set_text(text)

    def _copy_and_reset(self):
        """Copy text to clipboard again and reset to ready state."""
        if self._last_transcription and not self._last_transcription.startswith(
            "Error:"
        ):
            self._clipboard_set_text(self._last_transcription)

        self._state = self.STATE_READY
        self._last_transcription = ""

        self.action_button.set_label("🎤 Record")
        self.status_label.set_text("Ready")
        self.result_label.set_text("")
        self.waveform.clear()
        self._set_meeting_switch_sensitive(True)
        self._update_video_topics_button()

    def _on_youtube_analyze_clicked(self, *_args) -> None:
        if self._transcriber is None or not self._model_ready:
            return
        if self._state not in (self.STATE_READY, self.STATE_RESULT):
            self.status_label.set_text(
                "Termine a gravação ou a transcrição antes de analisar o YouTube."
            )
            return
        url = self.youtube_url_entry.get_text().strip()
        if not url:
            self.status_label.set_text("Cole o link do vídeo no campo acima.")
            return
        if not yt_dlp_available():
            self.status_label.set_text(
                "yt-dlp não encontrado neste ambiente. No terminal: pip install -e ."
            )
            return
        if not ffmpeg_available():
            self.status_label.set_text(
                "ffmpeg não encontrado. Instale: sudo apt install ffmpeg"
            )
            return

        save_dir = self._resolved_save_directory()
        if save_dir is None:
            self.status_label.set_text(
                "Defina uma pasta de salvamento (menu Configurações)."
            )
            return

        try:
            normalized_url = ensure_youtube_download_url(url)
        except ValueError as exc:
            self.status_label.set_text(str(exc))
            return
        if not self._ensure_ai_ready_for_media():
            return

        self._media_job_kind = "youtube"
        self._video_processing = True
        self._update_video_topics_button()
        self.action_button.set_sensitive(False)
        self.status_label.set_text("Baixando e analisando YouTube…")
        self.youtube_result_label.set_text(
            "Isso pode levar vários minutos (download + transcrição + tópicos)."
        )
        self._show_page("youtube")

        language_hint = self._settings.resolved_whisper_force_language()
        self._sync_openrouter_prefs_to_settings()
        ai_opts = self._meeting_ai_options()
        if ai_opts.enabled:
            backend = self._describe_ai_backend(ai_opts)
            self.status_label.set_text(f"YouTube: transcrevendo… depois {backend}")
            self.youtube_result_label.set_text(
                f"A gerar indice.txt via {backend} após a transcrição."
            )

        def worker() -> None:
            try:
                transcribers = self._transcriber
                if transcribers is None:
                    raise RuntimeError("modelo não disponível")
                out = analyze_youtube_to_topics(
                    normalized_url,
                    transcribers,
                    save_dir,
                    language=language_hint,
                    gap_seconds=10.0,
                    ai_summary_options=ai_opts,
                )
                GLib.idle_add(self._on_meeting_video_complete, None, str(out))
            except Exception as exc:
                GLib.idle_add(self._on_meeting_video_complete, str(exc), "")

        threading.Thread(target=worker, daemon=True).start()

    def _on_video_path_entry_clicked(self, _gesture, _n_press, _x, _y) -> None:
        self._open_video_file_chooser()

    def _on_video_browse_clicked(self, _button) -> None:
        self._open_video_file_chooser()

    def _open_video_file_chooser(self) -> None:
        if self._state not in (self.STATE_READY, self.STATE_RESULT):
            self.status_label.set_text(
                "Termine a gravação ou a transcrição antes de escolher um vídeo."
            )
            return
        if self._native_file_chooser is not None:
            return

        if not getattr(self, "window", None) or not self.window.get_visible():
            self.window.present()

        chooser = Gtk.FileChooserNative(
            title="Escolher vídeo",
            transient_for=self.window,
            action=Gtk.FileChooserAction.OPEN,
            accept_label="_Abrir",
            cancel_label="_Cancelar",
            modal=True,
        )
        video_filter = Gtk.FileFilter()
        video_filter.set_name("Vídeo")
        for pattern in (
            "*.mp4",
            "*.mkv",
            "*.webm",
            "*.mov",
            "*.avi",
            "*.ogv",
            "*.mpeg",
            "*.mpg",
            "*.m4v",
            "*.wmv",
        ):
            video_filter.add_pattern(pattern)
        all_filter = Gtk.FileFilter()
        all_filter.set_name("Todos os arquivos")
        all_filter.add_pattern("*")
        chooser.add_filter(video_filter)
        chooser.add_filter(all_filter)
        if self._selected_video_path is not None and self._selected_video_path.is_file():
            chooser.set_file(
                Gio.File.new_for_path(str(self._selected_video_path))
            )
        chooser.connect("response", self._on_video_file_chooser_response)
        self._present_native_file_chooser(chooser)

    def _on_video_file_chooser_response(
        self, native: Gtk.FileChooserNative, response_id: int
    ) -> None:
        try:
            if response_id != Gtk.ResponseType.ACCEPT:
                return

            gfile = native.get_file()
            path_str = gfile.get_path() if gfile is not None else None
            if not path_str:
                return

            video_path = Path(path_str)
            if not video_path.is_file():
                self.status_label.set_text(f"Arquivo não encontrado: {video_path}")
                return

            self._selected_video_path = video_path
            self.video_path_entry.set_text(str(video_path))
            self._update_video_topics_button()
            self.status_label.set_text(f"Vídeo selecionado: {video_path.name}")
        finally:
            native.destroy()
            self._release_native_file_chooser()

    def _on_video_topics_clicked(self, _button) -> None:
        if self._transcriber is None or not self._model_ready:
            return
        if self._state not in (self.STATE_READY, self.STATE_RESULT):
            self.status_label.set_text(
                "Termine a gravação ou a transcrição antes de analisar um vídeo."
            )
            return
        if not ffmpeg_available():
            self.status_label.set_text(
                "ffmpeg não encontrado. Instale: sudo apt install ffmpeg"
            )
            return
        if self._selected_video_path is None:
            self._open_video_file_chooser()
            return
        if not self._ensure_ai_ready_for_media():
            return

        self._start_video_analysis(self._selected_video_path)

    def _start_video_analysis(self, video_path: Path) -> None:
        save_dir = self._resolved_save_directory()
        if save_dir is None:
            self.status_label.set_text(
                "Defina uma pasta de salvamento (menu Configurações)."
            )
            return

        self._media_job_kind = "video"
        self._video_processing = True
        self._update_video_topics_button()
        self.action_button.set_sensitive(False)
        self.status_label.set_text(f"Analisando vídeo: {video_path.name}…")
        self.video_result_label.set_text(
            "Extraindo áudio, transcrevendo e separando tópicos. Isso pode levar vários minutos."
        )
        self._show_page("video")

        language_hint = self._settings.resolved_whisper_force_language()
        self._sync_openrouter_prefs_to_settings()
        ai_opts = self._meeting_ai_options()
        if ai_opts.enabled:
            backend = self._describe_ai_backend(ai_opts)
            is_cursor = (ai_opts.provider or "").strip().lower() == "cursor"
            if not is_cursor and not ai_opts.api_key and not os.environ.get(
                "LISTEN_OPENROUTER_API_KEY"
            ):
                self.video_result_label.set_text(
                    "IA activa mas sem chave — configure em Configurações ou "
                    "LISTEN_OPENROUTER_API_KEY."
                )
            else:
                self.status_label.set_text(
                    f"Vídeo: transcrevendo… depois {backend}"
                )
                self.video_result_label.set_text(
                    f"A gerar indice.txt via {backend} após a transcrição."
                )

        def worker() -> None:
            try:
                transcribers = self._transcriber
                if transcribers is None:
                    raise RuntimeError("modelo não disponível")
                out = analyze_video_to_topics(
                    video_path,
                    transcribers,
                    save_dir,
                    language=language_hint,
                    gap_seconds=10.0,
                    ai_summary_options=ai_opts,
                )
                GLib.idle_add(self._on_meeting_video_complete, None, str(out))
            except Exception as exc:
                GLib.idle_add(self._on_meeting_video_complete, str(exc), "")

        threading.Thread(target=worker, daemon=True).start()

    def _on_meeting_video_complete(self, error: Optional[str], out_dir: str) -> bool:
        self._video_processing = False
        self.action_button.set_sensitive(True)
        self._update_video_topics_button()

        page_label = (
            self.youtube_result_label
            if self._media_job_kind == "youtube"
            else self.video_result_label
        )
        ai_note = ""
        if self._settings.resolved_meeting_ai_enabled():
            if self._settings.cursor_mode:
                ai_note = "indice.txt via Cursor Agent (local). "
            else:
                ai_note = "indice.txt via OpenRouter. "
        ok_msg = (
            f'Consulte "indice.txt" ({ai_note}se activou IA em Configurações). '
            "Cada tópico também tem o seu .txt numerado."
        )

        if error:
            self.status_label.set_text(f"Erro ao processar: {error}")
            page_label.set_text("")
        else:
            self.status_label.set_text(f"✓ Tópicos salvos em: {out_dir}")
            page_label.set_text(ok_msg)
        if getattr(self, "window", None):
            self.window.present()
            if self.corner_mode:
                self._position_bottom_right()
        return False

    def run_app(self):
        """Run the application."""
        try:
            self.run(None)
        finally:
            if self._hotkey_listener is not None:
                self._hotkey_listener.stop()


def run_gui(
    model_size: Optional[ModelSize] = None,
    auto_copy: bool = True,
    *,
    corner_mode: bool = False,
    auto_start_recording: bool = False,
    save_transcriptions: Optional[bool] = None,
    save_directory: Optional[Path] = None,
    start_hidden: bool = False,
    global_hotkey: Optional[str] = None,
    meeting_mode: Optional[bool] = None,
):
    """Entry point for GUI mode."""
    app = ListenGUI(
        model_size=model_size,
        auto_copy=auto_copy,
        corner_mode=corner_mode,
        auto_start_recording=auto_start_recording,
        save_transcriptions=save_transcriptions,
        save_directory=save_directory,
        start_hidden=start_hidden,
        global_hotkey=global_hotkey,
        meeting_mode=meeting_mode,
    )
    app.run_app()
