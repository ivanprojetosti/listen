"""Helpers for discovering PulseAudio / PipeWire capture sources."""

from __future__ import annotations

import subprocess


def get_default_monitor_source() -> str | None:
    """Return the monitor source name for the default audio output sink."""
    try:
        result = subprocess.run(
            ["pactl", "get-default-sink"],
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    sink = result.stdout.strip()
    if not sink:
        return None
    return f"{sink}.monitor"


def is_meeting_capture_available() -> bool:
    """Check whether system-audio loopback capture is likely available."""
    return get_default_monitor_source() is not None


def ensure_default_input_ready() -> None:
    """Wake the default PulseAudio/PipeWire input (often suspended until used)."""
    for args in (
        ["pactl", "suspend-source", "@DEFAULT_SOURCE@", "0"],
        ["pactl", "set-source-mute", "@DEFAULT_SOURCE@", "0"],
    ):
        try:
            subprocess.run(args, capture_output=True, timeout=2, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
