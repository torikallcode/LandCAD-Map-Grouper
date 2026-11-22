from pathlib import Path

import numpy as np
import pytest

from core.map_page_detector import MapPageDetector
from core.pdf_reader import DocumentStatus, PdfInfo, inspect_pdf
from services.processing_service import (
    AnalysisResult,
    DocumentAnalysis,
    analyse_document,
    analyse_documents,
    detection_summary,
    render_pages,
    save_normalized,
)


@pytest.fixture
def sample_info(sample_pdfs) -> PdfInfo:
    return inspect_pdf(sample_pdfs[0])


# ------------------------------------------------------------------ rendering
def test_render_pages_returns_gray_arrays(sample_info):
    pages = render_pages(sample_info.path, dpi=72)
    assert len(pages) == sample_info.page_count
    assert all(page.ndim == 2 for page in pages)
    assert all(page.dtype == np.uint8 for page in pages)


def test_render_pages_respects_dpi(sample_info):
    low = render_pages(sample_info.path, dpi=60)
    high = render_pages(sample_info.path, dpi=110)
    assert high[0].shape[0] > low[0].shape[0]


# ------------------------------------------------------------------- analysis
def test_analyse_document_detects_map_on_second_page(sample_info):
    analysis = analyse_document(sample_info, MapPageDetector())

    assert analysis.is_analysed
    assert not analysis.has_error
    assert analysis.map_page_number == 2
    assert analysis.map_label == "Halaman 2"
    assert analysis.normalized is not None


def test_analyse_both_reference_documents_find_same_map_page(sample_pdfs):
    infos = [inspect_pdf(path) for path in sample_pdfs]
    result = analyse_documents(infos, MapPageDetector())

    assert isinstance(result, AnalysisResult)
    assert result.analysed_count == 2
    assert result.map_found_count == 2
    assert result.error_count == 0
    assert all(doc.map_page_number == 2 for doc in result.documents)


def test_analysis_scores_separate_admin_from_map(sample_info):
    analysis = analyse_document(sample_info, MapPageDetector())
    scores = {page.page_number: page.score for page in analysis.detection.pages}

    assert scores[2] > 0.6
    assert scores[1] < 0.4
    assert scores[2] - scores[1] > 0.3


def test_normalized_image_is_corrected_for_sideways_scan(sample_info):
    """The reference map is a portrait page scanned sideways.

    Correcting it therefore yields a landscape canvas whose labels read
    horizontally, while the stored original keeps the untouched portrait render.
    """
    analysis = analyse_document(sample_info, MapPageDetector())
    normalized = analysis.normalized

    assert normalized is not None
    original_height, original_width = normalized.original.shape
    assert original_height > original_width, "halaman asli memang portrait"

    height, width = normalized.image.shape
    assert width > height, "hasil koreksi membentuk kanvas landscape"
    assert any(step.startswith("rotate90") for step in normalized.steps)
    assert normalized.orientation.is_reliable


def test_analysis_stores_original_and_normalized_separately(sample_info):
    normalized = analyse_document(sample_info, MapPageDetector()).normalized
    assert normalized is not None
    assert normalized.original is not normalized.image
    assert normalized.original.shape != normalized.image.shape


def test_invalid_document_reports_error_without_raising(tmp_path):
    broken = tmp_path / "rusak.pdf"
    broken.write_bytes(b"bukan pdf")
    info = inspect_pdf(broken)

    analysis = analyse_document(info, MapPageDetector())

    assert analysis.has_error
    assert not analysis.is_analysed
    assert analysis.map_label == "Gagal"
    assert analysis.map_page_number is None


def test_analysis_continues_after_broken_file(tmp_path, sample_pdfs):
    broken = tmp_path / "rusak.pdf"
    broken.write_bytes(b"rusak")
    documents = [inspect_pdf(broken), inspect_pdf(sample_pdfs[0])]

    result = analyse_documents(documents, MapPageDetector())

    assert result.error_count == 1
    assert result.analysed_count == 1
    assert result.map_found_count == 1


def test_analysis_reports_progress(sample_pdfs):
    infos = [inspect_pdf(path) for path in sample_pdfs]
    events: list[tuple[int, int, str]] = []

    analyse_documents(
        infos, MapPageDetector(), progress=lambda c, t, n: events.append((c, t, n))
    )

    assert [event[0] for event in events] == [1, 2]
    assert all(event[1] == 2 for event in events)


def test_analysis_supports_cancellation(sample_pdfs):
    infos = [inspect_pdf(path) for path in sample_pdfs]
    result = analyse_documents(infos, MapPageDetector(), should_cancel=lambda: True)

    assert result.cancelled
    assert result.documents == ()


def test_analysis_empty_list():
    result = analyse_documents([], MapPageDetector())
    assert result.documents == ()
    assert result.analysed_count == 0


# --------------------------------------------------------------------- export
def test_save_normalized_writes_two_images(sample_info, tmp_path):
    analysis = analyse_document(sample_info, MapPageDetector())
    export = save_normalized(analysis, tmp_path)

    assert export is not None
    assert export.original_path.exists()
    assert export.normalized_path.exists()
    assert export.original_path.name.endswith("_asli.png")
    assert export.normalized_path.name.endswith("_normal.png")
    assert export.steps


def test_save_normalized_returns_none_without_image():
    info = PdfInfo(
        path=Path("x.pdf"), filename="x.pdf", size_bytes=0, modified_at=0.0,
        page_count=0, status=DocumentStatus.CORRUPT,
    )
    analysis = DocumentAnalysis(info=info, error="rusak")
    assert save_normalized(analysis, Path("unused")) is None


# -------------------------------------------------------------------- summary
def test_detection_summary_contains_key_facts(sample_info):
    summary = detection_summary(analyse_document(sample_info, MapPageDetector()))
    assert "Halaman peta: Halaman 2" in summary
    assert "Detektor:" in summary
    assert "Langkah normalisasi:" in summary
    assert "Orientasi:" in summary


def test_detection_summary_for_unanalysed_document():
    info = PdfInfo(
        path=Path("y.pdf"), filename="y.pdf", size_bytes=0, modified_at=0.0,
        page_count=0, status=DocumentStatus.CORRUPT, message="gagal dibuka",
    )
    summary = detection_summary(DocumentAnalysis(info=info, error="gagal dibuka"))
    assert "gagal dibuka" in summary
