"""
Listen - Voice-to-text transcription tool for Linux.

Usage:
    listen                  # Start in push-to-talk mode (hold Ctrl+Space)
    listen --toggle         # Start in toggle mode (press to start/stop)
    listen --model small    # Use a specific model size
    listen --help           # Show help

Controls:
    Ctrl+Space: Record (hold or toggle based on mode)
    Ctrl+C:     Exit
"""

import argparse
import sys
import threading
from pathlib import Path
from typing import Optional

from .runtime_env import ensure_utf8_runtime

ensure_utf8_runtime()

from pynput import keyboard
from rich.console import Console
from rich.markup import escape as rich_escape
from rich.panel import Panel
from rich.live import Live
from rich.text import Text

from .clipboard_copy import copy_plain_text
from .hotkey import HotkeyBinding, format_hotkey_examples
from .recorder import AudioRecorder
from .settings import ListenSettings
from .transcriber import Transcriber, ModelSize


console = Console()


def _configure_settings_interactive() -> ListenSettings:
    settings = ListenSettings.load()
    console.print(
        Panel(
            "[bold]Configuração do modo rápido[/bold]\n\n"
            "Defina o atalho global e onde salvar as transcrições.\n"
            f"Exemplos: {', '.join(format_hotkey_examples())}",
            border_style="blue",
        )
    )

    current = settings.hotkey
    raw = console.input(
        f"Atalho global [{current}]: ",
        markup=False,
    ).strip()
    if raw:
        try:
            HotkeyBinding.parse(raw)
            settings.hotkey = raw
        except ValueError as exc:
            console.print(f"[red]Atalho inválido: {exc}[/red]")
            sys.exit(1)

    current_dir = settings.save_directory
    raw_dir = console.input(
        f"Pasta para salvar .txt [{current_dir}]: ",
        markup=False,
    ).strip()
    if raw_dir:
        settings.save_directory = raw_dir

    corner = console.input(
        f"Janela no canto inferior direito? [{'S' if settings.corner_mode else 'N'}]: ",
        markup=False,
    ).strip().lower()
    if corner in ("s", "sim", "y", "yes"):
        settings.corner_mode = True
    elif corner in ("n", "nao", "não", "no"):
        settings.corner_mode = False

    path = settings.save()
    save_dir = rich_escape(str(settings.resolved_save_directory()))
    config_path = rich_escape(str(path))
    console.print(f"[green]✓[/green] Configuração salva em [cyan]{config_path}[/cyan]")
    console.print(
        f"Atalho: [cyan]{settings.hotkey}[/cyan] • "
        f"Salvar em: [cyan]{save_dir}[/cyan]"
    )
    console.print(
        "\n[dim]Para usar o atalho, inicie o Listen em segundo plano:[/dim] "
        "[bold cyan]listen --daemon[/bold cyan]"
    )
    return settings


def _print_settings(settings: ListenSettings) -> None:
    save_dir = rich_escape(str(settings.resolved_save_directory()))
    console.print(
        Panel(
            f"Atalho: [cyan]{settings.hotkey}[/cyan]\n"
            f"Pasta de gravações: [cyan]{save_dir}[/cyan]\n"
            f"Canto inferior direito: [cyan]{'sim' if settings.corner_mode else 'não'}[/cyan]\n"
            f"Gravar ao abrir: [cyan]{'sim' if settings.auto_record else 'não'}[/cyan]\n"
            f"Salvar transcrições: [cyan]{'sim' if settings.save_transcriptions else 'não'}[/cyan]\n"
            f"Copiar automaticamente: [cyan]{'sim' if settings.auto_copy else 'não'}[/cyan]",
            title="[bold blue]Listen — configuração[/bold blue]",
            border_style="blue",
        )
    )


def _run_quick_gui(
    model_size: Optional[ModelSize],
    settings: ListenSettings,
    *,
    daemon: bool,
    auto_copy: bool,
) -> None:
    from .gui import run_gui

    run_gui(
        model_size=model_size,
        auto_copy=auto_copy,
        corner_mode=settings.corner_mode,
        auto_start_recording=settings.auto_record and not daemon,
        save_transcriptions=settings.save_transcriptions,
        save_directory=settings.resolved_save_directory(),
        start_hidden=daemon,
        global_hotkey=settings.hotkey if daemon else None,
    )


