from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from core.pdf_reader import open_document
from utils.logging_utils import get_logger

logger = get_logger("render_worker")


class RenderSignals(QObject):
    finished = Signal(int, bytes, int, int, int)
    failed = Signal(int, str)


class RenderTask(QRunnable):
    """Renders one PDF page in a worker pool thread."""

    def __init__(self, token: int, path: Path, page_index: int, dpi: int) -> None:
        super().__init__()
        self.token = token
        self.signals = RenderSignals()
        self._path = Path(path)
        self._page_index = page_index
        self._dpi = dpi
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: D401 - QRunnable entry point
        try:
            with open_document(self._path) as document:
                page = document.render_page(self._page_index, self._dpi)
        except Exception as exc:  # noqa: BLE001 - surface render errors in the UI
            logger.warning(
                "Gagal merender %s halaman %d: %s",
                self._path.name,
                self._page_index + 1,
                exc,
            )
            self.signals.failed.emit(self.token, str(exc))
            return
        self.signals.finished.emit(
            self.token, page.png_bytes, page.width, page.height, page.dpi
        )
