"""Persistent settings for Listen quick-capture / hotkey mode."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def _default_config_dir() -> Path:
    return Path.home() / ".config" / "listen"


def _default_save_dir() -> Path:
    return Path.home() / ".local" / "share" / "listen" / "transcriptions"


@dataclass
class ListenSettings:
    """User-facing options for hotkey and quick-capture mode."""

    hotkey: str = "ctrl+shift+l"
    save_directory: str = field(default_factory=lambda: str(_default_save_dir()))
    corner_mode: bool = True
    auto_record: bool = True
    auto_copy: bool = True
    save_transcriptions: bool = True
    meeting_mode: bool = False

    @classmethod
    def load(cls, path: Path | None = None) -> "ListenSettings":
        config_path = path or (_default_config_dir() / "config.json")
        if not config_path.is_file():
            return cls()

        try:
            data: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()

        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    def save(self, path: Path | None = None) -> Path:
        config_path = path or (_default_config_dir() / "config.json")
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return config_path

    def resolved_save_directory(self) -> Path:
        return Path(self.save_directory).expanduser().resolve()
