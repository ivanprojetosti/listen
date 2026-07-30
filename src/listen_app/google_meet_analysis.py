"""Modo Google Meet: transcrição cronológica com timestamps (sem identificar participantes)."""

from __future__ import annotations

from pathlib import Path

from .meeting_analysis import _fmt_hms
from .transcriber import TranscriptSegment


def format_chronological_line(seg: TranscriptSegment) -> str:
    """Uma linha: [início – fim] texto"""
    return f"[{_fmt_hms(seg.start)} – {_fmt_hms(seg.end)}] {seg.text.strip()}"


def save_chronological_transcription(
    segments: list[TranscriptSegment],
    output_dir: Path,
    *,
    created_at=None,
    whisper_model: str = "",
    source_video: Path | None = None,
    source_url: str | None = None,
    language: str = "",
) -> Path:
    """Grava transcricao.txt — só linhas [início – fim] texto."""
    _ = (created_at, whisper_model, source_video, source_url, language)
    output_dir.mkdir(parents=True, exist_ok=True)

    body_lines = [
        format_chronological_line(s)
        for s in segments
        if s.text.strip()
    ]
    text = "\n".join(body_lines).rstrip() + "\n" if body_lines else "\n"
    out_path = output_dir / "transcricao.txt"
    out_path.write_text(text, encoding="utf-8")
    return output_dir
