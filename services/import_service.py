from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from config import SUPPORTED_EXTENSIONS
from core.pdf_reader import DocumentStatus, PdfInfo, inspect_pdf
from utils.file_utils import natural_sort_key
from utils.logging_utils import get_logger

logger = get_logger("import_service")

ProgressCallback = Callable[[int, int, str], None]
CancelCheck = Callable[[], bool]


@dataclass(frozen=True)
class ImportResult:
    documents: list[PdfInfo] = field(default_factory=list)
    cancelled: bool = False

    @property
    def total(self) -> int:
        return len(self.documents)

    @property
    def valid(self) -> list[PdfInfo]:
        return [doc for doc in self.documents if doc.is_valid]

    @property
    def invalid(self) -> list[PdfInfo]:
        return [doc for doc in self.documents if not doc.is_valid]

    @property
    def valid_count(self) -> int:
        return len(self.valid)

    @property
    def invalid_count(self) -> int:
        return len(self.invalid)

    @property
    def total_pages(self) -> int:
        return sum(doc.page_count for doc in self.valid)


def find_pdf_files(folder: Path, recursive: bool = False) -> list[Path]:
    """Return PDF paths in ``folder`` sorted naturally (SU_2 before SU_10)."""
    folder = Path(folder)
    if not folder.is_dir():
        raise NotADirectoryError(f"Folder tidak valid: {folder}")

    iterator = folder.rglob("*") if recursive else folder.glob("*")
    found = [
        path
        for path in iterator
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    return sorted(found, key=natural_sort_key)


def import_folder(
    folder: Path,
    recursive: bool = False,
    progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> ImportResult:
    """Scan a folder and validate every PDF found inside it."""
    paths = find_pdf_files(folder, recursive=recursive)
    logger.info("Menemukan %d file PDF di %s", len(paths), folder)
    return inspect_files(paths, progress=progress, should_cancel=should_cancel)


def inspect_files(
    paths: Iterable[Path],
    progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> ImportResult:
    """Validate an explicit list of PDF paths, continuing past failures."""
    unique: dict[str, Path] = {}
    for path in paths:
        path = Path(path)
        unique.setdefault(str(path.resolve()).lower(), path)

    ordered = sorted(unique.values(), key=natural_sort_key)
    total = len(ordered)
    documents: list[PdfInfo] = []

    for index, path in enumerate(ordered, start=1):
        if should_cancel is not None and should_cancel():
            logger.info("Import dibatalkan pada %d/%d file", index - 1, total)
            return ImportResult(documents=documents, cancelled=True)

        if progress is not None:
            progress(index, total, path.name)

        try:
            info = inspect_pdf(path)
        except Exception as exc:  # noqa: BLE001 - a single file must not stop a batch
            logger.exception("Kesalahan tak terduga saat memeriksa %s", path)
            info = _fallback_info(path, str(exc))
        documents.append(info)

    failed = sum(1 for doc in documents if not doc.is_valid)
    logger.info(
        "Validasi selesai: %d file, %d valid, %d gagal",
        total,
        total - failed,
        failed,
    )
    return ImportResult(documents=documents)


def summarize(documents: Sequence[PdfInfo]) -> dict[str, int]:
    total = len(documents)
    valid = sum(1 for doc in documents if doc.is_valid)
    return {
        "total": total,
        "valid": valid,
        "invalid": total - valid,
    }


def _fallback_info(path: Path, message: str) -> PdfInfo:
    try:
        stat = path.stat()
        size, modified = stat.st_size, stat.st_mtime
    except OSError:
        size, modified = 0, 0.0
    return PdfInfo(
        path=path,
        filename=path.name,
        size_bytes=size,
        modified_at=modified,
        page_count=0,
        status=DocumentStatus.UNREADABLE,
        message=message,
    )
