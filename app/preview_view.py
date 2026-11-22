from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.render_worker import RenderTask
from config import (
    DEFAULT_PREVIEW_DPI,
    MAX_PREVIEW_DIMENSION,
    MAX_ZOOM,
    MIN_ZOOM,
)
from core.pdf_reader import PdfInfo
from services.processing_service import DocumentAnalysis
from utils.image_utils import encode_png
from utils.logging_utils import get_logger

logger = get_logger("preview_view")

_PLACEHOLDER_TEXT = "Pilih dokumen untuk menampilkan pratinjau halaman."
_ZOOM_STEP = 1.25
_MODE_ASLI = "asli"
_MODE_NORMAL = "normal"


class PreviewPanel(QWidget):
    """Page preview with navigation, zoom and optional normalized view."""

    page_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._document: PdfInfo | None = None
        self._analysis: DocumentAnalysis | None = None
        self._page_index = 0
        self._dpi = DEFAULT_PREVIEW_DPI
        self._zoom = 1.0
        self._fit_to_width = True
        self._pixmap: QPixmap | None = None
        self._render_dpi = DEFAULT_PREVIEW_DPI
        self._render_token = 0
        self._mode = _MODE_ASLI
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)

        self._build_ui()
        self._set_controls_enabled(False)
        self._show_placeholder(_PLACEHOLDER_TEXT)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._title_label = QLabel("Belum ada dokumen")
        self._title_label.setObjectName("previewTitle")
        self._title_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        self._status_label = QLabel("")
        self._status_label.setObjectName("previewStatus")
        self._status_label.setWordWrap(True)

        self._analysis_label = QLabel("")
        self._analysis_label.setObjectName("previewAnalysis")
        self._analysis_label.setWordWrap(True)

        header = QVBoxLayout()
        header.setSpacing(2)
        header.addWidget(self._title_label)
        header.addWidget(self._status_label)
        header.addWidget(self._analysis_label)
        layout.addLayout(header)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setFrameShape(QFrame.Shape.StyledPanel)
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._scroll.setWidget(self._image_label)
        layout.addWidget(self._scroll, 1)

        layout.addWidget(self._build_toolbar())

    def _build_toolbar(self) -> QWidget:
        """Two compact rows so the panel minimum width stays small.

        A single row of controls demanded ~850px, which starved the document
        list in the splitter. Splitting navigation from view controls keeps the
        preview's minimum width near 350px.
        """
        container = QWidget()
        rows = QVBoxLayout(container)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(6)

        nav = QHBoxLayout()
        nav.setSpacing(6)

        self._first_button = QToolButton()
        self._first_button.setText("|<")
        self._first_button.setToolTip("Halaman pertama")
        self._first_button.clicked.connect(lambda: self.go_to_page(0))

        self._prev_button = QToolButton()
        self._prev_button.setText("‹")
        self._prev_button.setToolTip("Halaman sebelumnya")
        self._prev_button.clicked.connect(lambda: self.step_page(-1))

        self._page_spin = QSpinBox()
        self._page_spin.setMinimum(1)
        self._page_spin.setMaximum(1)
        self._page_spin.setFixedWidth(60)
        self._page_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_spin.setToolTip("Nomor halaman")
        self._page_spin.valueChanged.connect(self._on_spin_changed)

        self._page_total_label = QLabel("/ 1")
        self._page_total_label.setMinimumWidth(36)

        self._next_button = QToolButton()
        self._next_button.setText("›")
        self._next_button.setToolTip("Halaman berikutnya")
        self._next_button.clicked.connect(lambda: self.step_page(1))

        self._last_button = QToolButton()
        self._last_button.setText(">|")
        self._last_button.setToolTip("Halaman terakhir")
        self._last_button.clicked.connect(lambda: self.go_to_page(self._last_index()))

        for widget in (
            self._first_button,
            self._prev_button,
            self._page_spin,
            self._page_total_label,
            self._next_button,
            self._last_button,
        ):
            nav.addWidget(widget)
        nav.addStretch(1)

        self._page_info_label = QLabel("")
        self._page_info_label.setObjectName("pageInfo")
        nav.addWidget(self._page_info_label)
        rows.addLayout(nav)

        view = QHBoxLayout()
        view.setSpacing(6)

        self._asli_button = QPushButton("Asli")
        self._asli_button.setCheckable(True)
        self._asli_button.setChecked(True)
        self._asli_button.setToolTip("Tampilkan halaman PDF apa adanya")
        self._asli_button.clicked.connect(lambda: self._set_mode(_MODE_ASLI))

        self._normal_button = QPushButton("Normalisasi")
        self._normal_button.setCheckable(True)
        self._normal_button.setToolTip(
            "Tampilkan halaman peta setelah grayscale, kontras, denoise, rotasi, dan deskew"
        )
        self._normal_button.clicked.connect(lambda: self._set_mode(_MODE_NORMAL))

        view.addWidget(self._asli_button)
        view.addWidget(self._normal_button)
        view.addSpacing(10)

        self._fit_button = QPushButton("Lebar")
        self._fit_button.setCheckable(True)
        self._fit_button.setChecked(True)
        self._fit_button.setToolTip("Sesuaikan halaman dengan lebar panel")
        self._fit_button.toggled.connect(self._on_fit_toggled)
        view.addWidget(self._fit_button)

        self._zoom_out_button = QToolButton()
        self._zoom_out_button.setText("−")
        self._zoom_out_button.setToolTip("Perkecil")
        self._zoom_out_button.clicked.connect(
            lambda: self._apply_zoom(self._zoom / _ZOOM_STEP)
        )

        self._zoom_label = QLabel("100%")
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._zoom_label.setMinimumWidth(44)

        self._zoom_in_button = QToolButton()
        self._zoom_in_button.setText("+")
        self._zoom_in_button.setToolTip("Perbesar")
        self._zoom_in_button.clicked.connect(
            lambda: self._apply_zoom(self._zoom * _ZOOM_STEP)
        )

        view.addWidget(self._zoom_out_button)
        view.addWidget(self._zoom_label)
        view.addWidget(self._zoom_in_button)
        view.addStretch(1)
        rows.addLayout(view)
        return container

    # ------------------------------------------------------------- documents
    def show_document(
        self,
        document: PdfInfo,
        page_index: int = 0,
        analysis: DocumentAnalysis | None = None,
    ) -> None:
        self._document = document
        self._analysis = analysis
        self._pixmap = None
        self._page_index = 0
        self._zoom = 1.0
        self._fit_to_width = True
        self._fit_button.setChecked(True)
        self._mode = _MODE_ASLI
        self._asli_button.setChecked(True)
        self._normal_button.setChecked(False)

        self._title_label.setText(document.filename)
        self._title_label.setToolTip(str(document.path))
        self._status_label.setText(self._describe_status(document))
        self._analysis_label.setText(self._describe_analysis(analysis))

        if not document.is_valid:
            self._configure_pages(1)
            self._set_controls_enabled(False)
            self._show_placeholder(
                f"Dokumen tidak dapat ditampilkan.\n{document.error_message}"
            )
            return

        self._configure_pages(document.page_count)
        self._set_controls_enabled(True)
        self._normal_button.setEnabled(self._can_show_normalized())

        if page_index == 0 and analysis is not None and analysis.map_page_number:
            page_index = analysis.map_page_number - 1
        self.go_to_page(page_index, force=True)

    def clear(self) -> None:
        self._render_token += 1
        self._document = None
        self._analysis = None
        self._pixmap = None
        self._page_index = 0
        self._title_label.setText("Belum ada dokumen")
        self._title_label.setToolTip("")
        self._status_label.setText("")
        self._analysis_label.setText("")
        self._page_info_label.setText("")
        self._configure_pages(1)
        self._set_controls_enabled(False)
        self._show_placeholder(_PLACEHOLDER_TEXT)

    def current_document(self) -> PdfInfo | None:
        return self._document

    def current_page(self) -> int:
        return self._page_index

    def current_mode(self) -> str:
        return self._mode

    # ----------------------------------------------------------- navigation
    def go_to_page(self, page_index: int, force: bool = False) -> None:
        if self._document is None or not self._document.is_valid:
            return
        page_index = max(0, min(page_index, self._last_index()))
        if page_index == self._page_index and not force:
            return

        self._page_index = page_index
        self._update_page_widgets()
        self._request_render()
        self.page_changed.emit(page_index)

    def step_page(self, delta: int) -> None:
        self.go_to_page(self._page_index + delta)

    def zoom_in(self) -> None:
        self._apply_zoom(self._zoom * _ZOOM_STEP)

    def zoom_out(self) -> None:
        self._apply_zoom(self._zoom / _ZOOM_STEP)

    def reset_zoom(self) -> None:
        self._fit_button.setChecked(True)
        if not self._fit_to_width:
            self._on_fit_toggled(True)

    # ------------------------------------------------------------ internals
    def _last_index(self) -> int:
        if self._document is None:
            return 0
        return max(0, self._document.page_count - 1)

    def _can_show_normalized(self) -> bool:
        analysis = self._analysis
        return (
            analysis is not None
            and analysis.normalized is not None
            and analysis.map_page_number == self._page_index + 1
        )

    def _set_mode(self, mode: str) -> None:
        if mode == _MODE_NORMAL and not self._can_show_normalized():
            self._asli_button.setChecked(True)
            self._normal_button.setChecked(False)
            return

        self._mode = mode
        self._asli_button.setChecked(mode == _MODE_ASLI)
        self._normal_button.setChecked(mode == _MODE_NORMAL)

        if mode == _MODE_NORMAL:
            self._show_normalized()
        else:
            self._request_render()

    def _show_normalized(self) -> None:
        analysis = self._analysis
        if analysis is None or analysis.normalized is None:
            return
        self._render_token += 1
        png = encode_png(analysis.normalized.image)
        self._on_render_finished(
            self._render_token,
            png,
            analysis.normalized.image.shape[1],
            analysis.normalized.image.shape[0],
            0,
        )

    def _configure_pages(self, page_count: int) -> None:
        count = max(1, page_count)
        self._page_spin.blockSignals(True)
        self._page_spin.setMaximum(count)
        self._page_spin.setValue(self._page_index + 1)
        self._page_spin.blockSignals(False)
        self._page_total_label.setText(f"/ {count}")
        self._update_page_widgets()

    def _update_page_widgets(self) -> None:
        page_count = self._last_index() + 1
        self._page_spin.blockSignals(True)
        self._page_spin.setValue(self._page_index + 1)
        self._page_spin.blockSignals(False)

        self._first_button.setEnabled(self._page_index > 0)
        self._prev_button.setEnabled(self._page_index > 0)
        self._next_button.setEnabled(self._page_index < page_count - 1)
        self._last_button.setEnabled(self._page_index < page_count - 1)
        self._page_info_label.setText(f"Halaman {self._page_index + 1} dari {page_count}")

        if self._analysis is not None and self._analysis.detection is not None:
            assessment = self._analysis.detection.assessment(self._page_index)
            if assessment is not None:
                self._page_info_label.setText(
                    f"Halaman {self._page_index + 1} dari {page_count} "
                    f"• skor {assessment.score:.2f}"
                )

        self._normal_button.setEnabled(self._can_show_normalized())
        if self._mode == _MODE_NORMAL and not self._can_show_normalized():
            self._set_mode(_MODE_ASLI)

    def _set_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self._first_button,
            self._prev_button,
            self._next_button,
            self._last_button,
            self._page_spin,
            self._fit_button,
            self._zoom_in_button,
            self._zoom_out_button,
            self._asli_button,
            self._normal_button,
        ):
            widget.setEnabled(enabled)

    def _on_spin_changed(self, value: int) -> None:
        if value - 1 != self._page_index:
            self.go_to_page(value - 1)

    def _on_fit_toggled(self, checked: bool) -> None:
        self._fit_to_width = checked
        self._fit_button.setToolTip(
            "Sesuaikan halaman dengan lebar panel" if checked else "Ukuran asli (100%)"
        )
        if self._pixmap is not None:
            self._display_pixmap()

    def _describe_status(self, document: PdfInfo) -> str:
        parts = [f"Halaman: {document.page_count}", f"Status: {document.status_label}"]
        if not document.is_valid:
            parts.append(document.error_message)
        return "  •  ".join(part for part in parts if part)

    def _describe_analysis(self, analysis: DocumentAnalysis | None) -> str:
        if analysis is None:
            return "Belum dianalisis. Klik “Deteksi Halaman Peta” untuk menganalisis."
        if analysis.has_error:
            return f"Analisis gagal: {analysis.error}"
        if not analysis.is_analysed:
            return "Belum dianalisis."

        parts = [f"Halaman peta: {analysis.map_label}"]
        if analysis.normalized is not None:
            orientation = analysis.normalized.orientation
            skew = analysis.normalized.skew
            if orientation.quarter_turns:
                parts.append("rotasi 90° diterapkan")
            if any(step.startswith("deskew") for step in analysis.normalized.steps):
                parts.append(f"deskew {skew.angle:+.2f}°")
            else:
                parts.append("tanpa deskew")
        return "  •  ".join(parts)

    def _show_placeholder(self, text: str) -> None:
        self._image_label.setPixmap(QPixmap())
        self._image_label.setText(text)
        self._image_label.setStyleSheet("color: #7a7a7a; padding: 24px;")
        self._image_label.resize(self._scroll.viewport().size())

    def _request_render(self) -> None:
        if self._document is None:
            return
        self._render_token += 1
        self._image_label.setText("Memuat halaman…")
        self._image_label.setStyleSheet("color: #7a7a7a;")

        task = RenderTask(
            token=self._render_token,
            path=Path(self._document.path),
            page_index=self._page_index,
            dpi=self._dpi,
        )
        task.signals.finished.connect(self._on_render_finished)
        task.signals.failed.connect(self._on_render_failed)
        self._pool.start(task)

    def _on_render_finished(
        self, token: int, png_bytes: bytes, width: int, height: int, dpi: int
    ) -> None:
        if token != self._render_token:
            return
        image = QImage.fromData(png_bytes, "PNG")
        if image.isNull():
            self._on_render_failed(token, "Gambar halaman tidak dapat ditampilkan.")
            return
        self._pixmap = QPixmap.fromImage(image)
        self._render_dpi = dpi
        self._display_pixmap()

    def _on_render_failed(self, token: int, message: str) -> None:
        if token != self._render_token:
            return
        self._pixmap = None
        self._show_placeholder(f"Gagal menampilkan halaman.\n{message}")

    def _display_pixmap(self) -> None:
        if self._pixmap is None or self._pixmap.isNull():
            return
        pixmap = self._pixmap
        scaled = self._scaled_pixmap(pixmap)
        self._image_label.setStyleSheet("")
        self._image_label.setText("")
        self._image_label.setPixmap(scaled)
        self._image_label.resize(scaled.size())
        self._zoom_label.setText(f"{self._zoom * 100:.0f}%")

    def _scaled_pixmap(self, pixmap: QPixmap) -> QPixmap:
        if self._fit_to_width:
            available = max(self._scroll.viewport().width() - 8, 120)
            target_width = min(available, MAX_PREVIEW_DIMENSION)
            self._zoom = target_width / max(pixmap.width(), 1)
            return pixmap.scaledToWidth(
                target_width, Qt.TransformationMode.SmoothTransformation
            )
        width = max(int(pixmap.width() * self._zoom), 1)
        return pixmap.scaled(
            width,
            max(int(pixmap.height() * self._zoom), 1),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def _apply_zoom(self, value: float) -> None:
        self._zoom = max(MIN_ZOOM, min(value, MAX_ZOOM))
        if self._fit_to_width:
            self._fit_button.setChecked(False)
        if self._pixmap is not None:
            self._display_pixmap()
        self._zoom_label.setText(f"{self._zoom * 100:.0f}%")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._fit_to_width and self._pixmap is not None:
            self._display_pixmap()
        elif self._pixmap is None:
            self._image_label.resize(self._scroll.viewport().size())

    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)
