import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.feature_extractor import extract_features  # noqa: E402
from services.comparison_service import (  # noqa: E402
    ComparableMap,
    ComparisonResult,
    compare_maps,
    prepare_comparable,
)


@pytest.fixture(scope="module")
def qapp():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def comparison_view(qapp):
    from app.comparison_view import ComparisonView

    view = ComparisonView()
    yield view
    view.shutdown()
    view.deleteLater()


def _infos(sample_pdfs):
    from core.pdf_reader import inspect_pdf

    return [inspect_pdf(path) for path in sample_pdfs]


def _result(sample_pdfs, tmp_path) -> ComparisonResult:
    maps = [prepare_comparable(info, cache_dir=tmp_path) for info in _infos(sample_pdfs)]
    return compare_maps(maps[0], maps[1])


# ---------------------------------------------------------------- population
def test_view_starts_without_documents(comparison_view):
    assert comparison_view._doc_a_combo.count() == 0
    assert not comparison_view._compare_button.isEnabled()


def test_set_documents_populates_both_combos(comparison_view, sample_pdfs):
    infos = _infos(sample_pdfs)
    comparison_view.set_documents(infos)

    assert comparison_view._doc_a_combo.count() == len(infos)
    assert comparison_view._doc_b_combo.count() == len(infos)
    assert comparison_view._doc_a_combo.currentText() != comparison_view._doc_b_combo.currentText()


def test_compare_disabled_with_single_document(comparison_view, sample_pdfs):
    comparison_view.set_documents([_infos(sample_pdfs)[0]])
    assert not comparison_view._compare_button.isEnabled()


def test_compare_disabled_when_same_document_selected(comparison_view, sample_pdfs):
    infos = _infos(sample_pdfs)
    comparison_view.set_documents(infos)
    comparison_view._doc_b_combo.setCurrentIndex(comparison_view._doc_a_combo.currentIndex())
    assert not comparison_view._compare_button.isEnabled()


def test_swap_reverses_selection(comparison_view, sample_pdfs):
    infos = _infos(sample_pdfs)
    comparison_view.set_documents(infos)
    before_a = comparison_view._doc_a_combo.currentText()
    before_b = comparison_view._doc_b_combo.currentText()

    comparison_view.swap_documents()

    assert comparison_view._doc_a_combo.currentText() == before_b
    assert comparison_view._doc_b_combo.currentText() == before_a


def test_clear_resets_view(comparison_view, sample_pdfs):
    comparison_view.set_documents(_infos(sample_pdfs))
    comparison_view.clear()
    assert comparison_view.current_result() is None


# ------------------------------------------------------------------ rendering
def test_finished_result_renders_overlay(comparison_view, sample_pdfs, tmp_path):
    result = _result(sample_pdfs, tmp_path)
    comparison_view._on_finished(result)

    assert comparison_view.current_result() is result
    assert comparison_view._pixmap is not None
    assert not comparison_view._pixmap.isNull()
    assert "Kecocokan fitur:" in comparison_view._summary_label.text()


def test_side_by_side_mode_switches_image(comparison_view, sample_pdfs, tmp_path):
    result = _result(sample_pdfs, tmp_path)
    comparison_view._on_finished(result)

    comparison_view._mode_combo.setCurrentIndex(1)
    side_width = comparison_view._pixmap.width()

    comparison_view._mode_combo.setCurrentIndex(0)
    overlay_width = comparison_view._pixmap.width()

    assert side_width > overlay_width


def test_failed_result_shows_message(comparison_view, sample_pdfs, tmp_path):
    maps = [prepare_comparable(info, cache_dir=tmp_path) for info in _infos(sample_pdfs)]
    blank_features = extract_features(np.full((200, 200), 255, np.uint8))
    blank = ComparableMap(
        info=maps[0].info,
        analysis=maps[0].analysis,
        image=np.full((200, 200), 255, np.uint8),
        features=blank_features,
    )
    comparison_view._on_finished(compare_maps(maps[0], blank))

    assert comparison_view._pixmap is None
    assert "Fitur ORB" in comparison_view._summary_label.text() or "gagal" in comparison_view._summary_label.text().lower()


def test_worker_failure_is_surfaced(comparison_view):
    comparison_view._on_failed("contoh kegagalan")
    assert comparison_view.current_result() is None
    assert "contoh kegagalan" in comparison_view._summary_label.text()


def test_start_comparison_requires_two_documents(comparison_view, sample_pdfs):
    comparison_view.set_documents([_infos(sample_pdfs)[0]])
    comparison_view.start_comparison()
    assert comparison_view._worker is None


def test_start_comparison_rejects_identical_documents(comparison_view, sample_pdfs):
    infos = _infos(sample_pdfs)
    comparison_view.set_documents(infos)
    comparison_view._doc_b_combo.setCurrentIndex(comparison_view._doc_a_combo.currentIndex())
    comparison_view.start_comparison()
    assert comparison_view._worker is None


# ------------------------------------------------------------ worker contract
def test_comparison_worker_emits_result(qapp, sample_pdfs, tmp_path):
    """The worker must deliver a result across threads.

    ``finished_ok`` is queued to the receiving thread, so the event loop has to
    run for the slot to fire; ``wait()`` alone would leave ``results`` empty.
    """
    from PySide6.QtCore import QEventLoop, QTimer

    from app.comparison_worker import ComparisonWorker

    infos = _infos(sample_pdfs)
    results = []
    errors = []

    worker = ComparisonWorker(infos[0], infos[1], cache_dir=tmp_path)
    worker.finished_ok.connect(results.append)
    worker.failed.connect(errors.append)

    loop = QEventLoop()
    worker.finished.connect(loop.quit)
    QTimer.singleShot(120_000, loop.quit)
    worker.start()
    loop.exec()
    worker.wait(5000)

    assert not errors
    assert len(results) == 1
    assert results[0].is_aligned
