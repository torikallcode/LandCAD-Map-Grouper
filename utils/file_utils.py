from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

_NATURAL_CHUNK = re.compile(r"(\d+)")


def natural_sort_key(value: str | Path) -> tuple[object, ...]:
    name = Path(value).name if isinstance(value, Path) else value
    parts = _NATURAL_CHUNK.split(name.lower())
    return tuple(int(part) if part.isdigit() else part for part in parts)


def format_size(num_bytes: int | None) -> str:
    if num_bytes is None:
        return "-"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def format_timestamp(timestamp: float | None) -> str:
    if not timestamp:
        return "-"
    return datetime.fromtimestamp(timestamp).strftime("%d-%m-%Y %H:%M")


def is_pdf_file(path: Path, extensions: tuple[str, ...]) -> bool:
    return path.is_file() and path.suffix.lower() in extensions
