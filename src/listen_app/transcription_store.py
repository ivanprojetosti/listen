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


def read_transcription_text(path: Path) -> str:
    """Read transcription body from a saved .txt file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    text_lines = [line for line in lines if not line.startswith("# language:")]
    return "\n".join(text_lines).strip()


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
            raw_lines = path.read_text(encoding="utf-8").splitlines()
            language: str | None = None
            text_lines: list[str] = []
            for line in raw_lines:
                if line.startswith("# language:"):
                    language = line.split(":", 1)[1].strip() or None
                else:
                    text_lines.append(line)

            text = "\n".join(text_lines).strip()
            preview = text.replace("\n", " ")
            if len(preview) > 80:
                preview = preview[:77] + "..."

            items.append(
                SavedTranscription(
                    path=path,
                    created_at=created_at,
                    preview=preview or "(vazio)",
                    language=language,
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
    meeting_mode: bool = False,
) -> Path:
    """Write one transcription to a timestamped .txt file."""
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = directory / f"listen_{timestamp}.txt"

    lines = [text]
    if meeting_mode:
        lines.append("")
        lines.append("# mode: meeting")
    if language:
        lines.append("")
        lines.append(f"# language: {language}")

    filepath.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return filepath
