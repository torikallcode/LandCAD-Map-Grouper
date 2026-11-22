from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.models import (  # noqa: E402
    ANALYSIS_ROLE,
    DOCUMENT_ROLE,
    Column,
    DocumentFilterProxyModel,
    DocumentTableModel,
)
from core.pdf_reader import DocumentStatus, PdfInfo  # noqa: E402
from services.processing_service import (  # noqa: E402
    AnalysisResult,
    DocumentAnalysis,
)


@pytest.fixture(scope="module")
def qapp():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


def _doc(
    name: str,
    pages: int = 2,
    size: int = 1000,
    status: DocumentStatus = DocumentStatus.OK,
) -> PdfInfo:
    return PdfInfo(
        path=Path("C:/data") / name,
        filename=name,
        size_bytes=size,
        modified_at=0.0,
        page_count=pages,
        status=status,
        message="" if status is DocumentStatus.OK else "gagal",
    )


def _analysis(
    info: PdfInfo, map_page: int | None = 1, score: float = 0.85
) -> DocumentAnalysis:
    from core.map_page_detector import MapDetectionResult, PageAssessment, PageFeatures

    pages = []
    for index in range(info.page_count):
        is_map = map_page is not None and index == map_page
        pages.append(
            PageAssessment(
                page_index=index,
                is_map=is_map,
                score=score if is_map else 0.12,
                features=PageFeatures(width=100, height=140),
            )
        )
    detection = MapDetectionResult(pages=tuple(pages), map_page=map_page)
    return DocumentAnalysis(info=info, detection=detection)


# --------------------------------------------------------------------- basics
def test_model_exposes_rows_and_columns(qapp):
    model = DocumentTableModel([_doc("SU_057.pdf"), _doc("SU_058.pdf")])
    assert model.rowCount() == 2
    assert model.columnCount() == 7


def test_model_headers(qapp):
    model = DocumentTableModel([_doc("a.pdf")])
    headers = [
        model.headerData(i, Qt.Orientation.Horizontal)
        for i in range(model.columnCount())
    ]
    assert headers == [
        "No",
        "Nama File",
        "Halaman",
        "Halaman Peta",
        "Skor",
        "Ukuran",
        "Status",
    ]


def test_model_document_role(qapp):
    model = DocumentTableModel([_doc("SU_057.pdf")])
    index = model.index(0, Column.NAME)
    assert index.data(Qt.ItemDataRole.DisplayRole) == "SU_057.pdf"
    assert index.data(DOCUMENT_ROLE).filename == "SU_057.pdf"
    assert index.data(Qt.ItemDataRole.ToolTipRole).startswith("C:")


def test_model_shows_dash_for_invalid_page_count(qapp):
    model = DocumentTableModel(
        [_doc("rusak.pdf", pages=0, status=DocumentStatus.CORRUPT)]
    )
    assert model.index(0, Column.PAGES).data() == "-"
    assert model.index(0, Column.STATUS).data() == "Rusak"


def test_model_set_documents_resets(qapp):
    model = DocumentTableModel([_doc("a.pdf")])
    model.set_documents([_doc("b.pdf"), _doc("c.pdf")])
    assert model.rowCount() == 2
    assert model.document_at(0).filename == "b.pdf"
    model.clear()
    assert model.rowCount() == 0


# ------------------------------------------------------------ phase 2 columns
def test_map_columns_are_dash_before_analysis(qapp):
    model = DocumentTableModel([_doc("a.pdf")])
    assert model.index(0, Column.MAP_PAGE).data() == "-"
    assert model.index(0, Column.SCORE).data() == "-"


def test_map_columns_show_analysis_result(qapp):
    info = _doc("a.pdf")
    model = DocumentTableModel([info])
    model.set_analyses(AnalysisResult(documents=(_analysis(info),)))

    assert model.index(0, Column.MAP_PAGE).data() == "Halaman 2"
    assert model.index(0, Column.SCORE).data() == "0.85"


def test_analysis_role_returns_analysis(qapp):
    info = _doc("a.pdf")
    model = DocumentTableModel([info])
    model.set_analyses(AnalysisResult(documents=(_analysis(info),)))

    analysis = model.index(0, Column.NAME).data(ANALYSIS_ROLE)
    assert analysis is not None
    assert analysis.map_page_number == 2


