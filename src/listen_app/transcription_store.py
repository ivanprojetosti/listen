"""Save transcriptions to plain text files."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class SavedTranscription:
    path: Path
    created_at: datetime
    preview: str
    language: str | None = None


@dataclass
class ParsedTranscription:
    original: str
    language: str | None = None
    translation: str | None = None
    translation_language: str | None = None

    def display_text(self) -> str:
        from .transcription_format import format_transcription_display

        return format_transcription_display(
            self.original,
            language=self.language or "",
            translation=self.translation,
            translation_language=self.translation_language,
        )


def read_transcription_text(path: Path) -> str:
    """Read transcription body from a saved .txt file (original + tradução se houver)."""
    return parse_saved_transcription(path).display_text()


def parse_saved_transcription(path: Path) -> ParsedTranscription:
    """Separa original, metadados e tradução de um .txt guardado."""
    lines = path.read_text(encoding="utf-8").splitlines()
    language: str | None = None
    translation_language: str | None = None
    translation_lines: list[str] = []
    original_lines: list[str] = []
    in_translation = False

    for line in lines:
        if line.startswith("# language:"):
            language = line.split(":", 1)[1].strip() or None
            continue
        if line.startswith("# translation:"):
            translation_language = line.split(":", 1)[1].strip() or None
            in_translation = True
            continue
        if line.startswith("# "):
            continue
        if in_translation:
            translation_lines.append(line)
        else:
            original_lines.append(line)

    original = "\n".join(original_lines).strip()
    translation = "\n".join(translation_lines).strip() or None
    return ParsedTranscription(
        original=original,
        language=language,
        translation=translation,
        translation_language=translation_language,
    )


def list_saved_transcriptions(
    directory: Path,
    *,
    limit: int = 50,
) -> list[SavedTranscription]:
    """Return saved transcriptions ordered by most recent first."""
    if not directory.is_dir():
        return []

    files = sorted(
        directory.glob("listen_*.txt"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )[:limit]

    items: list[SavedTranscription] = []
    for path in files:
        try:
            created_at = datetime.fromtimestamp(path.stat().st_mtime)
            parsed = parse_saved_transcription(path)
            preview = parsed.original.replace("\n", " ")
            if len(preview) > 80:
                preview = preview[:77] + "..."

            items.append(
                SavedTranscription(
                    path=path,
                    created_at=created_at,
                    preview=preview or "(vazio)",
                    language=parsed.language,
                )
            )
        except OSError:
            continue

    return items


def save_transcription_text(
    text: str,
    directory: Path,
    *,
    language: str = "",
    translation: str | None = None,
    translation_language: str | None = None,
    meeting_mode: bool = False,
) -> Path:
    """Write one transcription to a timestamped .txt file."""
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = directory / f"listen_{timestamp}.txt"

    lines = [text.strip()]
    if translation and translation.strip():
        lines.append("")
        lines.append(f"# translation: {translation_language or '?'}")
        lines.append(translation.strip())
    if meeting_mode:
        lines.append("")
        lines.append("# mode: meeting")
    if language:
        lines.append("")
        lines.append(f"# language: {language}")

    filepath.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return filepath
