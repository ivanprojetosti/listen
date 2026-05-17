"""Transcrição de gravação: idioma detectado + tradução opcional."""

from __future__ import annotations

from .translation import languages_differ, normalize_language_code, translate_text
from .transcriber import Transcriber, TranscriptionResult


def transcribe_capture(
    transcriber: Transcriber,
    audio_source: str | bytes,
    *,
    whisper_language: str | None = None,
    translation_target: str | None = "pt-br",
) -> TranscriptionResult:
    """
    Transcreve no idioma detectado (ou ``whisper_language`` se forçado explicitamente).
    Se ``translation_target`` for definido e diferente do detectado, acrescenta tradução.
    """
    # Gravação: whisper_language=None → detectar (não usar config legada "language").
    lang_hint = (whisper_language or "").strip() or None
    result = transcriber.transcribe(audio_source, language=lang_hint)
    detected = normalize_language_code(result.language)
    target = normalize_language_code(translation_target)

    translation: str | None = None
    if target and detected and languages_differ(detected, target) and result.text.strip():
        translation = translate_text(result.text, detected, target)

    return TranscriptionResult(
        text=result.text,
        language=result.language,
        language_probability=result.language_probability,
        duration=result.duration,
        translation=translation,
        translation_language=target if translation else None,
    )