def test_set_analysis_updates_single_row(qapp):
    info = _doc("a.pdf")
    model = DocumentTableModel([info])
    model.set_analysis(_analysis(info))
    assert model.analysis_for(info) is not None
    assert model.index(0, Column.MAP_PAGE).data() == "Halaman 2"


def test_map_page_label_when_not_found(qapp):
    info = _doc("a.pdf")
    model = DocumentTableModel([info])
    model.set_analyses(
        AnalysisResult(documents=(_analysis(info, map_page=None, score=0.1),))
    )
    assert model.index(0, Column.MAP_PAGE).data() == "Tidak ditemukan"


def test_failed_analysis_shows_error_label(qapp):
    info = _doc("a.pdf")
    model = DocumentTableModel([info])
    model.set_analyses(
        AnalysisResult(documents=(DocumentAnalysis(info=info, error="gagal render"),))
    )
    assert model.index(0, Column.MAP_PAGE).data() == "Gagal"
    tooltip = model.index(0, Column.NAME).data(Qt.ItemDataRole.ToolTipRole)
    assert "gagal render" in tooltip


def test_clear_analyses_removes_results(qapp):
    info = _doc("a.pdf")
    model = DocumentTableModel([info])
    model.set_analysis(_analysis(info))
    model.clear_analyses()
    assert model.analysis_for(info) is None
    assert model.index(0, Column.MAP_PAGE).data() == "-"


def test_set_documents_clears_stale_analyses(qapp):
    info = _doc("a.pdf")
    model = DocumentTableModel([info])
    model.set_analysis(_analysis(info))
    model.set_documents([info])
    assert model.analysis_for(info) is None


# ---------------------------------------------------------------------- proxy
def test_proxy_filters_by_name_and_status(qapp):
    source = DocumentTableModel(
        [
            _doc("SU_057.pdf"),
            _doc("SU_058.pdf"),
            _doc("rusak.pdf", 0, 0, DocumentStatus.CORRUPT),
        ]
    )
    proxy = DocumentFilterProxyModel()
    proxy.setSourceModel(source)
    assert proxy.rowCount() == 3

    proxy.set_filter_text("058")
    assert proxy.rowCount() == 1

    proxy.set_filter_text("rusak")
    assert proxy.rowCount() == 1

    proxy.set_filter_text("")
    assert proxy.rowCount() == 3


def test_proxy_filters_by_map_label(qapp):
    found = _doc("a.pdf")
    missing = _doc("b.pdf")
    source = DocumentTableModel([found, missing])
    source.set_analyses(
        AnalysisResult(
            documents=(
                _analysis(found, map_page=1),
                _analysis(missing, map_page=None, score=0.1),
            )
        )
    )
    proxy = DocumentFilterProxyModel()
    proxy.setSourceModel(source)

    proxy.set_filter_text("tidak ditemukan")
    assert proxy.rowCount() == 1


def test_proxy_sorts_filenames_naturally(qapp):
    source = DocumentTableModel(
        [_doc("SU_10.pdf"), _doc("SU_2.pdf"), _doc("SU_1.pdf")]
    )
    proxy = DocumentFilterProxyModel()
    proxy.setSourceModel(source)
    proxy.sort(Column.NAME, Qt.SortOrder.AscendingOrder)

    names = [proxy.index(row, Column.NAME).data() for row in range(proxy.rowCount())]
    assert names == ["SU_1.pdf", "SU_2.pdf", "SU_10.pdf"]


def test_proxy_sorts_by_score(qapp):
    low = _doc("low.pdf")
    high = _doc("high.pdf")
    source = DocumentTableModel([low, high])
    source.set_analyses(
        AnalysisResult(
            documents=(
                _analysis(low, map_page=1, score=0.2),
                _analysis(high, map_page=1, score=0.9),
            )
        )
    )
    proxy = DocumentFilterProxyModel()
    proxy.setSourceModel(source)
    proxy.sort(Column.SCORE, Qt.SortOrder.DescendingOrder)

    assert proxy.index(0, Column.NAME).data() == "high.pdf"
