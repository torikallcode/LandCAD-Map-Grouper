from __future__ import annotations

from enum import IntEnum

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor

from core.pdf_reader import PdfInfo
from services.processing_service import AnalysisResult, DocumentAnalysis
from utils.file_utils import format_size, natural_sort_key

DOCUMENT_ROLE = Qt.ItemDataRole.UserRole + 1
ANALYSIS_ROLE = Qt.ItemDataRole.UserRole + 2


class Column(IntEnum):
    NUMBER = 0
    NAME = 1
    PAGES = 2
    MAP_PAGE = 3
    SCORE = 4
    SIZE = 5
    STATUS = 6


HEADERS = ("No", "Nama File", "Halaman", "Halaman Peta", "Skor", "Ukuran", "Status")

_INVALID_COLOR = QColor(178, 34, 34)
_VALID_COLOR = QColor(21, 115, 71)
_MUTED_COLOR = QColor(122, 130, 144)
_MAP_PENDING_COLOR = QColor(160, 106, 20)


class DocumentTableModel(QAbstractTableModel):
    """Read-only table model exposing documents plus map-detection results."""

    def __init__(
        self,
        documents: list[PdfInfo] | None = None,
        analyses: dict[str, DocumentAnalysis] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._documents: list[PdfInfo] = list(documents or [])
        self._analyses: dict[str, DocumentAnalysis] = dict(analyses or {})

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._documents)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(HEADERS):
            return HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._documents):
            return None

        document = self._documents[index.row()]
        analysis = self._analyses.get(document.key)
        column = Column(index.column())

        if role == DOCUMENT_ROLE:
            return document
        if role == ANALYSIS_ROLE:
            return analysis
        if role == Qt.ItemDataRole.DisplayRole:
            return self._display_value(document, analysis, column, index.row())
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if column in (Column.NUMBER, Column.PAGES, Column.SCORE, Column.SIZE):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole:
            return self._foreground(document, analysis, column)
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(document, analysis)
        return None

    def set_documents(self, documents: list[PdfInfo]) -> None:
        self.beginResetModel()
        self._documents = list(documents)
        self._analyses = {}
        self.endResetModel()

    def set_analyses(self, result: AnalysisResult) -> None:
        self.beginResetModel()
        self._analyses = {doc.info.key: doc for doc in result.documents}
        self.endResetModel()

    def set_analysis(self, analysis: DocumentAnalysis) -> None:
        key = analysis.info.key
        self._analyses[key] = analysis
        row = next(
            (i for i, doc in enumerate(self._documents) if doc.key == key), None
        )
        if row is None:
            return
        left = self.index(row, 0)
        right = self.index(row, len(HEADERS) - 1)
        self.dataChanged.emit(left, right)

    def clear_analyses(self) -> None:
        self.beginResetModel()
        self._analyses = {}
        self.endResetModel()

    def clear(self) -> None:
        self.set_documents([])

    def documents(self) -> list[PdfInfo]:
        return list(self._documents)

    def document_at(self, row: int) -> PdfInfo | None:
        if 0 <= row < len(self._documents):
            return self._documents[row]
        return None

    def analysis_for(self, document: PdfInfo) -> DocumentAnalysis | None:
        return self._analyses.get(document.key)

    def analysis_at(self, row: int) -> DocumentAnalysis | None:
        document = self.document_at(row)
        return self.analysis_for(document) if document else None

    @staticmethod
    def _display_value(
        document: PdfInfo,
        analysis: DocumentAnalysis | None,
        column: Column,
        row: int,
    ) -> str:
        if column is Column.NUMBER:
            return str(row + 1)
        if column is Column.NAME:
            return document.filename
        if column is Column.PAGES:
            return str(document.page_count) if document.is_valid else "-"
        if column is Column.MAP_PAGE:
            if analysis is None:
                return "-"
            return analysis.map_label
        if column is Column.SCORE:
            if analysis is None or not analysis.is_analysed:
                return "-"
            return f"{analysis.map_score:.2f}"
        if column is Column.SIZE:
            return format_size(document.size_bytes)
        return document.status_label

    @staticmethod
    def _foreground(
        document: PdfInfo,
        analysis: DocumentAnalysis | None,
        column: Column,
    ):
        if not document.is_valid:
            return _INVALID_COLOR
        if column is Column.STATUS:
            return _VALID_COLOR
        if column is Column.MAP_PAGE:
            if analysis is None:
                return _MUTED_COLOR
            if analysis.has_error:
                return _INVALID_COLOR
            if not analysis.is_analysed:
                return _MUTED_COLOR
            if analysis.map_page_number is None:
                return _MAP_PENDING_COLOR
            return None
        return None

    @staticmethod
    def _tooltip(document: PdfInfo, analysis: DocumentAnalysis | None) -> str:
        lines = [str(document.path), f"Status: {document.status_label}"]
        if document.is_valid:
            lines.append(f"Halaman: {document.page_count}")
        else:
            lines.append(f"Keterangan: {document.error_message}")
        lines.append(f"Ukuran: {format_size(document.size_bytes)}")

        if analysis is not None:
            if analysis.has_error:
                lines.append(f"Analisis: gagal ({analysis.error})")
            elif analysis.is_analysed:
                lines.append(f"Halaman peta: {analysis.map_label}")
                lines.append(f"Skor kemiripan peta: {analysis.map_score:.2f}")
                if analysis.normalized is not None:
                    skew = analysis.normalized.skew
                    lines.append(
                        f"Kemiringan: {skew.angle:+.2f}° "
                        f"(keyakinan {skew.confidence:.2f})"
                    )
        return "\n".join(lines)


