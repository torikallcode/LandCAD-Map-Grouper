from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

try:  # PyMuPDF >= 1.24 prefers the ``pymupdf`` name; ``fitz`` is a legacy alias.
    import pymupdf as fitz
except ImportError:  # pragma: no cover - depends on installed PyMuPDF build
    import fitz  # type: ignore[no-redef]

from config import MAX_PREVIEW_DIMENSION
from utils.logging_utils import get_logger

logger = get_logger("pdf_reader")

PDF_POINTS_PER_INCH = 72.0
_PDF_HEADER = b"%PDF-"
_HEADER_PROBE_BYTES = 1024


class DocumentStatus(str, Enum):
    OK = "ok"
    CORRUPT = "corrupt"
    ENCRYPTED = "encrypted"
    EMPTY = "empty"
    MISSING = "missing"
    UNREADABLE = "unreadable"

    @property
    def label(self) -> str:
        return _STATUS_LABELS[self]


_STATUS_LABELS = {
    DocumentStatus.OK: "Valid",
    DocumentStatus.CORRUPT: "Rusak",
    DocumentStatus.ENCRYPTED: "Terkunci",
    DocumentStatus.EMPTY: "Tanpa halaman",
    DocumentStatus.MISSING: "Tidak ditemukan",
    DocumentStatus.UNREADABLE: "Tidak dapat dibaca",
}


@dataclass(frozen=True)
class PdfInfo:
    path: Path
    filename: str
    size_bytes: int
    modified_at: float
    page_count: int
    status: DocumentStatus
    message: str = ""
    is_encrypted: bool = False
    title: str = ""

    @property
    def is_valid(self) -> bool:
        return self.status is DocumentStatus.OK

    @property
    def status_label(self) -> str:
        return self.status.label

    @property
    def error_message(self) -> str:
        if self.is_valid:
            return ""
        return self.message or _DEFAULT_MESSAGES.get(self.status, "")

    @property
    def key(self) -> str:
        return os.path.normcase(str(self.path.resolve()))


_DEFAULT_MESSAGES = {
    DocumentStatus.CORRUPT: "Struktur PDF tidak valid atau file terpotong.",
    DocumentStatus.ENCRYPTED: "PDF dilindungi kata sandi.",
    DocumentStatus.EMPTY: "PDF tidak memiliki halaman.",
    DocumentStatus.MISSING: "File tidak ditemukan di lokasi awal.",
    DocumentStatus.UNREADABLE: "File tidak dapat dibaca. Periksa izin akses.",
}


@dataclass(frozen=True)
class RenderedPage:
    page_index: int
    page_count: int
    width: int
    height: int
    dpi: int
    png_bytes: bytes = field(repr=False)

    @property
    def page_number(self) -> int:
        return self.page_index + 1


class PdfReadError(Exception):
    """Raised when a PDF cannot be opened or a page cannot be rendered."""


def open_document(path: Path) -> "PdfDocument":
    return PdfDocument(path)


