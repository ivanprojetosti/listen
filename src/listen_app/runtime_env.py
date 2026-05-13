"""Garante locale/IO em UTF-8 para evitar UnicodeDecodeError (0xc3) no Whisper/ctranslate2."""

from __future__ import annotations

import os


def _env_has_utf8(name: str) -> bool:
    v = (os.environ.get(name) or "").lower()
    return "utf-8" in v or "utf8" in v


def ensure_utf8_runtime() -> None:
    """
    Deve ser chamado no início do processo (antes de inferência).
    Com LANG/LC_ALL=C, várias libs decodificam texto como ASCII e quebram em PT/AR/etc.
    """
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    if _env_has_utf8("LC_ALL") or _env_has_utf8("LANG"):
        return

    lc = (os.environ.get("LC_ALL") or "").strip().upper()
    lang = (os.environ.get("LANG") or "").strip().upper()
    if lc in ("", "C", "POSIX"):
        os.environ["LC_ALL"] = "C.UTF-8"
    if lang in ("", "C", "POSIX"):
        os.environ["LANG"] = "C.UTF-8"
