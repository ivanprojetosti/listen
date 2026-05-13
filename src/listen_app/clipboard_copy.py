"""UTF-8 safe clipboard copy for Linux (avoids pyperclip ASCII issues on some backends)."""

from __future__ import annotations

import os
import shutil
import subprocess


def copy_plain_text(text: str) -> bool:
    """
    Copy plain text to the clipboard. Uses wl-copy/xclip with UTF-8 bytes first so
    Portuguese and other non-ASCII text works even when pyperclip falls back to
    backends that mishandle Unicode (e.g. some Klipper/qdbus paths).
    """
    if not text:
        return True

    data = text.encode("utf-8")

    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        try:
            subprocess.run(
                ["wl-copy"],
                input=data,
                check=True,
                capture_output=True,
                timeout=30,
            )
            return True
        except (subprocess.SubprocessError, OSError):
            pass

    if os.environ.get("DISPLAY") and shutil.which("xclip"):
        try:
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=data,
                check=True,
                capture_output=True,
                timeout=30,
            )
            return True
        except (subprocess.SubprocessError, OSError):
            pass

    try:
        import pyperclip

        pyperclip.copy(text)
        return True
    except Exception:
        return False
