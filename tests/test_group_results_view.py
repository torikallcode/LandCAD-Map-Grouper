import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.clustering_engine import ClusteringResult, Group, SimilarityEdge  # noqa: E402
from core.pdf_reader import PdfInfo, DocumentStatus  # noqa: E402
from services.grouping_service import GroupedDocument, GroupingResult  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def view(qapp):
    from app.group_results_view import GroupResultsView

    widget = GroupResultsView()
    yield widget
    widget.shutdown()
    widget.deleteLater()


def _info(name: str) -> PdfInfo:
    from pathlib import Path

    return PdfInfo(
        path=Path("C:/data") / name,
        filename=name,
        size_bytes=1,
        modified_at=0.0,
        page_count=2,
        status=DocumentStatus.OK,
    )


def _grouped_document(name: str) -> GroupedDocument:
    import numpy as np

    class _Features:
        is_empty = False

    info = _info(name)
    return GroupedDocument(
        info=info,
        image=np.zeros((8, 8), np.uint8),
        features=_Features(),
    )


def _result(score: float = 70.0) -> GroupingResult:
    docs = [_grouped_document("SU_057.pdf"), _grouped_document("SU_058.pdf")]
    keys = tuple(doc.key for doc in docs)
    clustering = ClusteringResult(
        groups=(
            Group(
                group_id=1,
                member_keys=keys,
                confidence="Sedang",
                min_pair_score=score,
                needs_review=True,
            ),
        ),
        ungrouped=(),
        edges=(SimilarityEdge(a_key=keys[0], b_key=keys[1], score=score),),
    )
    return GroupingResult(clustering=clustering, documents=tuple(docs))


# --------------------------------------------------------------------- basics
def test_view_starts_empty(view):
    assert view.current_result() is None
    assert not view._cluster_button.isEnabled()


def test_set_documents_enables_cluster_button(view):
    view.set_documents([_info("a.pdf"), _info("b.pdf")])
    assert view._cluster_button.isEnabled()


def test_single_document_disables_button(view):
    view.set_documents([_info("a.pdf")])
    assert not view._cluster_button.isEnabled()


# ------------------------------------------------------------------ populate
def test_populate_builds_group_tree(view):
    result = _result()
    view.set_documents([d.info for d in result.documents])
    view._on_finished(result)

    assert view._tree.topLevelItemCount() == 1
    parent = view._tree.topLevelItem(0)
    assert "KELOMPOK_001" in parent.text(0)
    assert parent.childCount() == 2
    assert parent.isExpanded()


def test_populate_includes_ungrouped_section(view):
    docs = [_grouped_document("lonely.pdf")]
    clustering = ClusteringResult(
        groups=(), ungrouped=(docs[0].key,), edges=()
    )
    result = GroupingResult(clustering=clustering, documents=tuple(docs))

    view.set_documents([d.info for d in docs])
    view._on_finished(result)

    labels = [view._tree.topLevelItem(i).text(0) for i in range(view._tree.topLevelItemCount())]
    assert any("Belum Terkelompok" in label for label in labels)


def test_status_label_reports_counts(view):
    result = _result()
    view.set_documents([d.info for d in result.documents])
    view._on_finished(result)
    assert "1 kelompok" in view._status_label.text()


def test_group_confidence_colours_are_applied(view):
    result = _result()
    view.set_documents([d.info for d in result.documents])
    view._on_finished(result)

    parent = view._tree.topLevelItem(0)
    colour = parent.foreground(0).color()
    assert colour.isValid()


# --------------------------------------------------------------------- detail
def test_selecting_member_shows_detail(view):
    result = _result()
    view.set_documents([d.info for d in result.documents])
    view._on_finished(result)

    parent = view._tree.topLevelItem(0)
    child = parent.child(0)
    child.setSelected(True)
    view._on_selection_changed()

    text = view._detail.text()
    assert "Kelompok: KELOMPOK_001" in text
    assert "Dokumen dalam kelompok: 2" in text
    assert "Pasangan terukur" in text


def test_selecting_ungrouped_member_shows_status(view):
    docs = [_grouped_document("lonely.pdf")]
    result = GroupingResult(
        clustering=ClusteringResult(groups=(), ungrouped=(docs[0].key,), edges=()),
        documents=tuple(docs),
    )
    view.set_documents([d.info for d in docs])
    view._on_finished(result)

    parent = view._tree.topLevelItem(0)
    parent.child(0).setSelected(True)
    view._on_selection_changed()

    assert "belum terkelompok" in view._detail.text().lower()


def test_document_selected_signal_emitted(view):
    result = _result()
    view.set_documents([d.info for d in result.documents])
    view._on_finished(result)

    captured = []
    view.document_selected.connect(captured.append)
    # Selecting an item already triggers ``itemSelectionChanged``, so the handler
    # must not be called manually as well.
    view._tree.topLevelItem(0).child(0).setSelected(True)

    assert len(captured) == 1


# -------------------------------------------------------------------- errors
def test_failure_shows_message(view):
    view.set_documents([_info("a.pdf"), _info("b.pdf")])
    view._on_failed("contoh galat")
    assert "gagal" in view._status_label.text().lower()


def test_grouping_needs_two_documents(view):
    view.set_documents([_info("a.pdf")])
    view.start_grouping()
    assert view._worker is None
