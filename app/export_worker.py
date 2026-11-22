from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from core.pdf_reader import PdfInfo
from core.review_model import ReviewState
from services.export_service import copy_documents_to_folders, export_report
from utils.logging_utils import get_logger

logger = get_logger("export_worker")


class ExportWorker(QThread):
    """Runs report writing and file copying off the UI thread.

    Copying several hundred PDFs on the UI thread froze the window; this worker
    keeps the app responsive and reports per-file progress.
    """

    progress = Signal(int, int, str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        state: ReviewState,
        documents: dict[str, PdfInfo],
        output_dir: Path,
        *,
        mode: str = "copy",
        fmt: str = "xlsx",
        overwrite: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._documents = dict(documents)
        self._output_dir = Path(output_dir)
        self._mode = mode
        self._fmt = fmt
        self._overwrite = overwrite

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            if self._mode == "report":
                result = export_report(
                    self._state, self._documents, self._output_dir, fmt=self._fmt
                )
            else:
                result = copy_documents_to_folders(
                    self._state,
                    self._documents,
                    self._output_dir,
                    overwrite=self._overwrite,
                    progress=self._emit_progress,
                )
        except Exception as exc:  # noqa: BLE001 - surface errors, never crash
            logger.exception("Ekspor gagal")
            self.failed.emit(str(exc))
            return

        self.finished_ok.emit(result)

    def _emit_progress(self, current: int, total: int, name: str) -> None:
        self.progress.emit(current, total, name)
