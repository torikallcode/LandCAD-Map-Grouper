from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from config import PATHS
from core.clustering_engine import DEFAULT_CLUSTER_CONFIG, ClusterConfig
from core.pdf_reader import PdfInfo
from services.cache_service import AnalysisCache
from services.grouping_service import group_documents
from utils.logging_utils import get_logger

logger = get_logger("grouping_worker")


class GroupingWorker(QThread):
    """Runs similarity scoring and clustering off the UI thread."""

    stage = Signal(str)
    progress = Signal(int, int, str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        documents: list[PdfInfo],
        cache_dir=PATHS.features,
        analysis_cache: AnalysisCache | None = None,
        cluster_config: ClusterConfig = DEFAULT_CLUSTER_CONFIG,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._documents = list(documents)
        self._cache_dir = cache_dir
        self._analysis_cache = analysis_cache
        self._cluster_config = cluster_config

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            result = group_documents(
                self._documents,
                cache_dir=self._cache_dir,
                config=self._cluster_config,
                progress=self._on_progress,
                should_cancel=self.isInterruptionRequested,
                analysis_cache=self._analysis_cache,
            )
        except Exception as exc:  # noqa: BLE001 - surface errors instead of crashing
            logger.exception("Pengelompokan gagal")
            self.failed.emit(str(exc))
            return

        self.finished_ok.emit(result)

    def _on_progress(self, current: int, total: int, label: str) -> None:
        self.progress.emit(current, total, label)
