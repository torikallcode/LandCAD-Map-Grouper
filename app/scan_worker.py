from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from services.import_service import import_folder
from utils.logging_utils import get_logger

logger = get_logger("scan_worker")


class ScanWorker(QThread):
    """Validates every PDF inside a folder without blocking the UI thread."""

    progress = Signal(int, int, str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, folder: Path, recursive: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._folder = Path(folder)
        self._recursive = recursive

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            result = import_folder(
                self._folder,
                recursive=self._recursive,
                progress=self._emit_progress,
                should_cancel=self.isInterruptionRequested,
            )
        except Exception as exc:  # noqa: BLE001 - report to UI instead of crashing
            logger.exception("Pemindaian folder gagal: %s", self._folder)
            self.failed.emit(str(exc))
            return
        self.finished_ok.emit(result)

    def _emit_progress(self, current: int, total: int, name: str) -> None:
        self.progress.emit(current, total, name)
