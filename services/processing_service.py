from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from config import MAP_DETECTION_DPI
from core.image_normalizer import (
    DEFAULT_CONFIG,
    NormalizationConfig,
    NormalizedImage,
    normalize_page,
)
from core.map_page_detector import (
    DETECTOR_VERSION,
    MapDetectionResult,
    MapPageDetector,
)
from core.pdf_reader import PdfInfo, open_document
from utils.image_utils import decode_png_gray, encode_png
from utils.logging_utils import get_logger

logger = get_logger("processing_service")

ProgressCallback = Callable[[int, int, str], None]
CancelCheck = Callable[[], bool]


@dataclass(frozen=True)
class DocumentAnalysis:
    """Map detection plus normalization outcome for one PDF."""

    info: PdfInfo
    detection: MapDetectionResult | None = None
    normalized: NormalizedImage | None = None
    error: str = ""

    @property
    def is_analysed(self) -> bool:
        return self.detection is not None

    @property
    def has_error(self) -> bool:
        return bool(self.error)

    @property
    def map_page_number(self) -> int | None:
        if self.detection is None:
            return None
        return self.detection.map_page_number

    @property
    def map_score(self) -> float:
        if self.detection is None or self.detection.best_page is None:
            return 0.0
        return self.detection.best_page.score

    @property
    def map_label(self) -> str:
        if self.has_error:
            return "Gagal"
        if self.detection is None:
            return "-"
        if self.detection.map_page_number is None:
            return "Tidak ditemukan"
        return f"Halaman {self.detection.map_page_number}"


@dataclass(frozen=True)
class AnalysisResult:
    documents: tuple[DocumentAnalysis, ...] = ()
    cancelled: bool = False

    @property
    def analysed_count(self) -> int:
        return sum(1 for doc in self.documents if doc.is_analysed)

    @property
    def map_found_count(self) -> int:
        return sum(
            1
            for doc in self.documents
            if doc.detection is not None and doc.detection.has_map
        )

    @property
    def error_count(self) -> int:
        return sum(1 for doc in self.documents if doc.has_error)


def render_pages(path: Path, dpi: int = MAP_DETECTION_DPI) -> list[np.ndarray]:
    """Render every page of a PDF to grayscale ndarrays."""
    pages: list[np.ndarray] = []
    with open_document(path) as document:
        for index in range(document.page_count):
            rendered = document.render_page(index, dpi=dpi)
            pages.append(decode_png_gray(rendered.png_bytes))
    return pages


def analyse_document(
    info: PdfInfo,
    detector: MapPageDetector,
    config: NormalizationConfig = DEFAULT_CONFIG,
    dpi: int = MAP_DETECTION_DPI,
) -> DocumentAnalysis:
    if not info.is_valid:
        return DocumentAnalysis(info=info, error=info.error_message)

    try:
        pages = render_pages(info.path, dpi=dpi)
    except Exception as exc:  # noqa: BLE001 - one bad file must not stop a batch
        logger.warning("Gagal merender %s: %s", info.filename, exc)
        return DocumentAnalysis(info=info, error=f"Gagal merender halaman: {exc}")

    if not pages:
        return DocumentAnalysis(info=info, error="Dokumen tidak memiliki halaman.")

    detection = detector.assess_pages(pages)

    normalized = None
    target_index = detection.map_page
    if target_index is None and detection.best_page is not None:
        target_index = detection.best_page.page_index
    if target_index is not None:
        try:
            normalized = normalize_page(pages[target_index], config)
        except Exception as exc:  # noqa: BLE001 - normalization is not fatal
            logger.warning("Gagal menormalisasi %s: %s", info.filename, exc)

    return DocumentAnalysis(info=info, detection=detection, normalized=normalized)


def analyse_documents(
    documents: list[PdfInfo],
    detector: MapPageDetector | None = None,
    config: NormalizationConfig = DEFAULT_CONFIG,
    dpi: int = MAP_DETECTION_DPI,
    progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> AnalysisResult:
    detector = detector or MapPageDetector()
    total = len(documents)
    results: list[DocumentAnalysis] = []

    logger.info("Memulai analisis %d dokumen", total)
    for index, info in enumerate(documents, start=1):
        if should_cancel is not None and should_cancel():
            logger.info("Analisis dibatalkan pada %d/%d", index - 1, total)
            return AnalysisResult(documents=tuple(results), cancelled=True)

        if progress is not None:
            progress(index, total, info.filename)

        results.append(analyse_document(info, detector, config, dpi))

    found = sum(1 for item in results if item.detection and item.detection.has_map)
    logger.info("Analisis selesai: %d dokumen, %d halaman peta ditemukan", total, found)
    return AnalysisResult(documents=tuple(results))


# --------------------------------------------------------------- export helpers
@dataclass(frozen=True)
class NormalizedExport:
    original_path: Path
    normalized_path: Path
    steps: tuple[str, ...] = field(default_factory=tuple)


def save_normalized(
    analysis: DocumentAnalysis,
    output_dir: Path,
    stem: str | None = None,
) -> NormalizedExport | None:
    """Write both the untouched render and the normalized image to disk."""
    if analysis.normalized is None:
        return None

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    base = stem or analysis.info.path.stem
    page = analysis.map_page_number or 0

    original_path = output_dir / f"{base}_p{page}_asli.png"
    normalized_path = output_dir / f"{base}_p{page}_normal.png"

    original_path.write_bytes(encode_png(analysis.normalized.original))
    normalized_path.write_bytes(encode_png(analysis.normalized.image))

    return NormalizedExport(
        original_path=original_path,
        normalized_path=normalized_path,
        steps=analysis.normalized.steps,
    )


def detection_summary(analysis: DocumentAnalysis) -> str:
    if analysis.detection is None:
        return analysis.error or "Belum dianalisis."
    lines = [
        f"Detektor: {DETECTOR_VERSION}",
        f"Halaman peta: {analysis.map_label}",
        f"Halaman kandidat: {analysis.detection.map_count}",
    ]
    for page in analysis.detection.pages:
        lines.append(
            f"  Halaman {page.page_number}: skor {page.score:.2f} "
            f"({'peta' if page.is_map else 'bukan peta'})"
        )
    normalized = analysis.normalized
    if normalized is not None:
        lines.append(f"Langkah normalisasi: {', '.join(normalized.steps)}")
        lines.append(
            f"Orientasi: {normalized.orientation.reason} "
            f"(rasio {normalized.orientation.ratio:.2f})"
        )
        lines.append(
            f"Kemiringan: {normalized.skew.angle:+.2f}° "
            f"(keyakinan {normalized.skew.confidence:.2f})"
        )
    return "\n".join(lines)


__all__ = [
    "AnalysisResult",
    "DocumentAnalysis",
    "NormalizedExport",
    "analyse_document",
    "analyse_documents",
    "detection_summary",
    "render_pages",
    "save_normalized",
]
