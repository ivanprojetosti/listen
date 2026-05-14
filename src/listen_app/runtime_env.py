"""Garante locale/IO em UTF-8 para evitar UnicodeDecodeError (0xc3) no Whisper/ctranslate2."""

from __future__ import annotations

import locale
import os

_APPLIED = False


def ensure_utf8_runtime() -> None:
    """
    Equivalente a:
      LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONUTF8=1 listen ...

    Deve rodar no início do processo, antes de carregar faster-whisper/GTK.
    Com LC_ALL=C (sem .UTF-8), libs C/Python decodificam texto como ASCII e quebram em PT/AR.
    """
    global _APPLIED
    if _APPLIED:
        return
    _APPLIED = True

    if os.environ.get("LISTEN_PRESERVE_LOCALE") == "1":
        return

    os.environ["LANG"] = "C.UTF-8"
    os.environ["LC_ALL"] = "C.UTF-8"
    os.environ["PYTHONUTF8"] = "1"
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    for name in ("C.UTF-8", "en_US.UTF-8", "pt_BR.UTF-8"):
        try:
            locale.setlocale(locale.LC_ALL, name)
            break
        except locale.Error:
            continue
