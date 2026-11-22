from __future__ import annotations

import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow
from config import APP_NAME, APP_VERSION, LOG_LEVEL, PATHS
from utils.logging_utils import configure_logging, get_logger

_STYLESHEET = """
QMainWindow, QWidget { background-color: #f5f6f8; }
QLabel#sectionTitle { font-size: 13px; font-weight: 600; color: #1f2430; }
QLabel#folderLabel { color: #4a5262; }
QLabel#emptyHint { color: #7a8290; padding: 16px; }
QLabel#detailLabel { color: #3c4353; }
QLabel#previewTitle { font-size: 14px; font-weight: 600; color: #1f2430; }
QLabel#previewStatus { color: #5a6274; }
QLabel#previewAnalysis { color: #1f6feb; }
QLabel#pageInfo { color: #6b7385; }
QLabel#legendLabel { color: #6b7385; font-size: 11px; }
QTabWidget::pane { border: 1px solid #dfe3ea; border-radius: 8px; background: #f5f6f8; }
QTabBar::tab {
    background: #eceff4;
    border: 1px solid #dfe3ea;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 7px 16px;
    margin-right: 2px;
    color: #4a5262;
}
QTabBar::tab:selected { background: #ffffff; color: #1f2430; font-weight: 600; }
QTabBar::tab:disabled { color: #a6adba; }
QComboBox {
    background-color: #ffffff;
    border: 1px solid #cfd5df;
    border-radius: 6px;
    padding: 5px 8px;
}
QFrame#statCard {
    background-color: #ffffff;
    border: 1px solid #dfe3ea;
    border-radius: 8px;
}
QLabel#statValue { font-size: 20px; font-weight: 700; color: #1f2430; }
QLabel#statTitle { font-size: 11px; color: #6b7385; }
QTableView {
    background-color: #ffffff;
    border: 1px solid #dfe3ea;
    border-radius: 8px;
    gridline-color: #eceff4;
    selection-background-color: #dbe8ff;
    selection-color: #14243d;
}
QTableView::item:selected { background-color: #dbe8ff; color: #14243d; }
QTableView::item:selected:!active { background-color: #e6eefc; color: #14243d; }
QTreeView, QTreeWidget {
    background-color: #ffffff;
    border: 1px solid #dfe3ea;
    border-radius: 8px;
    alternate-background-color: #fafbfd;
}
QTreeView::item:selected, QTreeWidget::item:selected {
    background-color: #dbe8ff;
    color: #14243d;
}
QTreeView::item:selected:!active, QTreeWidget::item:selected:!active {
    background-color: #e6eefc;
    color: #14243d;
}
QTreeView::item:hover, QTreeWidget::item:hover {
    background-color: #eef2f9;
}
QHeaderView::section {
    background-color: #f0f2f6;
    border: none;
    border-bottom: 1px solid #dfe3ea;
    padding: 6px 8px;
    font-weight: 600;
    color: #3a4150;
}
QPushButton {
    background-color: #ffffff;
    border: 1px solid #cfd5df;
    border-radius: 6px;
    padding: 7px 12px;
}
QPushButton:hover { background-color: #eef2f9; }
QPushButton:checked {
    background-color: #2f6fd0;
    border-color: #2f6fd0;
    color: #ffffff;
}
QPushButton:checked:hover { background-color: #3a7cdd; }
QPushButton:disabled { color: #a6adba; }
QToolButton {
    background-color: #ffffff;
    border: 1px solid #cfd5df;
    border-radius: 6px;
    padding: 4px 8px;
    min-width: 22px;
}
QToolButton:hover { background-color: #eef2f9; }
QToolButton:disabled { color: #a6adba; }
QLineEdit {
    background-color: #ffffff;
    border: 1px solid #cfd5df;
    border-radius: 6px;
    padding: 6px 8px;
}
QSplitter::handle { background-color: #e3e7ee; }
QProgressBar {
    background-color: #eef0f4;
    border: 1px solid #dfe3ea;
    border-radius: 6px;
    height: 14px;
    text-align: center;
}
QProgressBar::chunk { background-color: #2f6fd0; border-radius: 5px; }
"""


def _apply_palette(app: QApplication) -> None:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#f5f6f8"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#1f2430"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#1f2430"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#2f6fd0"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)


def create_application(argv: list[str] | None = None) -> QApplication:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    _apply_palette(app)
    app.setStyleSheet(_STYLESHEET)
    return app


def main() -> int:
    PATHS.ensure()
    configure_logging(PATHS.logs, level=LOG_LEVEL)
    logger = get_logger("main")
    logger.info("Memulai %s v%s", APP_NAME, APP_VERSION)

    app = create_application()
    window = MainWindow()
    window.show()
    exit_code = app.exec()
    logger.info("Aplikasi berhenti dengan kode %d", exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
