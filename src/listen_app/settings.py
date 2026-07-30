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
    video_google_meet_mode: bool = False
    language: str | None = None  # legado; não usar para gravar — ver migração em load()
    whisper_force_language: str | None = None
    translation_target: str | None = "pt-br"
    meeting_ai_summary: bool = False
    cursor_mode: bool = False
    cursor_model: str | None = None
    cursor_cli: str = "cursor"
    openrouter_api_key: str | None = None
    openrouter_model: str = "openai/gpt-4o-mini"
    openai_base_url: str | None = "https://openrouter.ai/api/v1"
    # Legado (CLI antigo); openrouter_model tem prioridade na GUI
    openai_model: str = "openai/gpt-4o-mini"

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
        if filtered.get("openai_base_url") == "":
            filtered["openai_base_url"] = None
        if filtered.get("translation_target") == "":
            filtered["translation_target"] = None
        if filtered.get("whisper_force_language") == "":
            filtered["whisper_force_language"] = None
        if filtered.get("language") == "":
            filtered["language"] = None
        # Config antiga: "language":"pt" forçava o Whisper (gravava em PT).
        # Passa a ser só alvo de tradução; gravação detecta idioma falado.
        if "translation_target" not in data:
            legacy = (filtered.get("language") or "").strip()
            if legacy:
                filtered["translation_target"] = legacy
            filtered["language"] = None
        if filtered.get("cursor_model") == "":
            filtered["cursor_model"] = None
        if filtered.get("cursor_cli") == "":
            filtered["cursor_cli"] = "cursor"
        if filtered.get("openrouter_api_key") == "":
            filtered["openrouter_api_key"] = None
        else:
            from .meeting_summary_ai import sanitize_api_key

            filtered["openrouter_api_key"] = sanitize_api_key(
                filtered.get("openrouter_api_key")
            )
        if not filtered.get("openrouter_model") and filtered.get("openai_model"):
            filtered["openrouter_model"] = filtered["openai_model"]
        migrated_legacy_language = (
            "translation_target" not in data and bool((data.get("language") or "").strip())
        )
        settings = cls(**filtered)
        if migrated_legacy_language:
            try:
                settings.save(config_path)
            except OSError:
                pass
        return settings

    def resolved_ai_model(self) -> str:
        return (self.openrouter_model or self.openai_model or "openai/gpt-4o-mini").strip()

    def resolved_ai_provider(self) -> str:
        """``cursor`` se modo Cursor activo; caso contrário ``openrouter``."""
        return "cursor" if self.cursor_mode else "openrouter"

    def resolved_meeting_ai_enabled(self) -> bool:
        """Índice por IA: switch explícito ou modo Cursor (implica IA no indice)."""
        return bool(self.meeting_ai_summary or self.cursor_mode)

    def resolved_translation_target(self) -> str | None:
        """Idioma para tradução abaixo da transcrição (ex.: pt-br). Vazio = sem tradução."""
        t = self.translation_target
        if t is None:
            return None
        t = str(t).strip()
        return t or None

    def resolved_whisper_force_language(self) -> str | None:
        """Força idioma do Whisper (vídeo/análise). Vazio = detectar automaticamente."""
        w = self.whisper_force_language
        if w is not None and str(w).strip():
            return str(w).strip()
        return None

    def resolved_ai_base_url(self) -> str:
        base = self.openai_base_url
        if base and str(base).strip():
            return str(base).strip().rstrip("/")
        return "https://openrouter.ai/api/v1"

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
