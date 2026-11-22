from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from config import PATHS
from core.pdf_reader import PdfInfo
from services.comparison_service import (
    ComparableMap,
    compare_maps,
    prepare_comparable,
)
from utils.logging_utils import get_logger

logger = get_logger("comparison_worker")


class ComparisonWorker(QThread):
    """Prepares both maps and aligns them without blocking the UI."""

    stage = Signal(str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        reference: PdfInfo,
        candidate: PdfInfo,
        cache_dir=PATHS.features,
        ignore_parcel: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._reference = reference
        self._candidate = candidate
        self._cache_dir = cache_dir
        self._ignore_parcel = ignore_parcel
        self._maps: tuple[ComparableMap, ComparableMap] | None = None

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            self.stage.emit(f"Menyiapkan peta {self._reference.filename}…")
            reference = prepare_comparable(self._reference, cache_dir=self._cache_dir)
            if self.isInterruptionRequested():
                return

            self.stage.emit(f"Menyiapkan peta {self._candidate.filename}…")
            candidate = prepare_comparable(self._candidate, cache_dir=self._cache_dir)
            if self.isInterruptionRequested():
                return

            self._maps = (reference, candidate)
            self.stage.emit("Mencocokkan fitur dan menyelaraskan peta…")
            result = compare_maps(
                reference, candidate, ignore_parcel=self._ignore_parcel
            )
        except Exception as exc:  # noqa: BLE001 - report instead of crashing the app
            logger.exception("Perbandingan gagal")
            self.failed.emit(str(exc))
            return

        self.finished_ok.emit(result)
