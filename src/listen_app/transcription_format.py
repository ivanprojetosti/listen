"""Formatação de transcrição original + tradução."""

from __future__ import annotations

TRANSLATION_HEADER = "——— Tradução ({lang}) ———"


def format_transcription_display(
    original: str,
    *,
    language: str = "",
    translation: str | None = None,
    translation_language: str | None = None,
) -> str:
    """Original no idioma detectado; tradução abaixo."""
    original = (original or "").strip()
    if not translation or not (translation := translation.strip()):
        return original
    lang_label = (translation_language or "?").strip() or "?"
    return "\n".join(
        [
            original,
            "",
            TRANSLATION_HEADER.format(lang=lang_label),
            translation,
        ]
    )
