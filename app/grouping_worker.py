from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from config import PATHS
from core.pdf_reader import PdfInfo
from services.grouping_service import group_documents
from utils.logging_utils import get_logger

logger = get_logger("grouping_worker")


class GroupingWorker(QThread):
    """Runs similarity scoring and clustering off the UI thread."""

    stage = Signal(str)
    progress = Signal(int, int, str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, documents: list[PdfInfo], cache_dir=PATHS.features, parent=None) -> None:
        super().__init__(parent)
        self._documents = list(documents)
        self._cache_dir = cache_dir

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            result = group_documents(
                self._documents,
                cache_dir=self._cache_dir,
                progress=self._on_progress,
                should_cancel=self.isInterruptionRequested,
            )
        except Exception as exc:  # noqa: BLE001 - surface errors instead of crashing
            logger.exception("Pengelompokan gagal")
            self.failed.emit(str(exc))
            return

        self.finished_ok.emit(result)

    def _on_progress(self, current: int, total: int, label: str) -> None:
        self.progress.emit(current, total, label)
