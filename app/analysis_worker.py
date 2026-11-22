from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from config import MAP_DETECTION_DPI
from core.map_page_detector import MapPageDetector
from core.pdf_reader import PdfInfo
from services.processing_service import analyse_documents
from utils.logging_utils import get_logger

logger = get_logger("analysis_worker")


class AnalysisWorker(QThread):
    """Runs map detection and normalization off the UI thread."""

    progress = Signal(int, int, str)
    document_done = Signal(object)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        documents: list[PdfInfo],
        detector: MapPageDetector | None = None,
        dpi: int = MAP_DETECTION_DPI,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._documents = list(documents)
        self._detector = detector or MapPageDetector()
        self._dpi = dpi

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            result = analyse_documents(
                self._documents,
                detector=self._detector,
                dpi=self._dpi,
                progress=self._emit_progress,
                should_cancel=self.isInterruptionRequested,
            )
        except Exception as exc:  # noqa: BLE001 - never let a worker crash silently
            logger.exception("Analisis dokumen gagal")
            self.failed.emit(str(exc))
            return

        for analysis in result.documents:
            self.document_done.emit(analysis)
        self.finished_ok.emit(result)

    def _emit_progress(self, current: int, total: int, name: str) -> None:
        self.progress.emit(current, total, name)
