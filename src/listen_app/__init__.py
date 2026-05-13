"""Listen - Voice-to-text transcription tool for Linux."""

from .runtime_env import ensure_utf8_runtime

ensure_utf8_runtime()

__version__ = "1.0.0"

from .recorder import AudioRecorder
from .transcriber import Transcriber, TranscriptionResult, ModelSize

__all__ = [
    "AudioRecorder",
    "Transcriber",
    "TranscriptionResult",
    "ModelSize",
    "__version__",
]
