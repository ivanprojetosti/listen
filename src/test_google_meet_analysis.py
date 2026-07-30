"""Testes do modo Google Meet (transcrição cronológica)."""

from pathlib import Path
import tempfile

from listen_app.google_meet_analysis import (
    format_chronological_line,
    save_chronological_transcription,
)
from listen_app.transcriber import TranscriptSegment


def test_format_chronological_line():
    seg = TranscriptSegment(start=2946.0, end=2947.0, text="Bechou?")
    assert format_chronological_line(seg) == "[49:06 – 49:07] Bechou?"


def test_save_chronological_transcription():
    segments = [
        TranscriptSegment(2946.0, 2947.0, "Bechou?"),
        TranscriptSegment(2947.0, 2948.0, "Bechou."),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        save_chronological_transcription(segments, out)
        text = (out / "transcricao.txt").read_text(encoding="utf-8")
    assert text == "[49:06 – 49:07] Bechou?\n[49:07 – 49:08] Bechou.\n"
