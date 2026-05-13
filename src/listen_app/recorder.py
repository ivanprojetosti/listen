"""Audio recording module for voice-to-text transcription."""

import audioop
import io
import os
import struct
import threading
import wave
from typing import Optional, Callable

import pyaudio

from .audio_sources import get_default_monitor_source


class AudioRecorder:
    """Records audio from the microphone with push-to-talk support."""

    # Whisper expects 16kHz mono audio
    SAMPLE_RATE = 16000
    CHANNELS = 1
    CHUNK_SIZE = 1024
    FORMAT = pyaudio.paInt16

    def __init__(
        self,
        on_status_change: Optional[Callable[[str], None]] = None,
        on_audio_chunk: Optional[Callable[[bytes], None]] = None,
        *,
        meeting_mode: bool = False,
    ):
        """
        Initialize the audio recorder.

        Args:
            on_status_change: Optional callback for status updates (e.g., 'recording', 'stopped')
            on_audio_chunk: Optional callback for real-time audio data (for waveform display)
            meeting_mode: Capture microphone and system output (meeting / loopback) mixed together
        """

        # Suppress ALSA error messages
        with self._alsa_error_handler():
            self._audio = pyaudio.PyAudio()

        self._stream: Optional[pyaudio.Stream] = None
        self._mic_stream: Optional[pyaudio.Stream] = None
        self._system_stream: Optional[pyaudio.Stream] = None
        self._system_audio: Optional[pyaudio.PyAudio] = None
        self._record_thread: Optional[threading.Thread] = None
        self._frames: list[bytes] = []
        self._is_recording = False
        self._lock = threading.Lock()
        self._on_status_change = on_status_change
        self._on_audio_chunk = on_audio_chunk
        self._meeting_mode = meeting_mode
        self._system_rate = self.SAMPLE_RATE
        self._system_channels = self.CHANNELS

    def _alsa_error_handler(self):
        """Context manager to suppress ALSA error messages to stderr."""
        from contextlib import contextmanager
        import os as _os

        @contextmanager
        def suppress_stderr():
            null_fd = -1
            saved_stderr_fd = -1
            try:
                # Open /dev/null
                null_fd = _os.open(_os.devnull, _os.O_RDWR)
                # Save original stderr (FD 2)
                saved_stderr_fd = _os.dup(2)

                # Redirect stderr (FD 2) to /dev/null
                _os.dup2(null_fd, 2)

                yield
            except Exception:
                # If anything fails, still yield so the app continues
                yield
            finally:
                # Restore stderr
                if saved_stderr_fd >= 0:
                    _os.dup2(saved_stderr_fd, 2)
                    _os.close(saved_stderr_fd)
                if null_fd >= 0:
                    _os.close(null_fd)

        return suppress_stderr()

    def _notify_status(self, status: str) -> None:
        """Notify status change via callback if set."""
        if self._on_status_change:
            self._on_status_change(status)

    @property
    def meeting_mode(self) -> bool:
        return self._meeting_mode

    @meeting_mode.setter
    def meeting_mode(self, enabled: bool) -> None:
        if self._is_recording:
            return
        self._meeting_mode = enabled

    def start(self, input_device_index: Optional[int] = None) -> None:
        """Start recording audio from the microphone."""
        with self._lock:
            if self._is_recording:
                return

            self._frames = []
            self._is_recording = True

            if self._meeting_mode:
                self._start_meeting_capture(input_device_index)
            else:
                self._stream = self._audio.open(
                    format=self.FORMAT,
                    channels=self.CHANNELS,
                    rate=self.SAMPLE_RATE,
                    input=True,
                    input_device_index=input_device_index,
                    frames_per_buffer=self.CHUNK_SIZE,
                    stream_callback=self._audio_callback,
                )
                self._stream.start_stream()

            self._notify_status("recording")

    def _start_meeting_capture(self, input_device_index: Optional[int]) -> None:
        monitor_source = get_default_monitor_source()
        if monitor_source is None:
            raise RuntimeError(
                "Modo reunião indisponível: não foi possível localizar a saída de áudio do sistema (pactl)."
            )

        self._mic_stream = self._audio.open(
            format=self.FORMAT,
            channels=self.CHANNELS,
            rate=self.SAMPLE_RATE,
            input=True,
            input_device_index=input_device_index,
            frames_per_buffer=self.CHUNK_SIZE,
        )

        previous_source = os.environ.get("PULSE_SOURCE")
        os.environ["PULSE_SOURCE"] = monitor_source
        try:
            with self._alsa_error_handler():
                self._system_audio = pyaudio.PyAudio()
            self._system_stream, self._system_rate, self._system_channels = (
                self._open_system_stream(self._system_audio)
            )
        finally:
            if previous_source is None:
                os.environ.pop("PULSE_SOURCE", None)
            else:
                os.environ["PULSE_SOURCE"] = previous_source

        self._record_thread = threading.Thread(
            target=self._meeting_capture_loop,
            daemon=True,
        )
        self._record_thread.start()

    def _open_system_stream(
        self, audio: pyaudio.PyAudio
    ) -> tuple[pyaudio.Stream, int, int]:
        for rate, channels in (
            (self.SAMPLE_RATE, self.CHANNELS),
            (48000, 2),
            (44100, 2),
        ):
            try:
                stream = audio.open(
                    format=self.FORMAT,
                    channels=channels,
                    rate=rate,
                    input=True,
                    frames_per_buffer=self.CHUNK_SIZE,
                )
                return stream, rate, channels
            except OSError:
                continue
        raise RuntimeError(
            "Modo reunião indisponível: não foi possível abrir a captura do áudio do sistema."
        )

    def _meeting_capture_loop(self) -> None:
        while True:
            with self._lock:
                if not self._is_recording:
                    break
                mic_stream = self._mic_stream
                system_stream = self._system_stream
                system_rate = self._system_rate
                system_channels = self._system_channels

            if mic_stream is None or system_stream is None:
                break

            try:
                mic_data = mic_stream.read(
                    self.CHUNK_SIZE, exception_on_overflow=False
                )
                system_data = system_stream.read(
                    self.CHUNK_SIZE, exception_on_overflow=False
                )
            except OSError:
                break

            system_mono = self._to_target_pcm(
                system_data, system_channels, system_rate
            )
            mixed = self._mix_pcm16(mic_data, system_mono)

            with self._lock:
                if self._is_recording:
                    self._frames.append(mixed)
                    if self._on_audio_chunk:
                        self._on_audio_chunk(mixed)

    def _to_target_pcm(self, data: bytes, channels: int, rate: int) -> bytes:
        if channels > 1:
            data = audioop.tomono(data, 2, 0.5, 0.5)
        if rate != self.SAMPLE_RATE:
            data, _ = audioop.ratecv(data, 2, 1, rate, self.SAMPLE_RATE, None)
        return data

    @staticmethod
    def _mix_pcm16(primary: bytes, secondary: bytes) -> bytes:
        if not secondary:
            return primary
        if not primary:
            return secondary

        primary_samples = struct.unpack(f"<{len(primary) // 2}h", primary)
        secondary_samples = struct.unpack(f"<{len(secondary) // 2}h", secondary)
        count = max(len(primary_samples), len(secondary_samples))

        mixed: list[int] = []
        for index in range(count):
            left = primary_samples[index] if index < len(primary_samples) else 0
            right = secondary_samples[index] if index < len(secondary_samples) else 0
            mixed.append(max(-32768, min(32767, (left + right) // 2)))

        return struct.pack(f"<{count}h", *mixed)

    def _audio_callback(self, in_data, frame_count, time_info, status):
        """Callback for audio stream - stores audio frames."""
        if self._is_recording:
            self._frames.append(in_data)
            if self._on_audio_chunk:
                self._on_audio_chunk(in_data)
        return (None, pyaudio.paContinue)

    def stop(self) -> bytes:
        """
        Stop recording and return the audio data as WAV bytes.

        Returns:
            WAV file contents as bytes
        """
        with self._lock:
            if not self._is_recording:
                return b""

            self._is_recording = False

            if self._meeting_mode:
                self._stop_meeting_capture()
            elif self._stream:
                self._stream.stop_stream()
                self._stream.close()
                self._stream = None

            self._notify_status("stopped")

            # Convert frames to WAV format in memory
            return self._frames_to_wav()

    def _stop_meeting_capture(self) -> None:
        if self._record_thread is not None:
            self._record_thread.join(timeout=2)
            self._record_thread = None

        for stream in (self._mic_stream, self._system_stream):
            if stream is not None:
                stream.stop_stream()
                stream.close()

        self._mic_stream = None
        self._system_stream = None

        if self._system_audio is not None:
            self._system_audio.terminate()
            self._system_audio = None

    def _frames_to_wav(self) -> bytes:
        """Convert recorded frames to WAV format bytes."""
        buffer = io.BytesIO()

        with wave.open(buffer, "wb") as wf:
            wf.setnchannels(self.CHANNELS)
            wf.setsampwidth(self._audio.get_sample_size(self.FORMAT))
            wf.setframerate(self.SAMPLE_RATE)
            wf.writeframes(b"".join(self._frames))

        return buffer.getvalue()

    def save_to_file(self, filepath: str) -> None:
        """
        Save the last recording to a WAV file.

        Args:
            filepath: Path to save the WAV file
        """
        wav_data = self._frames_to_wav()
        with open(filepath, "wb") as f:
            f.write(wav_data)

    def is_recording(self) -> bool:
        """Check if currently recording."""
        return self._is_recording

    def terminate(self) -> None:
        """Clean up PyAudio resources."""
        if self._is_recording:
            self.stop()
        if self._stream:
            self._stream.close()
        self._audio.terminate()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.terminate()
