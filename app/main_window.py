from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QItemSelection, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.analysis_worker import AnalysisWorker
from app.comparison_view import ComparisonView
from app.dashboard import DashboardBar
from app.group_results_view import GroupResultsView
from app.models import (
    DOCUMENT_ROLE,
    Column,
    DocumentFilterProxyModel,
    DocumentTableModel,
)
from app.preview_view import PreviewPanel
from app.scan_worker import ScanWorker
from app.settings_store import AppSettings, load_settings
from app.settings_view import SettingsView
from config import APP_NAME, RECURSIVE_SCAN_DEFAULT
from core.clustering_engine import ClusterConfig
from core.map_page_detector import MapPageDetector
from core.pdf_reader import PdfInfo
from database import db
from services.cache_service import AnalysisCache, file_fingerprint
from services.import_service import ImportResult, find_pdf_files
from services.persistence_service import (
    RestoredReview,
    load_review,
    save_review,
)
from services.processing_service import (
    AnalysisResult,
    DocumentAnalysis,
    detection_summary,
)
from utils.logging_utils import get_logger

logger = get_logger("main_window")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — Phase 9 (Perbaikan Akurasi)")
        self.resize(1360, 880)
        self.setMinimumSize(1020, 660)

        self._documents: list[PdfInfo] = []
        self._folder: Path | None = None
        self._worker: ScanWorker | None = None
        self._analysis_worker: AnalysisWorker | None = None
        self._recursive = RECURSIVE_SCAN_DEFAULT

        self._model = DocumentTableModel(parent=self)
        self._proxy = DocumentFilterProxyModel(self)
        self._proxy.setSourceModel(self._model)

        self._analysis_cache = AnalysisCache()
        self._database_path = db.initialize()
        self._settings: AppSettings = load_settings()

        self._build_ui()
        self._build_actions()
        self._connect_signals()
        self._update_summary()
        self._set_busy(False)
        self._update_analysis_buttons()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 8)
        root.setSpacing(10)

        root.addWidget(self._build_toolbar())
        self._dashboard = DashboardBar()
        root.addWidget(self._dashboard)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_document_panel())
        splitter.addWidget(self._build_preview_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([880, 600])

        self._workspace_tabs = QTabWidget()
        browse_tab = QWidget()
        browse_layout = QVBoxLayout(browse_tab)
        browse_layout.setContentsMargins(0, 8, 0, 0)
        browse_layout.addWidget(splitter)
        self._workspace_tabs.addTab(browse_tab, "Dokumen && Pratinjau")

        self._comparison_view = ComparisonView()
        self._workspace_tabs.addTab(self._comparison_view, "Perbandingan Peta")

        self._group_view = GroupResultsView()
        self._group_view.document_selected.connect(self._select_document_by_key)
        self._group_view.set_analysis_cache(self._analysis_cache)
        self._group_view.set_cluster_config(self._cluster_config())
        self._group_view.review_changed.connect(self._on_review_changed)
        self._group_view.restore_requested.connect(self._restore_saved_review)
        self._workspace_tabs.addTab(self._group_view, "Hasil Pengelompokan")

        self._settings_view = SettingsView(self._settings)
        self._settings_view.settings_changed.connect(self._on_settings_changed)
        self._settings_view.clear_cache_requested.connect(self._clear_analysis_cache)
        self._workspace_tabs.addTab(self._settings_view, "Pengaturan")

        root.addWidget(self._workspace_tabs, 1)

        self.setCentralWidget(central)
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Siap. Pilih folder PDF untuk memulai.")

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._open_button = QPushButton("Buka Folder PDF")
        self._open_button.setToolTip("Pilih satu folder berisi dokumen PDF")

        self._refresh_button = QPushButton("Muat Ulang")
        self._refresh_button.setToolTip("Pindai ulang folder yang sedang aktif")

        self._remove_button = QPushButton("Keluarkan dari Daftar")
        self._remove_button.setToolTip("Hapus dari daftar tanpa menghapus file asli")

        self._detect_button = QPushButton("Deteksi Halaman Peta")
        self._detect_button.setToolTip(
            "Deteksi halaman peta dan normalisasi (grayscale, kontras, denoise, "
            "rotasi, deskew) pada dokumen valid"
        )

        folder_label_wrap = QVBoxLayout()
        folder_label_wrap.setSpacing(0)
        self._folder_label = QLabel("Belum ada folder dipilih")
        self._folder_label.setObjectName("folderLabel")
        self._folder_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        folder_label_wrap.addWidget(self._folder_label)

        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Cari nama file, status, atau halaman peta…")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.setMaximumWidth(300)

        layout.addWidget(self._open_button)
        layout.addWidget(self._refresh_button)
        layout.addWidget(self._remove_button)
        layout.addWidget(self._detect_button)
        layout.addSpacing(8)
        layout.addLayout(folder_label_wrap, 1)
        layout.addWidget(self._filter_edit)

        self._progress = QProgressBar()
        self._progress.setMaximumWidth(240)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)
        return bar

    def _build_document_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QLabel("Daftar Dokumen PDF")
        header.setObjectName("sectionTitle")
        layout.addWidget(header)

        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.setWordWrap(False)
        self._table.verticalHeader().setVisible(False)
        header_view = self._table.horizontalHeader()
        header_view.setMinimumSectionSize(58)
        header_view.setStretchLastSection(False)
        header_view.setSectionResizeMode(
            Column.NUMBER, QHeaderView.ResizeMode.ResizeToContents
        )
        header_view.setSectionResizeMode(Column.NAME, QHeaderView.ResizeMode.Stretch)
        for column, width in (
            (Column.PAGES, 78),
            (Column.MAP_PAGE, 116),
            (Column.SCORE, 70),
            (Column.SIZE, 84),
            (Column.STATUS, 92),
        ):
            header_view.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self._table.setColumnWidth(column, width)
        self._table.sortByColumn(Column.NAME, Qt.SortOrder.AscendingOrder)
        layout.addWidget(self._table, 1)

        self._empty_label = QLabel(
            "Belum ada dokumen. Klik “Buka Folder PDF” untuk memilih folder."
        )
        self._empty_label.setObjectName("emptyHint")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._empty_label)

        self._detail_label = QLabel("")
        self._detail_label.setObjectName("detailLabel")
        self._detail_label.setWordWrap(True)
        self._detail_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self._detail_label)
        return panel

    def _build_preview_panel(self) -> QWidget:
        self._preview = PreviewPanel()
        return self._preview

    def _build_actions(self) -> None:
        open_action = QAction("Buka Folder", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._choose_folder)

        refresh_action = QAction("Muat Ulang", self)
        refresh_action.setShortcut(QKeySequence.StandardKey.Refresh)
        refresh_action.triggered.connect(self._reload_folder)

        detect_action = QAction("Deteksi Halaman Peta", self)
        detect_action.setShortcut(QKeySequence("Ctrl+D"))
        detect_action.triggered.connect(self._start_analysis)

        next_action = QAction("Halaman Berikutnya", self)
        next_action.setShortcut(QKeySequence(Qt.Key.Key_PageDown))
        next_action.triggered.connect(lambda: self._preview.step_page(1))

        prev_action = QAction("Halaman Sebelumnya", self)
        prev_action.setShortcut(QKeySequence(Qt.Key.Key_PageUp))
        prev_action.triggered.connect(lambda: self._preview.step_page(-1))

        for action in (open_action, refresh_action, detect_action, next_action, prev_action):
            self.addAction(action)

    def _connect_signals(self) -> None:
        self._open_button.clicked.connect(self._choose_folder)
        self._refresh_button.clicked.connect(self._reload_folder)
        self._remove_button.clicked.connect(self._remove_selected)
        self._detect_button.clicked.connect(self._start_analysis)
        self._filter_edit.textChanged.connect(self._proxy.set_filter_text)
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)

    # ------------------------------------------------------------- actions
    def _choose_folder(self) -> None:
        start_dir = str(self._folder) if self._folder else str(Path.home())
        folder = QFileDialog.getExistingDirectory(
            self, "Pilih Folder Berisi PDF", start_dir
        )
        if not folder:
            return
        self._start_scan(Path(folder))

    def _reload_folder(self) -> None:
        if self._folder is None:
            self._choose_folder()
            return
        self._start_scan(self._folder)

    def _start_scan(self, folder: Path) -> None:
        if self._is_busy():
            return
        try:
            has_pdfs = bool(find_pdf_files(folder, recursive=self._recursive))
        except OSError as exc:
            QMessageBox.warning(self, "Folder tidak valid", str(exc))
            return

        if not has_pdfs:
            QMessageBox.information(
                self,
                "Tidak ada PDF",
                f"Folder berikut tidak berisi file PDF:\n{folder}",
            )

        self._folder = folder
        self._folder_label.setText(str(folder))
        self._folder_label.setToolTip(str(folder))
        self._progress.setRange(0, 0)
        self._progress.setVisible(True)
        self._status_bar.showMessage("Memindai dan memvalidasi PDF…")

        worker = ScanWorker(folder, recursive=self._recursive, parent=self)
        worker.progress.connect(self._on_scan_progress)
        worker.finished_ok.connect(self._on_scan_finished)
        worker.failed.connect(self._on_scan_failed)
        worker.finished.connect(self._on_scan_stopped)
        self._worker = worker
        self._set_busy(True, "Memindai folder…")
        worker.start()

    def _start_analysis(self) -> None:
        if self._is_busy():
            return
        valid = [doc for doc in self._documents if doc.is_valid]
        if not valid:
            QMessageBox.information(
                self,
                "Tidak ada dokumen valid",
                "Muat folder PDF dan pastikan minimal satu dokumen valid.",
            )
            return

        self._progress.setRange(0, len(valid))
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._set_busy(True, f"Menganalisis {len(valid)} dokumen…")

        worker = AnalysisWorker(
            valid,
            MapPageDetector(threshold=self._settings.map_page_threshold),
            parent=self,
        )
        worker.progress.connect(self._on_analysis_progress)
        worker.document_done.connect(self._on_document_analysed)
        worker.finished_ok.connect(self._on_analysis_finished)
        worker.failed.connect(self._on_analysis_failed)
        worker.finished.connect(self._on_analysis_stopped)
        self._analysis_worker = worker
        worker.start()

    def _on_analysis_progress(self, current: int, total: int, name: str) -> None:
        if self._progress.maximum() != total:
            self._progress.setRange(0, max(total, 1))
        self._progress.setValue(current - 1)
        self._status_bar.showMessage(f"Menganalisis {current}/{total}: {name}")

    def _on_document_analysed(self, analysis: DocumentAnalysis) -> None:
        self._model.set_analysis(analysis)
        document = self._selected_document()
        if document is not None and document.key == analysis.info.key:
            self._preview.show_document(document, analysis=analysis)
        self._update_summary()

    def _on_analysis_finished(self, result: AnalysisResult) -> None:
        self._model.set_analyses(result)
        self._progress.setValue(self._progress.maximum())
        self._refresh_selected_preview()
        self._refresh_comparison_sources()
        self._persist_analysis(result)
        self._update_summary()

        message = (
            f"Analisis selesai: {result.map_found_count} dari "
            f"{result.analysed_count} dokumen memiliki halaman peta."
        )
        if result.error_count:
            message += f" {result.error_count} dokumen gagal dianalisis."
        self._status_bar.showMessage(message)
        logger.info(message)

    def _on_analysis_failed(self, message: str) -> None:
        self._status_bar.showMessage("Analisis gagal.")
        QMessageBox.critical(self, "Gagal menganalisis dokumen", message)

    def _on_analysis_stopped(self) -> None:
        worker = self._analysis_worker
        self._analysis_worker = None
        self._progress.setVisible(False)
        self._progress.setRange(0, 0)
        self._set_busy(False)
        if worker is not None:
            worker.deleteLater()
        self._update_analysis_buttons()

    def _on_scan_progress(self, current: int, total: int, name: str) -> None:
        if self._progress.maximum() != total:
            self._progress.setRange(0, max(total, 1))
        self._progress.setValue(current)
        self._status_bar.showMessage(f"Memeriksa {current}/{total}: {name}")

    def _on_scan_finished(self, result: ImportResult) -> None:
        self._documents = list(result.documents)
        self._model.set_documents(self._documents)
        self._comparison_view.clear()
        self._group_view.set_documents([])
        self._workspace_tabs.setTabEnabled(1, False)
        self._workspace_tabs.setTabEnabled(2, False)
        self._update_summary()
        self._empty_label.setVisible(not self._documents)
        self._select_first_row()

        message = (
            f"{result.valid_count} dari {result.total} PDF berhasil dibaca."
            if result.total
            else "Tidak ada file PDF di folder ini."
        )
        if result.invalid_count:
            message += f" {result.invalid_count} file gagal dibaca."
        message += " Klik “Deteksi Halaman Peta” untuk melanjutkan."
        self._status_bar.showMessage(message)
        logger.info(message)
        self._offer_saved_review()

    def _on_scan_failed(self, message: str) -> None:
        self._status_bar.showMessage("Pemindaian gagal.")
        QMessageBox.critical(self, "Gagal memindai folder", message)

    def _on_scan_stopped(self) -> None:
        worker = self._worker
        self._worker = None
        self._progress.setVisible(False)
        self._progress.setRange(0, 0)
        self._set_busy(False)
        if worker is not None:
            worker.deleteLater()

    def _remove_selected(self) -> None:
        document = self._selected_document()
        if document is None:
            return
        remaining = [doc for doc in self._documents if doc.key != document.key]
        self._documents = remaining
        self._model.set_documents(remaining)
        self._empty_label.setVisible(not remaining)
        self._update_summary()
        self._preview.clear()
        self._detail_label.setText("")
        self._status_bar.showMessage(
            f"{document.filename} dikeluarkan dari daftar (file asli tidak diubah)."
        )
        self._select_first_row()

    def _on_selection_changed(
        self, _selected: QItemSelection, _deselected: QItemSelection
    ) -> None:
        self._refresh_selected_preview()

    def _refresh_selected_preview(self) -> None:
        document = self._selected_document()
        if document is None:
            self._preview.clear()
            self._detail_label.setText("")
            return

        analysis = self._model.analysis_for(document)
        self._detail_label.setText(self._describe_document(document, analysis))
        self._preview.show_document(document, analysis=analysis)

        if not document.is_valid:
            self._status_bar.showMessage(
                f"{document.filename} — {document.status_label}: {document.error_message}"
            )
        elif analysis is not None and analysis.is_analysed:
            self._status_bar.showMessage(
                f"{document.filename} — halaman peta {analysis.map_label} "
                f"(skor {analysis.map_score:.2f})"
            )
        else:
            self._status_bar.showMessage(
                f"{document.filename} — {document.page_count} halaman"
            )

    def _persist_analysis(self, result: AnalysisResult) -> None:
        """Record document metadata and map pages in SQLite (PRD section 11).

        Only metadata is stored; descriptors stay in cache files because the PRD
        prefers files when they are more efficient.
        """
        try:
            with db.connect(self._database_path) as connection:
                for item in result.documents:
                    info = item.info
                    try:
                        fingerprint = file_fingerprint(info.path)
                    except OSError:
                        fingerprint = ""
                    db.upsert_document(
                        connection,
                        filename=info.filename,
                        original_path=str(info.path.resolve()),
                        file_hash=fingerprint,
                        page_count=info.page_count,
                        map_page=item.map_page_number,
                        processing_status=(
                            "gagal" if item.has_error else "dianalisis"
                        ),
                    )
        except Exception as exc:  # noqa: BLE001 - persistence must never crash the UI
            logger.warning("Gagal menyimpan ke database: %s", exc)

    def _refresh_comparison_sources(self) -> None:
        """Offer only documents whose map page was actually found."""
        comparables = [
            document
            for document in self._documents
            if (analysis := self._model.analysis_for(document)) is not None
            and analysis.map_page_number is not None
        ]
        self._comparison_view.set_documents(comparables)
        self._group_view.set_documents(comparables)
        self._workspace_tabs.setTabEnabled(1, len(comparables) >= 2)
        self._workspace_tabs.setTabEnabled(2, len(comparables) >= 2)

    def _select_document_by_key(self, key: str) -> None:
        for row in range(self._proxy.rowCount()):
            source = self._proxy.mapToSource(self._proxy.index(row, 0))
            document = source.data(DOCUMENT_ROLE)
            if document is not None and document.key == key:
                self._table.selectRow(row)
                self._workspace_tabs.setCurrentIndex(0)
                return

    # ------------------------------------------------------------- settings
    def _cluster_config(self) -> ClusterConfig:
        return ClusterConfig(
            link_threshold=self._settings.link_threshold,
            high_confidence_threshold=self._settings.high_confidence_threshold,
            prefilter_enabled=self._settings.prefilter_enabled,
        )

    def _on_settings_changed(self, settings: AppSettings) -> None:
        self._settings = settings
        self._group_view.set_cluster_config(self._cluster_config())
        self._status_bar.showMessage(
            "Pengaturan tersimpan. Jalankan ulang pengelompokan agar berlaku."
        )

    def _clear_analysis_cache(self) -> None:
        removed = self._analysis_cache.clear()
        self._settings_view.set_status(
            f"Cache analisis dibersihkan ({removed} entri)."
        )
        self._status_bar.showMessage(f"Cache analisis dibersihkan ({removed} entri).")

    # ------------------------------------------------------------ summarize
    def _on_review_changed(self) -> None:
        self._update_summary()
        review = self._group_view.current_review()
        if review is not None and review.total_groups:
            save_review(self._database_path, review, self._document_map())
            self._group_view.set_restore_available(True)

    def _document_map(self) -> dict[str, PdfInfo]:
        return {document.key: document for document in self._documents}

    def _restore_saved_review(self) -> None:
        restored = load_review(self._database_path, only_within=self._folder)
        if restored is None or restored.is_empty:
            QMessageBox.information(
                self, "Tidak ada hasil tersimpan", "Belum ada kelompok tersimpan."
            )
            return
        self._apply_restored(restored)

    def _apply_restored(self, restored: RestoredReview) -> None:
        documents = [
            info for info in self._document_map().values()
            if info.key in restored.documents
        ]
        if not documents:
            QMessageBox.information(
                self,
                "Hasil tersimpan tidak cocok",
                "Dokumen pada hasil tersimpan tidak ada di folder yang sedang dibuka.",
            )
            return

        self._group_view.adopt_review(restored.state, documents)
        self._workspace_tabs.setCurrentIndex(2)
        message = (
            f"Hasil tersimpan dimuat: {restored.state.total_groups} kelompok, "
            f"{len(restored.state.ungrouped)} belum terkelompok."
        )
        if restored.missing_paths:
            message += f" {len(restored.missing_paths)} file tidak ditemukan."
        self._status_bar.showMessage(message)

    def _offer_saved_review(self) -> None:
        """Ask before applying a saved review from an earlier session."""
        restored = load_review(self._database_path, only_within=self._folder)
        if restored is None or restored.is_empty:
            self._group_view.set_restore_available(False)
            return

        matched = [
            info for info in self._document_map().values()
            if info.key in restored.documents
        ]
        self._group_view.set_restore_available(bool(matched))
        if len(matched) < 2:
            return

        answer = QMessageBox.question(
            self,
            "Ada hasil pengelompokan tersimpan",
            f"Ditemukan hasil pengelompokan tersimpan untuk {len(matched)} dokumen "
            "di folder ini.\n\nMuat hasil tersebut?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._apply_restored(restored)

    # ------------------------------------------------------------- helpers
    def _selected_document(self) -> PdfInfo | None:
        indexes = self._table.selectionModel().selectedRows()
        if not indexes:
            return None
        source_index = self._proxy.mapToSource(indexes[0])
        return source_index.data(DOCUMENT_ROLE)

    def _select_first_row(self) -> None:
        if self._proxy.rowCount() > 0:
            self._table.selectRow(0)
        else:
            self._preview.clear()

    def _update_summary(self) -> None:
        total = len(self._documents)
        valid = sum(1 for doc in self._documents if doc.is_valid)
        pages = sum(doc.page_count for doc in self._documents if doc.is_valid)
        analysed = 0
        map_found = 0
        for document in self._documents:
            analysis = self._model.analysis_for(document)
            if analysis is None or not analysis.is_analysed:
                continue
            analysed += 1
            if analysis.map_page_number is not None:
                map_found += 1

        review = self._group_view.current_review()
        group_count = review.total_groups if review is not None else 0
        ungrouped_count = len(review.ungrouped) if review is not None else 0

        self._dashboard.update_summary(
            total=total,
            valid=valid,
            invalid=total - valid,
            pages=pages,
            map_found=map_found,
            analysed=analysed,
            status=self._current_status(),
            group_count=group_count,
            ungrouped_count=ungrouped_count,
        )

    def _current_status(self) -> str:
        if self._is_busy():
            return "Menganalisis" if self._analysis_worker is not None else "Memindai"
        if not self._documents:
            return "Kosong"
        if self._analysis_worker is None:
            analysed = sum(
                1
                for doc in self._documents
                if (a := self._model.analysis_for(doc)) is not None and a.is_analysed
            )
            if analysed:
                return "Selesai"
        return "Siap"

    def _is_busy(self) -> bool:
        return (
            self._worker is not None and self._worker.isRunning()
        ) or (
            self._analysis_worker is not None and self._analysis_worker.isRunning()
        )

    def _set_busy(self, busy: bool, message: str = "") -> None:
        for widget in (
            self._open_button,
            self._refresh_button,
            self._remove_button,
            self._filter_edit,
        ):
            widget.setEnabled(not busy)
        self._table.setEnabled(not busy)
        if message:
            self._status_bar.showMessage(message)
        if hasattr(self, "_dashboard"):
            self._update_summary()
        self._update_analysis_buttons()

    def _update_analysis_buttons(self) -> None:
        has_valid = any(doc.is_valid for doc in self._documents)
        self._detect_button.setEnabled(has_valid and not self._is_busy())

    @staticmethod
    def _describe_document(
        document: PdfInfo, analysis: DocumentAnalysis | None
    ) -> str:
        lines = [
            f"File: {document.filename}",
            f"Lokasi: {document.path}",
            f"Status: {document.status_label}",
        ]
        if document.is_valid:
            lines.append(f"Jumlah halaman: {document.page_count}")
        else:
            lines.append(f"Keterangan: {document.error_message}")

        if analysis is not None and analysis.is_analysed:
            lines.append("")
            lines.append(detection_summary(analysis))
        return "\n".join(lines)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._comparison_view.shutdown()
        self._group_view.shutdown()
        for worker in (self._worker, self._analysis_worker):
            if worker is not None and worker.isRunning():
                worker.requestInterruption()
                worker.wait(3000)
        super().closeEvent(event)