class DocumentFilterProxyModel(QSortFilterProxyModel):
    """Filters by filename/status/map label and sorts naturally."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._filter_text = ""
        self.setDynamicSortFilter(True)
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def set_filter_text(self, text: str) -> None:
        normalized = text.strip().lower()
        if normalized == self._filter_text:
            return
        self._filter_text = normalized
        self.beginFilterChange()
        self.endFilterChange()

    def filter_text(self) -> str:
        return self._filter_text

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        if not self._filter_text:
            return True
        model = self.sourceModel()
        document = model.document_at(source_row) if isinstance(model, DocumentTableModel) else None
        if document is None:
            return True
        analysis = model.analysis_for(document)
        map_label = analysis.map_label if analysis is not None else ""
        haystack = f"{document.filename} {document.status_label} {map_label}".lower()
        return all(token in haystack for token in self._filter_text.split())

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:  # noqa: N802
        model = self.sourceModel()
        if not isinstance(model, DocumentTableModel):
            return super().lessThan(left, right)

        left_doc = model.document_at(left.row())
        right_doc = model.document_at(right.row())
        if left_doc is None or right_doc is None:
            return super().lessThan(left, right)

        column = Column(left.column())
        if column is Column.NAME:
            return natural_sort_key(left_doc.filename) < natural_sort_key(right_doc.filename)
        if column is Column.PAGES:
            return left_doc.page_count < right_doc.page_count
        if column is Column.SIZE:
            return left_doc.size_bytes < right_doc.size_bytes
        if column is Column.STATUS:
            return left_doc.status_label < right_doc.status_label
        if column is Column.SCORE:
            left_analysis = model.analysis_for(left_doc)
            right_analysis = model.analysis_for(right_doc)
            return (left_analysis.map_score if left_analysis else 0.0) < (
                right_analysis.map_score if right_analysis else 0.0
            )
        if column is Column.MAP_PAGE:
            left_analysis = model.analysis_for(left_doc)
            right_analysis = model.analysis_for(right_doc)
            left_page = left_analysis.map_page_number if left_analysis else None
            right_page = right_analysis.map_page_number if right_analysis else None
            return (left_page or 0) < (right_page or 0)
        return left.row() < right.row()