class PdfDocument:
    """Read-only wrapper around a PyMuPDF document.

    The original file is never modified: the document is opened for reading only
    and every render is done in memory.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._doc = None

    def __enter__(self) -> "PdfDocument":
        self._open()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def _open(self) -> None:
        if self._doc is not None:
            return
        try:
            self._doc = fitz.open(self.path)
        except Exception as exc:  # noqa: BLE001 - PyMuPDF raises several types
            raise PdfReadError(f"Gagal membuka PDF: {exc}") from exc

        if self._doc.needs_pass and not self._doc.authenticate(""):
            self.close()
            raise PdfReadError("PDF dilindungi kata sandi.")

    @property
    def page_count(self) -> int:
        if self._doc is None:
            raise PdfReadError("Dokumen belum dibuka.")
        return self._doc.page_count

    def page_size_points(self, page_index: int) -> tuple[float, float]:
        page = self._page(page_index)
        return float(page.rect.width), float(page.rect.height)

    def render_page(
        self,
        page_index: int,
        dpi: int,
        max_dimension: int = MAX_PREVIEW_DIMENSION,
    ) -> RenderedPage:
        page = self._page(page_index)
        effective_dpi = self._clamped_dpi(page, dpi, max_dimension)
        scale = effective_dpi / PDF_POINTS_PER_INCH
        try:
            pixmap = page.get_pixmap(
                matrix=fitz.Matrix(scale, scale),
                alpha=False,
                colorspace=fitz.csRGB,
            )
        except Exception as exc:  # noqa: BLE001 - rendering failures vary
            raise PdfReadError(f"Gagal merender halaman {page_index + 1}: {exc}") from exc

        return RenderedPage(
            page_index=page_index,
            page_count=self.page_count,
            width=pixmap.width,
            height=pixmap.height,
            dpi=effective_dpi,
            png_bytes=pixmap.tobytes("png"),
        )

    def close(self) -> None:
        if self._doc is not None:
            try:
                self._doc.close()
            finally:
                self._doc = None

    def _page(self, page_index: int):
        self._open()
        assert self._doc is not None
        if not 0 <= page_index < self._doc.page_count:
            raise PdfReadError(
                f"Indeks halaman {page_index} di luar rentang "
                f"(0-{max(self._doc.page_count - 1, 0)})."
            )
        return self._doc.load_page(page_index)

    @staticmethod
    def _clamped_dpi(page, dpi: int, max_dimension: int) -> int:
        width_in = page.rect.width / PDF_POINTS_PER_INCH
        height_in = page.rect.height / PDF_POINTS_PER_INCH
        longest_side = max(width_in, height_in, 1e-6)
        if max_dimension > 0:
            dpi = min(dpi, int(max_dimension / longest_side))
        return max(1, dpi)


def inspect_pdf(path: Path) -> PdfInfo:
    """Validate a PDF and collect basic metadata without rendering anything."""
    path = Path(path)
    if not path.exists():
        return _error_info(path, 0, 0.0, DocumentStatus.MISSING)
    try:
        stat = path.stat()
    except OSError as exc:
        logger.warning("Tidak dapat membaca stat %s: %s", path, exc)
        return _error_info(path, 0, 0.0, DocumentStatus.UNREADABLE, str(exc))

    if not _has_pdf_signature(path):
        logger.warning("File bukan PDF (tanda tangan tidak cocok): %s", path.name)
        return _info(
            path,
            stat,
            0,
            DocumentStatus.CORRUPT,
            "File tidak memiliki tanda tangan PDF yang valid.",
        )

    if stat.st_size < 64:
        return _info(
            path, stat, 0, DocumentStatus.CORRUPT, "File terlalu kecil untuk menjadi PDF."
        )

    try:
        with fitz.open(path) as doc:
            if doc.needs_pass and not doc.authenticate(""):
                return _info(
                    path,
                    stat,
                    page_count=doc.page_count,
                    status=DocumentStatus.ENCRYPTED,
                    message=_DEFAULT_MESSAGES[DocumentStatus.ENCRYPTED],
                    is_encrypted=True,
                )
            page_count = doc.page_count
            title = (doc.metadata or {}).get("title", "") or ""
    except fitz.FileDataError as exc:
        logger.warning("PDF rusak %s: %s", path.name, exc)
        return _info(
            path, stat, 0, DocumentStatus.CORRUPT, f"Struktur PDF tidak valid: {exc}"
        )
    except Exception as exc:  # noqa: BLE001 - corrupt files raise varied errors
        logger.warning("Gagal memeriksa %s: %s", path.name, exc)
        return _info(path, stat, 0, DocumentStatus.CORRUPT, f"Gagal membaca PDF: {exc}")

    if page_count <= 0:
        return _info(
            path,
            stat,
            0,
            DocumentStatus.EMPTY,
            _DEFAULT_MESSAGES[DocumentStatus.EMPTY],
        )

    return _info(path, stat, page_count, DocumentStatus.OK, title=title)


def validate_pdf(path: Path) -> tuple[bool, str]:
    info = inspect_pdf(path)
    return info.is_valid, info.error_message


def _has_pdf_signature(path: Path) -> bool:
    """PDF readers tolerate junk before the header, so probe the first KB."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(_HEADER_PROBE_BYTES)
    except OSError:
        return False
    return _PDF_HEADER in head


def _info(
    path: Path,
    stat: os.stat_result,
    page_count: int,
    status: DocumentStatus,
    message: str = "",
    is_encrypted: bool = False,
    title: str = "",
) -> PdfInfo:
    return PdfInfo(
        path=path,
        filename=path.name,
        size_bytes=stat.st_size,
        modified_at=stat.st_mtime,
        page_count=page_count,
        status=status,
        message=message,
        is_encrypted=is_encrypted,
        title=title,
    )


def _error_info(
    path: Path,
    size_bytes: int,
    modified_at: float,
    status: DocumentStatus,
    message: str = "",
) -> PdfInfo:
    return PdfInfo(
        path=path,
        filename=path.name,
        size_bytes=size_bytes,
        modified_at=modified_at,
        page_count=0,
        status=status,
        message=message,
    )
