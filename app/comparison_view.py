from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.comparison_worker import ComparisonWorker
from core.pdf_reader import PdfInfo
from services.comparison_service import ComparisonResult, comparison_summary
from utils.logging_utils import get_logger

logger = get_logger("comparison_view")

_MODE_OVERLAY = "overlay"
_MODE_SIDE = "side"
_MODE_UNMASKED = "unmasked"
_MODE_PARCEL = "parcel"
_PLACEHOLDER = (
    "Pilih dua dokumen (A dan B), lalu klik “Bandingkan”.\n"
    "Peta akan diselaraskan dan ditampilkan sebagai overlay."
)


class ComparisonView(QWidget):
    """Side-by-side and overlay comparison of two aligned map pages."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker: ComparisonWorker | None = None
        self._result: ComparisonResult | None = None
        self._pixmap: QPixmap | None = None
        self._documents: list[PdfInfo] = []

        self._build_ui()
        self._set_busy(False)
        self._show_placeholder(_PLACEHOLDER)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        header = QLabel("Perbandingan Peta")
        header.setObjectName("sectionTitle")
        layout.addWidget(header)

        layout.addWidget(self._build_controls())

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._build_canvas())

        self._summary_label = QLabel("")
        self._summary_label.setObjectName("detailLabel")
        self._summary_label.setWordWrap(True)
        self._summary_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        summary_scroll = QScrollArea()
        summary_scroll.setWidgetResizable(True)
        summary_scroll.setFrameShape(QFrame.Shape.StyledPanel)
        summary_scroll.setWidget(self._summary_label)
        summary_scroll.setMinimumHeight(120)
        splitter.addWidget(summary_scroll)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([620, 150])
        layout.addWidget(splitter, 1)

        legend = QLabel(
            "Overlay: biru = peta A, merah = peta B setelah diselaraskan, "
            "gelap = tinta yang berimpit."
        )
        legend.setObjectName("legendLabel")
        legend.setWordWrap(True)
        layout.addWidget(legend)

    def _build_controls(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self._doc_a_combo = QComboBox()
        self._doc_a_combo.setMinimumWidth(190)
        self._doc_a_combo.setToolTip("Dokumen acuan (A)")
        self._doc_a_combo.currentIndexChanged.connect(self._on_selection_changed)

        self._doc_b_combo = QComboBox()
        self._doc_b_combo.setMinimumWidth(190)
        self._doc_b_combo.setToolTip("Dokumen pembanding (B)")
        self._doc_b_combo.currentIndexChanged.connect(self._on_selection_changed)

        row.addWidget(QLabel("A:"))
        row.addWidget(self._doc_a_combo)
        row.addWidget(QLabel("B:"))
        row.addWidget(self._doc_b_combo)

        self._compare_button = QPushButton("Bandingkan")
        self._compare_button.setToolTip("Selaraskan kedua peta dan tampilkan hasilnya")
        self._compare_button.clicked.connect(self.start_comparison)
        row.addWidget(self._compare_button)

        self._swap_button = QPushButton("Tukar")
        self._swap_button.setToolTip("Tukar dokumen A dan B")
        self._swap_button.clicked.connect(self.swap_documents)
        row.addWidget(self._swap_button)

        self._mode_combo = QComboBox()
        self._mode_combo.addItem("Overlay", _MODE_OVERLAY)
        self._mode_combo.addItem("Berdampingan", _MODE_SIDE)
        self._mode_combo.addItem("Overlay tanpa masking", _MODE_UNMASKED)
        self._mode_combo.addItem("Bidang utama", _MODE_PARCEL)
        self._mode_combo.currentIndexChanged.connect(self._refresh_canvas)
        row.addWidget(self._mode_combo)

        self._parcel_check = QPushButton("Abaikan Bidang Utama")
        self._parcel_check.setCheckable(True)
        self._parcel_check.setChecked(True)
        self._parcel_check.setToolTip(
            "Keluarkan garis bidang utama sebelum mencocokkan, agar perbedaan "
            "bidang tidak memengaruhi kemiripan peta dasar"
        )
        self._parcel_check.toggled.connect(self._on_parcel_toggled)
        row.addWidget(self._parcel_check)

        row.addStretch(1)
        return bar

    def _build_canvas(self) -> QWidget:
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setFrameShape(QFrame.Shape.StyledPanel)
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )
        self._scroll.setWidget(self._image_label)
        return self._scroll

    # --------------------------------------------------------------- public
    def set_documents(self, documents: list[PdfInfo]) -> None:
        """Offer only documents that actually have a detected map page."""
        self._documents = list(documents)
        current_a = self._doc_a_combo.currentData()
        current_b = self._doc_b_combo.currentData()

        for combo, previous in (
            (self._doc_a_combo, current_a),
            (self._doc_b_combo, current_b),
        ):
            combo.blockSignals(True)
            combo.clear()
            for document in documents:
                combo.addItem(document.filename, document.key)
            if previous is not None:
                index = combo.findData(previous)
                if index >= 0:
                    combo.setCurrentIndex(index)
            combo.blockSignals(False)

        if len(documents) >= 2:
            if self._doc_b_combo.currentIndex() in (-1, self._doc_a_combo.currentIndex()):
                self._doc_b_combo.blockSignals(True)
                self._doc_b_combo.setCurrentIndex(
                    1 if self._doc_a_combo.currentIndex() == 0 else 0
                )
                self._doc_b_combo.blockSignals(False)

        self._update_controls()

    def set_document_pair(self, reference_key: str, candidate_key: str | None = None) -> None:
        index = self._doc_a_combo.findData(reference_key)
        if index >= 0:
            self._doc_a_combo.setCurrentIndex(index)
        if candidate_key is not None:
            index_b = self._doc_b_combo.findData(candidate_key)
            if index_b >= 0:
                self._doc_b_combo.setCurrentIndex(index_b)

    def start_comparison(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return

        reference = self._selected_document(self._doc_a_combo)
        candidate = self._selected_document(self._doc_b_combo)
        if reference is None or candidate is None:
            self._show_placeholder("Pilih dokumen A dan B terlebih dahulu.")
            return
        if reference.key == candidate.key:
            self._show_placeholder("Dokumen A dan B harus berbeda.")
            return

        self._result = None
        self._pixmap = None
        self._summary_label.setText("")
        self._show_placeholder("Menyiapkan perbandingan…")
        self._set_busy(True)

        worker = ComparisonWorker(
            reference,
            candidate,
            ignore_parcel=self._parcel_check.isChecked(),
            parent=self,
        )
        worker.stage.connect(self._on_stage)
        worker.finished_ok.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_worker_stopped)
        self._worker = worker
        worker.start()

    def swap_documents(self) -> None:
        index_a = self._doc_a_combo.currentIndex()
        index_b = self._doc_b_combo.currentIndex()
        self._doc_a_combo.blockSignals(True)
        self._doc_b_combo.blockSignals(True)
        self._doc_a_combo.setCurrentIndex(index_b)
        self._doc_b_combo.setCurrentIndex(index_a)
        self._doc_a_combo.blockSignals(False)
        self._doc_b_combo.blockSignals(False)
        if self._result is not None:
            self.start_comparison()

    def clear(self) -> None:
        self._result = None
        self._pixmap = None
        self._summary_label.setText("")
        self._show_placeholder(_PLACEHOLDER)

    def current_result(self) -> ComparisonResult | None:
        return self._result

    # ------------------------------------------------------------ internals
    def _selected_document(self, combo: QComboBox) -> PdfInfo | None:
        key = combo.currentData()
        if key is None:
            return None
        return next((doc for doc in self._documents if doc.key == key), None)

    def _on_selection_changed(self) -> None:
        self._update_controls()

    def _update_controls(self) -> None:
        has_two = len(self._documents) >= 2
        same = (
            self._doc_a_combo.currentData() is not None
            and self._doc_a_combo.currentData() == self._doc_b_combo.currentData()
        )
        idle = self._worker is None or not self._worker.isRunning()
        self._compare_button.setEnabled(has_two and not same and idle)
        self._swap_button.setEnabled(has_two and idle)
        self._doc_a_combo.setEnabled(not self._is_busy())
        self._doc_b_combo.setEnabled(not self._is_busy())

    def _is_busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _on_stage(self, message: str) -> None:
        self._show_placeholder(message)

    def _on_finished(self, result: ComparisonResult) -> None:
        self._result = result
        self._summary_label.setText(comparison_summary(result))

        if result.has_error or not result.overlay_png:
            self._show_placeholder(
                result.error or "Perbandingan tidak menghasilkan gambar."
            )
            return
        self._refresh_canvas()

    def _on_failed(self, message: str) -> None:
        self._result = None
        self._summary_label.setText(f"Perbandingan gagal: {message}")
        self._show_placeholder(f"Perbandingan gagal.\n{message}")

    def _on_worker_stopped(self) -> None:
        worker = self._worker
        self._worker = None
        self._set_busy(False)
        if worker is not None:
            worker.deleteLater()

    def _set_busy(self, busy: bool) -> None:
        self._compare_button.setEnabled(not busy and len(self._documents) >= 2)
        self._swap_button.setEnabled(not busy and len(self._documents) >= 2)
        self._doc_a_combo.setEnabled(not busy)
        self._doc_b_combo.setEnabled(not busy)
        self._mode_combo.setEnabled(not busy)
        self._parcel_check.setEnabled(not busy)
        if not busy:
            self._update_controls()

    def _refresh_canvas(self) -> None:
        result = self._result
        if result is None:
            return

        mode = self._mode_combo.currentData()
        if mode == _MODE_OVERLAY:
            payload = result.overlay_png
        elif mode == _MODE_SIDE:
            payload = result.side_by_side_png
        elif mode == _MODE_UNMASKED:
            payload = result.overlay_unmasked_png or result.overlay_png
        else:
            payload = result.parcel_mask_png or result.overlay_png

        if not payload:
            self._show_placeholder("Gambar perbandingan tidak tersedia untuk mode ini.")
            return

        image = QImage.fromData(payload, "PNG")
        if image.isNull():
            self._show_placeholder("Gambar perbandingan tidak dapat dibuka.")
            return

        self._pixmap = QPixmap.fromImage(image)
        self._image_label.setText("")
        self._image_label.setStyleSheet("")
        self._apply_scale()

    def _on_parcel_toggled(self, checked: bool) -> None:
        if self._result is None:
            return
        # Re-run: masking changes both the alignment and the score.
        self.start_comparison()

    def _apply_scale(self) -> None:
        if self._pixmap is None or self._pixmap.isNull():
            return
        # Fit the whole comparison into view: an overlay is only useful when the
        # entire page is visible, whereas fit-to-width clipped the lower half.
        viewport = self._scroll.viewport().size()
        available_width = max(viewport.width() - 8, 120)
        available_height = max(viewport.height() - 8, 120)
        scaled = self._pixmap.scaled(
            available_width,
            available_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.setPixmap(scaled)
        self._image_label.resize(scaled.size())

    def _show_placeholder(self, text: str) -> None:
        self._pixmap = None
        self._image_label.setPixmap(QPixmap())
        self._image_label.setText(text)
        self._image_label.setStyleSheet("color: #7a7a7a; padding: 24px;")
        self._image_label.resize(self._scroll.viewport().size())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._pixmap is not None:
            self._apply_scale()
        else:
            self._image_label.resize(self._scroll.viewport().size())

    def shutdown(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(3000)
