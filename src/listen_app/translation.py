"""Tradução do texto transcrito (complemento ao Whisper)."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_LANG_CODE = re.compile(r"^[a-z]{2}")


def normalize_language_code(code: str | None) -> str:
    """Código ISO 639-1 em minúsculas (ex.: en, pt, pt-br)."""
    if not code:
        return ""
    return code.strip().lower().replace("_", "-")


def primary_language_code(code: str | None) -> str:
    """Família do idioma para comparar (pt-br e pt → pt)."""
    c = normalize_language_code(code)
    if not c:
        return ""
    if c.startswith("pt"):
        return "pt"
    m = _LANG_CODE.match(c)
    return m.group(0) if m else c[:2]


def languages_differ(detected: str, target: str) -> bool:
    return primary_language_code(detected) != primary_language_code(target)


def translate_text(
    text: str,
    source_lang: str,
    target_lang: str,
) -> str | None:
    """
    Traduz ``text`` de ``source_lang`` para ``target_lang``.
    Devolve None se falhar ou se deep-translator não estiver instalado.
    """
    text = (text or "").strip()
    src = normalize_language_code(source_lang)
    tgt = normalize_language_code(target_lang)
    if not text or not tgt or not languages_differ(src, tgt):
        return None

    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        logger.warning(
            "deep-translator não instalado; tradução omitida. "
            "Instale: pip install deep-translator"
        )
        return None

    try:
        translator = GoogleTranslator(
            source=src if src else "auto",
            target=tgt,
        )
        # Textos longos: dividir por parágrafos
        chunks = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
        if not chunks:
            chunks = [text]
        out_parts: list[str] = []
        for chunk in chunks:
            if len(chunk) > 4500:
                sentences = re.split(r"(?<=[.!?])\s+", chunk)
                buf = ""
                for sent in sentences:
                    if len(buf) + len(sent) > 4500 and buf:
                        out_parts.append(translator.translate(buf.strip()))
                        buf = sent
                    else:
                        buf = f"{buf} {sent}".strip() if buf else sent
                if buf:
                    out_parts.append(translator.translate(buf.strip()))
            else:
                out_parts.append(translator.translate(chunk))
        return "\n\n".join(p for p in out_parts if p)
    except Exception as exc:
        logger.warning("Tradução falhou (%s → %s): %s", src, tgt, exc)
        return None