class ListenApp:
    """Main application for voice-to-text transcription."""

    def __init__(
        self,
        model_size: Optional[ModelSize] = None,
        toggle_mode: bool = False,
        auto_copy: bool = True,
    ):
        """
        Initialize the Listen application.

        Args:
            model_size: Whisper model size to use
            toggle_mode: If True, use toggle (press to start/stop) instead of push-to-talk
            auto_copy: If True, automatically copy transcription to clipboard
        """
        self.toggle_mode = toggle_mode
        self.auto_copy = auto_copy
        self._running = True
        self._recording = False
        self._processing = False
        self._last_transcription = ""
        self._last_language = ""
        self._status_lock = threading.Lock()

        # Initialize components (lazy load transcriber)
        self._recorder = AudioRecorder(on_status_change=self._on_recording_status)
        self._transcriber: Optional[Transcriber] = None
        self._model_size = model_size

        # Keyboard state
        self._ctrl_pressed = False
        self._space_pressed = False

    def _get_transcriber(self) -> Transcriber:
        """Lazy load the transcriber model."""
        if self._transcriber is None:
            console.print("[dim]Loading speech recognition model...[/dim]")
            self._transcriber = Transcriber(model_size=self._model_size)
            info = self._transcriber.get_model_info()
            console.print(
                f"[green]✓[/green] Model loaded: [cyan]{info['model_size']}[/cyan] "
                f"on [cyan]{info['device']}[/cyan]"
            )
        return self._transcriber

    def _on_recording_status(self, status: str) -> None:
        """Handle recording status changes."""
        with self._status_lock:
            self._recording = status == "recording"

    def _get_display(self) -> Panel:
        """Generate the status display panel."""
        with self._status_lock:
            if self._processing:
                status = Text("⏳ Processing...", style="yellow bold")
            elif self._recording:
                status = Text(
                    "🔴 Recording... (release to transcribe)", style="red bold"
                )
            else:
                mode = "Press" if self.toggle_mode else "Hold"
                status = Text(f"🎤 Ready - {mode} Ctrl+Space to record", style="green")

            content = Text()
            content.append(status)

            if self._last_transcription:
                content.append("\n\n")
                content.append("Last transcription:\n", style="dim")
                content.append(f'"{self._last_transcription}"', style="white")
                if self.auto_copy:
                    content.append(" ", style="dim")
                    content.append("(copied to clipboard)", style="dim italic")
                if self._last_language:
                    lang_names = {
                        "ar": "Arabic",
                        "en": "English",
                        "fr": "French",
                        "es": "Spanish",
                        "de": "German",
                        "zh": "Chinese",
                    }
                    lang_display = lang_names.get(
                        self._last_language, self._last_language.upper()
                    )
                    content.append(f" [{lang_display}]", style="cyan")

            return Panel(
                content,
                title="[bold blue]Listen[/bold blue]",
                subtitle="[dim]Ctrl+C to exit[/dim]",
                border_style="blue",
            )

    def _start_recording(self) -> None:
        """Start audio recording."""
        if not self._recording and not self._processing:
            self._recorder.start()

    def _stop_recording_and_transcribe(self) -> None:
        """Stop recording and transcribe the audio."""
        if self._recording:
            with self._status_lock:
                self._processing = True

            # Stop recording and get audio
            audio_data = self._recorder.stop()

            if len(audio_data) > 1000:  # Minimum audio length check
                try:
                    transcriber = self._get_transcriber()
                    result = transcriber.transcribe(audio_data)

                    self._last_transcription = result.text
                    self._last_language = result.language

                    if self.auto_copy and result.text:
                        copy_plain_text(result.text)

                except Exception as e:
                    console.print(f"[red]Error: {e}[/red]")
            else:
                self._last_transcription = "(no audio captured)"

            with self._status_lock:
                self._processing = False

    def _on_key_press(self, key) -> None:
        """Handle key press events."""
        try:
            if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
                self._ctrl_pressed = True
            elif key == keyboard.Key.space:
                self._space_pressed = True

            # Check for Ctrl+Space
            if self._ctrl_pressed and self._space_pressed:
                if self.toggle_mode:
                    # Toggle mode: start or stop
                    if self._recording:
                        threading.Thread(
                            target=self._stop_recording_and_transcribe, daemon=True
                        ).start()
                    else:
                        self._start_recording()
                else:
                    # Push-to-talk: start recording
                    self._start_recording()
        except AttributeError:
            pass

    def _on_key_release(self, key) -> None:
        """Handle key release events."""
        try:
            if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
                self._ctrl_pressed = False
                # In push-to-talk mode, stop when Ctrl is released
                if not self.toggle_mode and self._recording:
                    threading.Thread(
                        target=self._stop_recording_and_transcribe, daemon=True
                    ).start()
            elif key == keyboard.Key.space:
                self._space_pressed = False
                # In push-to-talk mode, also stop when Space is released
                if not self.toggle_mode and self._recording:
                    threading.Thread(
                        target=self._stop_recording_and_transcribe, daemon=True
                    ).start()
        except AttributeError:
            pass

    def run(self) -> None:
        """Run the main application loop."""
        console.clear()
        console.print(
            Panel(
                "[bold]Listen[/bold] - Voice-to-Text Transcription\n\n"
                f"Mode: [cyan]{'Toggle' if self.toggle_mode else 'Push-to-talk'}[/cyan]\n"
                "Shortcut: [cyan]Ctrl+Space[/cyan]",
                border_style="blue",
            )
        )

        # Pre-load the model
        self._get_transcriber()

        console.print()

        # Start keyboard listener
        listener = keyboard.Listener(
            on_press=self._on_key_press, on_release=self._on_key_release
        )
        listener.start()

        try:
            with Live(
                self._get_display(), refresh_per_second=4, console=console
            ) as live:
                while self._running:
                    live.update(self._get_display())
        except KeyboardInterrupt:
            pass
        finally:
            listener.stop()
            self._recorder.terminate()
            console.print("\n[dim]Goodbye![/dim]")


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Voice-to-text transcription tool for Linux",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    listen                      Start GUI (default)
    listen --cli                Use terminal interface
    listen --quick              Modo rápido (canto + gravação + salvar .txt)
    listen --daemon             Atalho global em segundo plano
    listen --configure          Configurar atalho e pasta de salvamento
    listen --model small        Use the 'small' Whisper model
        """,
    )

    parser.add_argument(
        "--quick",
        "-q",
        action="store_true",
        help="Modo rápido: janela no canto inferior direito, grava ao abrir e salva .txt",
    )

    parser.add_argument(
        "--daemon",
        "-d",
        action="store_true",
        help="Fica em segundo plano e abre/grava com o atalho configurado",
    )

    parser.add_argument(
        "--configure",
        action="store_true",
        help="Configurar atalho global e pasta de transcrições",
    )

    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Mostrar configuração atual do modo rápido",
    )

    parser.add_argument(
        "--hotkey",
        metavar="COMBO",
        help="Atalho global (ex: ctrl+shift+l); salva na configuração",
    )

    parser.add_argument(
        "--save-dir",
        metavar="PATH",
        help="Pasta para salvar transcrições .txt; salva na configuração",
    )

    parser.add_argument(
        "--no-corner",
        action="store_true",
        help="Desativa posição no canto inferior direito (modo rápido)",
    )

    parser.add_argument(
        "--no-auto-record",
        action="store_true",
        help="Não inicia gravação automaticamente ao abrir (modo rápido)",
    )

    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Não salvar transcrições em arquivo .txt",
    )

    parser.add_argument(
        "--cli",
        "-c",
        action="store_true",
        help="Use terminal interface instead of GUI",
    )

    parser.add_argument(
        "--toggle",
        "-t",
        action="store_true",
        help="Use toggle mode (press to start/stop) instead of push-to-talk (CLI only)",
    )

    parser.add_argument(
        "--model",
        "-m",
        choices=["tiny", "base", "small", "medium", "large-v3"],
        default=None,
        help="Whisper model size (default: auto-select based on device)",
    )

    parser.add_argument(
        "--no-copy",
        action="store_true",
        help="Don't automatically copy transcription to clipboard",
    )

    args = parser.parse_args()

    settings = ListenSettings.load()
    settings_changed = False

    if args.hotkey:
        try:
            HotkeyBinding.parse(args.hotkey)
        except ValueError as exc:
            console.print(f"[red]Atalho inválido: {exc}[/red]")
            sys.exit(1)
        settings.hotkey = args.hotkey
        settings_changed = True

    if args.save_dir:
        settings.save_directory = args.save_dir
        settings_changed = True

    if args.no_corner:
        settings.corner_mode = False
        settings_changed = True

    if args.no_auto_record:
        settings.auto_record = False
        settings_changed = True

    if args.no_save:
        settings.save_transcriptions = False
        settings_changed = True

    if args.quick or args.daemon:
        if not args.no_corner:
            settings.corner_mode = True
        if not args.no_auto_record:
            settings.auto_record = True
        if not args.no_save:
            settings.save_transcriptions = True
        settings_changed = True

    if settings_changed and not args.configure:
        path = settings.save()
        console.print(
            f"[dim]Configuração atualizada em {rich_escape(str(path))}[/dim]"
        )

    config_only = (
        any(
            (
                args.hotkey,
                args.save_dir,
                args.no_corner,
                args.no_auto_record,
                args.no_save,
            )
        )
        and not args.quick
        and not args.daemon
        and not args.cli
        and not args.configure
        and not args.show_config
    )

    if config_only:
        _print_settings(settings)
        return

    quick_auto_copy = settings.auto_copy if not args.no_copy else False

    try:
        if args.configure:
            _configure_settings_interactive()
        elif args.show_config:
            _print_settings(settings)
        elif args.cli:
            # Terminal interface
            app = ListenApp(
                model_size=args.model,
                toggle_mode=args.toggle,
                auto_copy=not args.no_copy,
            )
            app.run()
        elif args.quick or args.daemon:
            if args.daemon:
                save_dir = rich_escape(str(settings.resolved_save_directory()))
                console.print(
                    Panel(
                        f"[bold]Listen em segundo plano[/bold]\n\n"
                        f"Atalho: [cyan]{settings.hotkey}[/cyan]\n"
                        f"Salvar em: [cyan]{save_dir}[/cyan]\n"
                        f"Canto inferior direito: [cyan]{'sim' if settings.corner_mode else 'não'}[/cyan]\n\n"
                        "[dim]Pressione o atalho para abrir e gravar. "
                        "Pressione de novo para parar e transcrever.[/dim]",
                        border_style="blue",
                    )
                )
            try:
                _run_quick_gui(
                    model_size=args.model,
                    settings=settings,
                    daemon=args.daemon,
                    auto_copy=quick_auto_copy,
                )
            except ImportError as e:
                _missing = getattr(e, "name", "") or ""
                _msg = str(e).lower()
                if _missing in ("gi", "_gi", "cairo") or "gi" in _msg:
                    console.print(
                        "[red]GTK/PyGObject não encontrado (módulo 'gi'). A interface gráfica precisa dele.[/red]\n\n"
                        "[bold]Ubuntu / Debian:[/bold] instale os pacotes do sistema e use um venv com site-packages do SO, ou o Python do sistema:\n"
                        "  [cyan]sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 libgtk-4-1 libadwaita-1-0[/cyan]\n"
                        "  [cyan]python3 -m venv --system-site-packages .venv[/cyan]   [dim]# depois: source .venv/bin/activate && pip install -e .[/dim]\n\n"
                        "[bold]Sem instalar GTK:[/bold] use só o modo terminal:\n"
                        "  [cyan]listen --cli[/cyan]"
                    )
                    sys.exit(1)
                raise
        else:
            # GUI interface (default)
            try:
                from .gui import run_gui
            except ImportError as e:
                _missing = getattr(e, "name", "") or ""
                _msg = str(e).lower()
                if _missing in ("gi", "_gi", "cairo") or "gi" in _msg:
                    console.print(
                        "[red]GTK/PyGObject não encontrado (módulo 'gi'). A interface gráfica precisa dele.[/red]\n\n"
                        "[bold]Ubuntu / Debian:[/bold] instale os pacotes do sistema e use um venv com site-packages do SO, ou o Python do sistema:\n"
                        "  [cyan]sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 libgtk-4-1 libadwaita-1-0[/cyan]\n"
                        "  [cyan]python3 -m venv --system-site-packages .venv[/cyan]   [dim]# depois: source .venv/bin/activate && pip install -e .[/dim]\n\n"
                        "[bold]Sem instalar GTK:[/bold] use só o modo terminal:\n"
                        "  [cyan]listen --cli[/cyan]"
                    )
                    sys.exit(1)
                raise
            settings = ListenSettings.load()
            if not any((args.quick, args.daemon, args.cli, args.configure)):
                console.print(
                    "[dim]Dica: use [cyan]listen --daemon[/cyan] para o atalho global "
                    f"([cyan]{settings.hotkey}[/cyan]) e salvar em "
                    f"[cyan]{settings.resolved_save_directory()}[/cyan][/dim]"
                )
            run_gui(
                model_size=args.model,
                auto_copy=not args.no_copy,
                save_transcriptions=settings.save_transcriptions,
                save_directory=settings.resolved_save_directory(),
                meeting_mode=settings.meeting_mode,
            )
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
