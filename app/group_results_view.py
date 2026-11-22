from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.export_worker import ExportWorker
from app.grouping_worker import GroupingWorker
from config import OUTPUT_DIR
from core.clustering_engine import DEFAULT_CLUSTER_CONFIG, ClusterConfig
from core.pdf_reader import PdfInfo
from core.review_model import ReviewState
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
_VERIFIED_COLOR = QColor(31, 111, 235)

_GROUP_ROLE = Qt.ItemDataRole.UserRole + 1
_DOC_ROLE = Qt.ItemDataRole.UserRole


class GroupResultsView(QWidget):
    """Clustered documents with manual review and export controls."""

    document_selected = Signal(str)
    review_changed = Signal()
    restore_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker: GroupingWorker | None = None
        self._export_worker: ExportWorker | None = None
        self._documents: list[PdfInfo] = []
        self._result: GroupingResult | None = None
        self._review: ReviewState | None = None
        self._selected_group_id: int | None = None
        self._pending_target: Path | None = None
        self._analysis_cache = None
        self._cluster_config = DEFAULT_CLUSTER_CONFIG

        self._build_ui()
        self._set_busy(False)
        self._show_placeholder(_PLACEHOLDER)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(self._build_header())
        layout.addWidget(self._build_review_bar())

        body = QHBoxLayout()
        body.setSpacing(8)
        body.addWidget(self._build_tree_panel(), 3)
        body.addWidget(self._build_detail_panel(), 2)
        layout.addLayout(body, 1)

        self._legend = QLabel(
            "Hijau = keyakinan tinggi (≥ 85)  •  Jingga = perlu pemeriksaan "
            "(82–84)  •  Biru = terverifikasi  •  Merah = belum terkelompok. "
            "Pasangan 65–81 ditampilkan sebagai kandidat dan TIDAK digabung."
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
        self._cluster_button.setToolTip("Hitung kemiripan dan bentuk kelompok")
        self._cluster_button.clicked.connect(self.start_grouping)
        row.addWidget(self._cluster_button)

        self._status_label = QLabel("")
        self._status_label.setObjectName("pageInfo")
        row.addWidget(self._status_label)
        row.addStretch(1)

        self._export_button = QPushButton("Ekspor Laporan")
        self._export_button.setToolTip("Simpan laporan CSV atau XLSX")
        self._export_button.clicked.connect(self.export_report_dialog)
        row.addWidget(self._export_button)

        self._copy_button = QPushButton("Salin ke Folder")
        self._copy_button.setToolTip(
            "Salin dokumen ke folder kelompok (file asli tidak diubah)"
        )
        self._copy_button.clicked.connect(self.copy_to_folders_dialog)
        row.addWidget(self._copy_button)
        return bar

    def _build_review_bar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        label = QLabel("Koreksi:")
        label.setObjectName("pageInfo")
        row.addWidget(label)

        self._verify_button = QPushButton("Tandai Terverifikasi")
        self._verify_button.clicked.connect(self.verify_selected_group)
        row.addWidget(self._verify_button)

        self._unverify_button = QPushButton("Batalkan Verifikasi")
        self._unverify_button.clicked.connect(self.unverify_selected_group)
        row.addWidget(self._unverify_button)

        self._merge_button = QPushButton("Gabungkan ke…")
        self._merge_button.setToolTip(
            "Kelompok terpilih akan digabung ke kelompok tujuan di sebelahnya"
        )
        self._merge_button.clicked.connect(self.merge_into)
        row.addWidget(self._merge_button)

        self._merge_target = QComboBox()
        self._merge_target.setMinimumWidth(140)
        row.addWidget(self._merge_target)

        self._split_button = QPushButton("Pisahkan Dokumen")
        self._split_button.setToolTip("Keluarkan dokumen terpilih dari kelompoknya")
        self._split_button.clicked.connect(self.split_selected_document)
        row.addWidget(self._split_button)

        self._reset_button = QPushButton("Kembalikan Otomatis")
        self._reset_button.setToolTip("Buang semua koreksi manual")
        self._reset_button.clicked.connect(self.reset_review)
        row.addWidget(self._reset_button)

        self._restore_button = QPushButton("Muat Hasil Tersimpan")
        self._restore_button.setToolTip(
            "Muat kembali kelompok yang tersimpan di database dari sesi sebelumnya"
        )
        self._restore_button.clicked.connect(self.restore_requested)
        self._restore_button.setEnabled(False)
        row.addWidget(self._restore_button)

        row.addStretch(1)
        return bar

    def _build_tree_panel(self) -> QWidget:
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Dokumen", "Skor", "Status"])
        self._tree.setColumnWidth(0, 250)
        self._tree.setColumnWidth(1, 60)
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
        self._review = None
        self._selected_group_id = None
        self._tree.clear()
        self._show_placeholder(_PLACEHOLDER)
        self._status_label.setText("")
        self._set_busy(False)

    def set_analysis_cache(self, cache) -> None:
        """Share the on-disk analysis cache so re-grouping is fast."""
        self._analysis_cache = cache

    def set_cluster_config(self, config: ClusterConfig) -> None:
        """Apply thresholds from the settings tab."""
        self._cluster_config = config

    def set_restore_available(self, available: bool) -> None:
        """Enable the 'Muat Hasil Tersimpan' button when the DB has groups."""
        self._restore_button.setEnabled(available)

    def adopt_review(self, state: ReviewState, documents: list[PdfInfo]) -> None:
        """Show a grouping result restored from the database."""
        self._documents = list(documents)
        self._review = state
        self._selected_group_id = None
        self._populate()
        self._set_busy(False)
        self._status_label.setText(
            f"Hasil tersimpan dimuat: {state.total_groups} kelompok, "
            f"{len(state.ungrouped)} belum terkelompok"
        )
        self.review_changed.emit()

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

        worker = GroupingWorker(
            self._documents,
            analysis_cache=self._analysis_cache,
            cluster_config=self._cluster_config,
            parent=self,
        )
        worker.progress.connect(self._on_progress)
        worker.finished_ok.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_worker_stopped)
        self._worker = worker
        worker.start()

    def current_result(self) -> GroupingResult | None:
        return self._result

    def current_review(self) -> ReviewState | None:
        return self._review

    def shutdown(self) -> None:
        for worker in (self._worker, self._export_worker):
            if worker is not None and worker.isRunning():
                worker.requestInterruption()
                worker.wait(5000)

    # ------------------------------------------------------- review actions
    def verify_selected_group(self) -> None:
        state = self._review
        group_id = self._current_group_id()
        if state is None or group_id is None:
            self._status_label.setText("Pilih kelompok terlebih dahulu.")
            return
        self._apply_review(state.set_verified(group_id, True))

    def unverify_selected_group(self) -> None:
        state = self._review
        group_id = self._current_group_id()
        if state is None or group_id is None:
            self._status_label.setText("Pilih kelompok terlebih dahulu.")
            return
        self._apply_review(state.set_verified(group_id, False))

    def merge_into(self) -> None:
        state = self._review
        source = self._current_group_id()
        target = self._merge_target.currentData()
        if state is None:
            return
        if source is None or target is None:
            self._status_label.setText("Pilih kelompok sumber dan tujuan.")
            return
        if source == int(target):
            self._status_label.setText("Kelompok sumber dan tujuan harus berbeda.")
            return
        try:
            merged = state.merge_groups((source,), int(target))
        except KeyError as exc:
            self._status_label.setText(str(exc))
            return
        self._selected_group_id = int(target)
        self._apply_review(merged)

    def split_selected_document(self) -> None:
        state = self._review
        key = self._current_document_key()
        if state is None:
            return
        if key is None:
            self._status_label.setText("Pilih dokumen terlebih dahulu.")
            return
        self._apply_review(state.split_document(key))

    def reset_review(self) -> None:
        state = self._review
        if state is None or self._result is None:
            return
        self._apply_review(state.reset(self._result.clustering))

    # ------------------------------------------------------------- exporting
    def export_report_dialog(self) -> None:
        state = self._review
        if state is None:
            self._status_label.setText("Belum ada hasil untuk diekspor.")
            return

        suggested = str(Path(OUTPUT_DIR) / "laporan_pengelompokan.xlsx")
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Simpan Laporan",
            suggested,
            "Excel (*.xlsx);;CSV (*.csv)",
        )
        if not path:
            return

        target = Path(path)
        fmt = "csv" if target.suffix.lower() == ".csv" else "xlsx"
        self._start_export(
            mode="report",
            output_dir=target.parent,
            fmt=fmt,
            target=target,
        )

    def copy_to_folders_dialog(self) -> None:
        state = self._review
        if state is None:
            self._status_label.setText("Belum ada hasil untuk disalin.")
            return

        folder = QFileDialog.getExistingDirectory(
            self, "Pilih Folder Tujuan", str(OUTPUT_DIR)
        )
        if not folder:
            return

        pending = [group for group in state.groups if not group.verified]
        if pending:
            answer = QMessageBox.question(
                self,
                "Masih ada kelompok belum terverifikasi",
                f"{len(pending)} kelompok belum ditandai terverifikasi.\n"
                "Tetap salin sekarang?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self._start_export(mode="copy", output_dir=Path(folder))

    def _start_export(
        self,
        *,
        mode: str,
        output_dir: Path,
        fmt: str = "xlsx",
        target: Path | None = None,
    ) -> None:
        """Run report writing or file copying in a background thread."""
        state = self._review
        if state is None:
            return
        if self._export_worker is not None and self._export_worker.isRunning():
            self._status_label.setText("Ekspor lain masih berjalan…")
            return

        self._pending_target = target
        self._status_label.setText(
            "Menulis laporan…" if mode == "report" else "Menyalin dokumen…"
        )
        self._set_busy(True)

        worker = ExportWorker(
            state,
            self._document_map(),
            output_dir,
            mode=mode,
            fmt=fmt,
            parent=self,
        )
        worker.progress.connect(self._on_export_progress)
        worker.finished_ok.connect(self._on_export_finished)
        worker.failed.connect(self._on_export_failed)
        worker.finished.connect(self._on_export_stopped)
        self._export_worker = worker
        worker.start()

    def _on_export_progress(self, current: int, total: int, name: str) -> None:
        self._status_label.setText(f"Menyalin {current}/{total} — {name}")

    def _on_export_finished(self, result) -> None:
        if result.has_error:
            QMessageBox.critical(self, "Ekspor gagal", result.error)
            return

        if result.report_path is not None:
            final = result.report_path
            target = getattr(self, "_pending_target", None)
            try:
                if target is not None and final != target:
                    if target.exists():
                        target.unlink()
                    final.replace(target)
                    final = target
            except OSError as exc:
                logger.warning("Gagal mengganti nama laporan: %s", exc)
            self._status_label.setText(f"Laporan disimpan: {final}")
            QMessageBox.information(
                self, "Ekspor selesai", f"Laporan disimpan:\n{final}"
            )
            return

        message = (
            f"{result.copied_count} file disalin.\n"
            f"Dilewati (sudah ada): {len(result.skipped)}\n\n"
            "File asli tidak diubah atau dihapus."
        )
        self._status_label.setText(
            f"{result.copied_count} file disalin, {len(result.skipped)} dilewati"
        )
        QMessageBox.information(self, "Penyalinan selesai", message)

    def _on_export_failed(self, message: str) -> None:
        QMessageBox.critical(self, "Ekspor gagal", message)
        self._status_label.setText("Ekspor gagal")

    def _on_export_stopped(self) -> None:
        worker = self._export_worker
        self._export_worker = None
        self._pending_target = None
        self._set_busy(False)
        if worker is not None:
            worker.deleteLater()

    # ------------------------------------------------------------ internals
    def _document_map(self) -> dict[str, PdfInfo]:
        return {document.key: document for document in self._documents}

    def _current_group_id(self) -> int | None:
        for item in self._tree.selectedItems():
            value = item.data(0, _GROUP_ROLE)
            if value is not None:
                return int(value)
            key = item.data(0, _DOC_ROLE)
            if key:
                parent = item.parent()
                if parent is not None:
                    parent_value = parent.data(0, _GROUP_ROLE)
                    if parent_value is not None:
                        return int(parent_value)
        return self._selected_group_id

    def _current_document_key(self) -> str | None:
        """Return the selected document key, ignoring selected group headers.

        With ``SingleSelection`` a tree can report the previously selected parent
        before the newly selected child, so scanning for the first row that
        actually carries a document key is the reliable lookup.
        """
        for item in self._tree.selectedItems():
            key = item.data(0, _DOC_ROLE)
            if key:
                return key
        return None

    def _apply_review(self, state: ReviewState) -> None:
        self._review = state
        # Remember which row the user had selected so a rebuild can restore it
        # instead of jumping back to the group header.
        selected_key = self._current_document_key()
        if self._selected_group_id is not None and state.group_by_id(
            self._selected_group_id
        ) is None:
            self._selected_group_id = None
        self._populate()
        if selected_key is not None:
            self._select_document(selected_key)
        self._status_label.setText(
            f"{state.total_groups} kelompok, {len(state.ungrouped)} belum, "
            f"{state.verified_count} terverifikasi"
        )
        self.review_changed.emit()

    def _on_progress(self, current: int, total: int, label: str) -> None:
        self._status_label.setText(f"{current}/{total} — {label}")

    def _on_finished(self, result: GroupingResult) -> None:
        self._result = result
        names = {document.key: document.filename for document in result.documents}
        self._review = ReviewState.from_clustering(result.clustering, names)
        self._populate()
        self._set_busy(False)
        self._status_label.setText(
            f"{result.group_count} kelompok, {result.ungrouped_count} belum terkelompok"
        )
        if result.clustering.candidate_count:
            self._status_label.setText(
                self._status_label.text()
                + f", {result.clustering.candidate_count} pasangan kandidat"
            )
        if result.errors:
            self._status_label.setText(
                self._status_label.text() + f", {len(result.errors)} gagal"
            )
        self.review_changed.emit()

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
        has_review = self._review is not None
        for widget in (
            self._verify_button,
            self._unverify_button,
            self._merge_button,
            self._merge_target,
            self._split_button,
            self._reset_button,
            self._export_button,
            self._copy_button,
        ):
            widget.setEnabled(has_review and not busy)

    def _populate(self) -> None:
        state = self._review
        self._tree.clear()
        if state is None:
            return

        for group in state.groups:
            if group.verified:
                colour = _VERIFIED_COLOR
            elif group.confidence == "Tinggi":
                colour = _HIGH_COLOR
            elif group.confidence == "Sedang":
                colour = _REVIEW_COLOR
            else:
                colour = _LOW_COLOR

            status = (
                group.status_label if group.verified else f"Keyakinan {group.confidence}"
            )
            parent = QTreeWidgetItem(
                [
                    f"{group.name} — {group.size} dokumen",
                    f"{group.min_pair_score:.1f}",
                    status,
                ]
            )
            parent.setData(0, _GROUP_ROLE, group.group_id)
            for column in range(3):
                parent.setForeground(column, colour)
            self._tree.addTopLevelItem(parent)

            scores = state.member_scores(group)
            for key in group.member_keys:
                child = QTreeWidgetItem(
                    [
                        state.name_of(key),
                        f"{scores.get(key, 0.0):.1f}",
                        "Anggota kelompok",
                    ]
                )
                child.setData(0, _DOC_ROLE, key)
                parent.addChild(child)
            parent.setExpanded(True)

        if state.ungrouped:
            parent = QTreeWidgetItem(
                ["Belum Terkelompok", "", f"{len(state.ungrouped)} dokumen"]
            )
            for column in range(3):
                parent.setForeground(column, _LOW_COLOR)
            self._tree.addTopLevelItem(parent)
            for key in state.ungrouped:
                child = QTreeWidgetItem(
                    [state.name_of(key), "0.0", "Tidak ada pasangan cocok"]
                )
                child.setData(0, _DOC_ROLE, key)
                parent.addChild(child)
            parent.setExpanded(True)

        # Pairs in the "needs review" band: shown, never merged automatically.
        candidates = (
            self._result.clustering.candidates if self._result is not None else ()
        )
        if candidates:
            parent = QTreeWidgetItem(
                ["Kandidat (perlu pemeriksaan)", "", f"{len(candidates)} pasangan"]
            )
            for column in range(3):
                parent.setForeground(column, _REVIEW_COLOR)
            self._tree.addTopLevelItem(parent)
            for pair in sorted(candidates, key=lambda item: -item.score):
                child = QTreeWidgetItem(
                    [
                        f"{state.name_of(pair.a_key)} ↔ {state.name_of(pair.b_key)}",
                        f"{pair.score:.1f}",
                        "Tidak digabung — periksa manual",
                    ]
                )
                parent.addChild(child)
            parent.setExpanded(True)

        self._refresh_merge_targets()
        self._empty_label.setVisible(self._tree.topLevelItemCount() == 0)
        self._detail.setText(self._summary_text(state))
        if self._selected_group_id is not None:
            self._select_group(self._selected_group_id)

    def _summary_text(self, state: ReviewState) -> str:
        return "\n".join(
            [
                f"Jumlah kelompok: {state.total_groups}",
                f"Dokumen berkelompok: {state.grouped_count}",
                f"Belum terkelompok: {len(state.ungrouped)}",
                f"Terverifikasi: {state.verified_count}",
                f"Perlu verifikasi: {state.needs_review_count}",
            ]
        )

    def _refresh_merge_targets(self) -> None:
        state = self._review
        current = self._merge_target.currentData()
        self._merge_target.blockSignals(True)
        self._merge_target.clear()
        if state is not None:
            for group in state.groups:
                self._merge_target.addItem(group.name, group.group_id)
            if current is not None:
                index = self._merge_target.findData(current)
                if index >= 0:
                    self._merge_target.setCurrentIndex(index)
        self._merge_target.blockSignals(False)

    def _select_group(self, group_id: int) -> None:
        for index in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(index)
            if item.data(0, _GROUP_ROLE) == group_id:
                item.setSelected(True)
                return

    def _select_document(self, key: str) -> bool:
        """Select the row of ``key`` wherever it now lives."""
        for index in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(index)
            if item.data(0, _DOC_ROLE) == key:
                item.setSelected(True)
                return True
            for child_index in range(item.childCount()):
                child = item.child(child_index)
                if child.data(0, _DOC_ROLE) == key:
                    self._tree.clearSelection()
                    child.setSelected(True)
                    self._tree.setCurrentItem(child)
                    return True
        return False

    def _on_selection_changed(self) -> None:
        items = self._tree.selectedItems()
        if not items:
            return

        item = items[0]
        key = item.data(0, _DOC_ROLE)
        group_id = item.data(0, _GROUP_ROLE)
        if group_id is not None:
            self._selected_group_id = int(group_id)

        if key:
            self.document_selected.emit(key)
        self._detail.setText(self._describe(item, key))

    def _describe(self, item: QTreeWidgetItem, key: str | None) -> str:
        state = self._review
        if state is None:
            return ""

        lines: list[str] = []
        if key is not None:
            group = state.group_of(key)
            lines.append(f"Dokumen: {state.name_of(key)}")
            if group is not None:
                lines.append(f"Kelompok: {group.name}")
                lines.append(f"Dokumen dalam kelompok: {group.size}")
                lines.append(f"Keyakinan kelompok: {group.confidence}")
                lines.append(f"Skor pasangan terendah: {group.min_pair_score:.1f}")
                lines.append(f"Status: {group.status_label}")
                lines.append("")
                lines.append("Anggota:")
                for member_key in group.member_keys:
                    marker = "→" if member_key == key else "•"
                    lines.append(f"  {marker} {state.name_of(member_key)}")
            else:
                lines.append("Status: belum terkelompok")
                lines.append(
                    "Tidak ada dokumen lain dengan kemiripan peta dasar yang memadai."
                )

            lines.append("")
            lines.append("Pasangan terukur (skor tertinggi):")
            edges = [
                edge
                for edge in state.edges
                if key in (edge.a_key, edge.b_key) and not edge.skipped
            ]
            edges.sort(key=lambda edge: -edge.score)
            for edge in edges[:8]:
                other = edge.b_key if edge.a_key == key else edge.a_key
                lines.append(
                    f"  {edge.score:5.1f}  {state.name_of(other)} "
                    f"({edge.match_count} kecocokan)"
                )
        else:
            group = state.group_by_id(self._selected_group_id or 0)
            if group is not None:
                lines.append(f"Kelompok: {group.name}")
                lines.append(f"Dokumen: {group.size}")
                lines.append(f"Keyakinan: {group.confidence}")
                lines.append(f"Skor pasangan terendah: {group.min_pair_score:.1f}")
                lines.append(f"Status: {group.status_label}")
                lines.append("")
                lines.append("Anggota:")
                for member_key in group.member_keys:
                    lines.append(f"  • {state.name_of(member_key)}")
            else:
                lines.append(item.text(0))
                lines.append(item.text(2))

        lines.append("")
        lines.append(self._summary_text(state))
        return "\n".join(lines)

    def _show_placeholder(self, text: str) -> None:
        self._empty_label.setText(text)
        self._empty_label.setVisible(self._tree.topLevelItemCount() == 0)
