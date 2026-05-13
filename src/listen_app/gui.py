"""GTK4 GUI for Listen voice-to-text application."""

import struct
import threading
from pathlib import Path
from typing import Optional

from .runtime_env import ensure_utf8_runtime

ensure_utf8_runtime()

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk

from .audio_sources import is_meeting_capture_available
from .clipboard_copy import copy_plain_text
from .hotkey import GlobalHotkeyListener
from .recorder import AudioRecorder
from .transcriber import Transcriber, ModelSize
from .settings import ListenSettings
from .transcription_store import (
    SavedTranscription,
    list_saved_transcriptions,
    read_transcription_text,
    save_transcription_text,
)


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

        # Transcription result must reach the GTK thread without passing str through
        # GLib.idle_add(..., user_data): GObject marshalling can use ASCII and break PT/AR text.
        self._transcription_delivery_lock = threading.Lock()
        self._pending_transcription: Optional[tuple[str, str]] = None

        self.connect("activate", self._on_activate)

    def _on_activate(self, app):
        """Initialize the main window."""
        # Create main window
        self.window = Adw.ApplicationWindow(application=app)
        self.window.set_title("Listen")
        self.window.set_default_size(self.window_width, self.window_height)
        if self.corner_mode:
            self.window.connect("realize", self._on_window_realize_corner)

        # Main container
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Header bar
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label="Listen"))

        # Model selector dropdown
        model_options = ["tiny", "base", "small", "medium", "large-v3"]
        self._model_strings = Gtk.StringList.new(model_options)
        self.model_dropdown = Gtk.DropDown(model=self._model_strings)
        self.model_dropdown.set_tooltip_text(
            "Select model size (larger = better Arabic)"
        )
        # Set default selection based on initial model_size or default
        default_idx = (
            model_options.index(self.model_size)
            if self.model_size in model_options
            else 2
        )  # 'small'
        self.model_dropdown.set_selected(default_idx)
        self.model_dropdown.connect("notify::selected", self._on_model_changed)
        header.pack_end(self.model_dropdown)

        main_box.append(header)

        # Content box with padding
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content_box.set_margin_start(20)
        content_box.set_margin_end(20)
        content_box.set_margin_top(16)
        content_box.set_margin_bottom(16)

        # Device info panel (collapsible)
        self.device_info_frame = Gtk.Frame()
        self.device_info_frame.add_css_class("device-info-frame")
        device_info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        device_info_box.set_margin_start(12)
        device_info_box.set_margin_end(12)
        device_info_box.set_margin_top(8)
        device_info_box.set_margin_bottom(8)

        # Device info label
        self.device_info_label = Gtk.Label(label="⏳ Initializing...")
        self.device_info_label.set_xalign(0)
        self.device_info_label.set_wrap(True)
        self.device_info_label.add_css_class("device-info-label")
        self.device_info_label.set_use_markup(True)
        device_info_box.append(self.device_info_label)

        self.device_info_frame.set_child(device_info_box)
        content_box.append(self.device_info_frame)

        meeting_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        meeting_row.set_halign(Gtk.Align.START)
        meeting_label = Gtk.Label(label="Modo reunião")
        meeting_label.set_xalign(0)
        meeting_row.append(meeting_label)
        self.meeting_switch = Gtk.Switch()
        self.meeting_switch.set_active(self.meeting_mode)
        self.meeting_switch.set_sensitive(is_meeting_capture_available())
        self.meeting_switch.connect("notify::active", self._on_meeting_mode_toggled)
        meeting_row.append(self.meeting_switch)
        content_box.append(meeting_row)

        self.meeting_hint_label = Gtk.Label(
            label="Captura microfone + áudio do sistema (vozes da chamada)"
        )
        self.meeting_hint_label.set_xalign(0)
        self.meeting_hint_label.set_wrap(True)
        self.meeting_hint_label.add_css_class("dim-label")
        self.meeting_hint_label.set_visible(self.meeting_mode)
        content_box.append(self.meeting_hint_label)

        if not is_meeting_capture_available():
            unavailable = Gtk.Label(
                label="Modo reunião indisponível neste sistema (requer PipeWire/PulseAudio)."
            )
            unavailable.set_xalign(0)
            unavailable.set_wrap(True)
            unavailable.add_css_class("dim-label")
            content_box.append(unavailable)

        # Saved transcriptions history
        history_label = Gtk.Label(label="Transcrições salvas")
        history_label.set_xalign(0)
        history_label.add_css_class("heading")
        content_box.append(history_label)

        self.saved_list_box = Gtk.ListBox()
        self.saved_list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.saved_list_box.add_css_class("saved-transcription-list")
        self.saved_list_box.connect("row-activated", self._on_saved_row_activated)

        self.saved_list_scroll = Gtk.ScrolledWindow()
        self.saved_list_scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        self.saved_list_scroll.set_min_content_height(100)
        self.saved_list_scroll.set_max_content_height(160)
        self.saved_list_scroll.set_vexpand(True)
        self.saved_list_scroll.set_child(self.saved_list_box)

        history_frame = Gtk.Frame()
        history_frame.add_css_class("saved-history-frame")
        history_frame.set_child(self.saved_list_scroll)
        content_box.append(history_frame)

        self.saved_empty_label = Gtk.Label(label="Nenhuma transcrição salva ainda")
        self.saved_empty_label.add_css_class("dim-label")
        self.saved_empty_label.set_margin_top(8)
        content_box.append(self.saved_empty_label)

        # Waveform visualization
        self.waveform = WaveformDrawingArea()
        waveform_frame = Gtk.Frame()
        waveform_frame.set_child(self.waveform)
        content_box.append(waveform_frame)

        # Status label
        self.status_label = Gtk.Label(label="Loading model...")
        self.status_label.add_css_class("dim-label")
        content_box.append(self.status_label)

        # Main action button
        self.action_button = Gtk.Button(label="🎤 Record")
        self.action_button.add_css_class("suggested-action")
        self.action_button.add_css_class("pill")
        self.action_button.set_size_request(-1, 50)
        self.action_button.connect("clicked", self._on_action_clicked)
        self.action_button.set_sensitive(False)
        content_box.append(self.action_button)

        # Transcription result
        self.result_label = Gtk.Label(label="")
        self.result_label.set_wrap(True)
        self.result_label.set_selectable(True)
        self.result_label.set_margin_top(8)
        content_box.append(self.result_label)

        main_box.append(content_box)
        self.window.set_content(main_box)

        # Apply custom CSS
        self._apply_css()

        # Initialize recorder
        self._recorder = AudioRecorder(
            on_status_change=self._on_recording_status,
            meeting_mode=self.meeting_mode,
        )

        # Load model in background
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

    def _schedule_transcription_ui(self, text: str, language: str = "") -> None:
        """Queue (text, language) for the main thread; avoids GObject UTF-8/ASCII bugs."""
        with self._transcription_delivery_lock:
            self._pending_transcription = (text, language)
        GLib.idle_add(self._deliver_pending_transcription)

    def _deliver_pending_transcription(self, *_args) -> bool:
        with self._transcription_delivery_lock:
            pending = self._pending_transcription
            self._pending_transcription = None
        if pending is not None:
            self._on_transcription_complete(pending[0], pending[1])
        return False

    def _on_window_realize_corner(self, _window):
        self._position_bottom_right()

    def _get_active_monitor_geometry(self) -> tuple[int, int, int, int]:
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor()
        if monitor is None:
            return 0, 0, 1920, 1080

        seat = display.get_default_seat()
        if seat is not None:
            pointer = seat.get_pointer()
            if pointer is not None:
                _, px, py, _ = pointer.get_position()
                at_pointer = display.get_monitor_at_point(px, py)
                if at_pointer is not None:
                    monitor = at_pointer

        geometry = monitor.get_geometry()
        return geometry.x, geometry.y, geometry.width, geometry.height

    def _position_bottom_right(self, *_args) -> bool:
        if not self.corner_mode:
            return False

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

        if self._state == self.STATE_READY and self._model_ready:
            self._start_recording()
        elif self._state == self.STATE_RECORDING:
            self._stop_and_transcribe()
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

            GLib.idle_add(self.action_button.set_sensitive, True)
            GLib.idle_add(self.model_dropdown.set_sensitive, True)
            GLib.idle_add(self._set_model_ready)

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

        self.result_label.set_text(f'"{text}"')
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
        self.status_label.set_text("Processing audio...")

        # Stop and transcribe in background
        threading.Thread(target=self._transcribe_audio, daemon=True).start()

    def _transcribe_audio(self):
        """Transcribe recorded audio (runs in background thread)."""
        audio_data = self._recorder.stop()

        if len(audio_data) < 1000:
            self._schedule_transcription_ui("(no audio captured)", "")
            return

        try:
            result = self._transcriber.transcribe(audio_data)
            text = result.text.strip()
            language = result.language or ""
            self._schedule_transcription_ui(text, language)
        except Exception as e:
            self._schedule_transcription_ui(f"Error: {e}", "")

    def _on_transcription_complete(self, text: str, language: str = ""):
        """Handle transcription completion (runs on main thread)."""
        if (
            self.auto_copy
            and text
            and not text.startswith("Error:")
            and not text.startswith("(")
        ):
            self._clipboard_set_text(text)

        saved_path: Optional[Path] = None
        if (
            self.save_transcriptions
            and self.save_directory
            and text
            and not text.startswith("Error:")
            and not text.startswith("(")
        ):
            try:
                saved_path = save_transcription_text(
                    text,
                    self.save_directory,
                    language=language,
                    meeting_mode=self.meeting_mode,
                )
            except OSError as exc:
                text = f"Error: não foi possível salvar o arquivo ({exc})"

        self._last_saved_path = saved_path

        if saved_path is not None:
            self._refresh_saved_list()

        self._last_transcription = text
        self._last_language = language
        self._state = self.STATE_RESULT

        self.action_button.set_label("📋 Copy & New Recording")
        self.action_button.remove_css_class("destructive-action")
        self.action_button.add_css_class("suggested-action")
        self.action_button.set_sensitive(True)
        self._set_meeting_switch_sensitive(True)

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

        if text and not text.startswith("Error:") and not text.startswith("("):
            status_msg = "✓ Copiado para a área de transferência!"
            if saved_path is not None:
                status_msg += f" • Salvo em {saved_path.name}"
            if lang_display:
                status_msg += f" • {lang_display} detectado"
            self.status_label.set_text(status_msg)
            self.result_label.set_text(f'"{text}"')
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
