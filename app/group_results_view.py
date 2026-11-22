from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.grouping_worker import GroupingWorker
from core.pdf_reader import PdfInfo
from services.grouping_service import GroupingResult
from utils.logging_utils import get_logger

logger = get_logger("group_results_view")

_PLACEHOLDER = (
    "Belum ada hasil pengelompokan.\n"
    "Muat folder, jalankan “Deteksi Halaman Peta”, lalu klik “Kelompokkan”."
)

_HIGH_COLOR = QColor(21, 115, 71)
_REVIEW_COLOR = QColor(160, 106, 20)
_LOW_COLOR = QColor(178, 34, 34)


class GroupResultsView(QWidget):
    """Shows clustered documents with confidence and ungrouped documents."""

    document_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker: GroupingWorker | None = None
        self._documents: list[PdfInfo] = []
        self._result: GroupingResult | None = None

        self._build_ui()
        self._set_busy(False)
        self._show_placeholder(_PLACEHOLDER)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(self._build_header())

        splitter = QHBoxLayout()
        splitter.setSpacing(8)
        splitter.addWidget(self._build_tree_panel(), 3)
        splitter.addWidget(self._build_detail_panel(), 2)
        layout.addLayout(splitter, 1)

        self._legend = QLabel(
            "Hijau = keyakinan tinggi (skor ≥ 85)  •  Jingga = perlu pemeriksaan "
            "(65–84)  •  Merah = belum terkelompok"
        )
        self._legend.setObjectName("legendLabel")
        self._legend.setWordWrap(True)
        layout.addWidget(self._legend)

    def _build_header(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        title = QLabel("Hasil Pengelompokan")
        title.setObjectName("sectionTitle")
        row.addWidget(title)

        self._cluster_button = QPushButton("Kelompokkan")
        self._cluster_button.setToolTip(
            "Hitung kemiripan antar dokumen dan bentuk kelompok"
        )
        self._cluster_button.clicked.connect(self.start_grouping)
        row.addWidget(self._cluster_button)

        self._status_label = QLabel("")
        self._status_label.setObjectName("pageInfo")
        row.addWidget(self._status_label)
        row.addStretch(1)
        return bar

    def _build_tree_panel(self) -> QWidget:
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Dokumen", "Skor", "Status"])
        self._tree.setColumnWidth(0, 260)
        self._tree.setColumnWidth(1, 70)
        self._tree.setAlternatingRowColors(True)
        self._tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._tree.itemSelectionChanged.connect(self._on_selection_changed)
        column.addWidget(self._tree, 1)

        self._empty_label = QLabel(_PLACEHOLDER)
        self._empty_label.setObjectName("emptyHint")
        self._empty_label.setWordWrap(True)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(self._empty_label)
        return panel

    def _build_detail_panel(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("statCard")
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        header = QLabel("Rincian")
        header.setObjectName("sectionTitle")
        layout.addWidget(header)

        self._detail = QLabel("")
        self._detail.setObjectName("detailLabel")
        self._detail.setWordWrap(True)
        self._detail.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._detail.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._detail.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._detail)
        layout.addWidget(scroll, 1)
        return frame

    # --------------------------------------------------------------- public
    def set_documents(self, documents: list[PdfInfo]) -> None:
        self._documents = list(documents)
        self._result = None
        self._tree.clear()
        self._show_placeholder(_PLACEHOLDER)
        self._status_label.setText("")
        self._set_busy(False)

    def start_grouping(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        if len(self._documents) < 2:
            self._show_placeholder("Perlu minimal dua dokumen untuk pengelompokan.")
            return

        self._set_busy(True)
        self._tree.clear()
        self._detail.setText("")
        self._status_label.setText("Menyiapkan dokumen…")

        worker = GroupingWorker(self._documents, parent=self)
        worker.progress.connect(self._on_progress)
        worker.finished_ok.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_worker_stopped)
        self._worker = worker
        worker.start()

    def current_result(self) -> GroupingResult | None:
        return self._result

    def shutdown(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(3000)

    # ------------------------------------------------------------ internals
    def _on_progress(self, current: int, total: int, label: str) -> None:
        self._status_label.setText(f"{current}/{total} — {label}")

    def _on_finished(self, result: GroupingResult) -> None:
        self._result = result
        self._populate(result)
        self._status_label.setText(
            f"{result.group_count} kelompok, {result.ungrouped_count} belum terkelompok"
        )
        if result.errors:
            self._status_label.setText(
                self._status_label.text() + f", {len(result.errors)} gagal"
            )

    def _on_failed(self, message: str) -> None:
        self._show_placeholder(f"Pengelompokan gagal.\n{message}")
        self._status_label.setText("Gagal")

    def _on_worker_stopped(self) -> None:
        worker = self._worker
        self._worker = None
        self._set_busy(False)
        if worker is not None:
            worker.deleteLater()

    def _set_busy(self, busy: bool) -> None:
        self._cluster_button.setEnabled(not busy and len(self._documents) >= 2)
        self._tree.setEnabled(not busy)

    def _populate(self, result: GroupingResult) -> None:
        self._tree.clear()
        clustering = result.clustering
        for group in clustering.groups:
            parent = QTreeWidgetItem(
                [
                    f"{group.name} — {group.size} dokumen",
                    f"{group.min_pair_score:.1f}",
                    f"Keyakinan {group.confidence}",
                ]
            )
            parent.setFirstColumnSpanned(False)
            colour = (
                _HIGH_COLOR
                if group.confidence == "Tinggi"
                else _REVIEW_COLOR if group.confidence == "Sedang" else _LOW_COLOR
            )
            for column in range(3):
                parent.setForeground(column, colour)
            self._tree.addTopLevelItem(parent)

            for key in group.member_keys:
                child = QTreeWidgetItem(
                    [result.name_of(key), "", "Anggota kelompok"]
                )
                child.setData(0, Qt.ItemDataRole.UserRole, key)
                parent.addChild(child)
            parent.setExpanded(True)

        if clustering.ungrouped:
            parent = QTreeWidgetItem(
                ["Belum Terkelompok", "", f"{len(clustering.ungrouped)} dokumen"]
            )
            for column in range(3):
                parent.setForeground(column, _LOW_COLOR)
            self._tree.addTopLevelItem(parent)
            for key in clustering.ungrouped:
                child = QTreeWidgetItem(
                    [result.name_of(key), "0.0", "Tidak ada pasangan cocok"]
                )
                child.setData(0, Qt.ItemDataRole.UserRole, key)
                parent.addChild(child)
            parent.setExpanded(True)

        self._empty_label.setVisible(self._tree.topLevelItemCount() == 0)
        self._detail.setText(result.summary())
        self._detail.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

    def _on_selection_changed(self) -> None:
        items = self._tree.selectedItems()
        if not items:
            return
        item = items[0]
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if key:
            self.document_selected.emit(key)
        self._detail.setText(self._describe(item, key))

    def _describe(self, item: QTreeWidgetItem, key: str | None) -> str:
        result = self._result
        if result is None:
            return ""

        lines: list[str] = []
        if key is not None:
            group = result.clustering.group_of(key)
            lines.append(f"Dokumen: {result.name_of(key)}")
            if group is not None:
                lines.append(f"Kelompok: {group.name}")
                lines.append(f"Dokumen dalam kelompok: {group.size}")
                lines.append(f"Keyakinan kelompok: {group.confidence}")
                lines.append(f"Skor pasangan terendah: {group.min_pair_score:.1f}")
                lines.append(
                    "Perlu verifikasi manual"
                    if group.needs_review
                    else "Keyakinan tinggi"
                )
                lines.append("")
                lines.append("Anggota:")
                for member_key in group.member_keys:
                    lines.append(f"  • {result.name_of(member_key)}")
            else:
                lines.append("Status: belum terkelompok")
                lines.append(
                    "Tidak ada dokumen lain dengan kemiripan peta dasar yang memadai."
                )

            lines.append("")
            lines.append("Pasangan terukur (skor tertinggi):")
            scores = [
                edge
                for edge in result.clustering.edges
                if key in (edge.a_key, edge.b_key) and not edge.skipped
            ]
            scores.sort(key=lambda edge: -edge.score)
            for edge in scores[:8]:
                other = edge.b_key if edge.a_key == key else edge.a_key
                lines.append(f"  {edge.score:5.1f}  {result.name_of(other)}")
        else:
            lines.append(item.text(0))
            lines.append(item.text(2))

        return "\n".join(lines)

    def _show_placeholder(self, text: str) -> None:
        self._empty_label.setText(text)
        self._empty_label.setVisible(self._tree.topLevelItemCount() == 0)
