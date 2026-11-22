import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.clustering_engine import ClusteringResult, Group, SimilarityEdge  # noqa: E402
from core.pdf_reader import DocumentStatus, PdfInfo  # noqa: E402
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

    return GroupedDocument(
        info=_info(name), image=np.zeros((8, 8), np.uint8), features=_Features()
    )


def _result(score: float = 70.0, high_score: float = 90.0) -> GroupingResult:
    docs = [
        _grouped_document("SU_057.pdf"),
        _grouped_document("SU_058.pdf"),
        _grouped_document("SU_059.pdf"),
        _grouped_document("SU_060.pdf"),
    ]
    keys = [doc.key for doc in docs]
    clustering = ClusteringResult(
        groups=(
            Group(
                group_id=1,
                member_keys=(keys[0], keys[1]),
                confidence="Sedang",
                min_pair_score=score,
                needs_review=True,
            ),
            Group(
                group_id=2,
                member_keys=(keys[2], keys[3]),
                confidence="Tinggi",
                min_pair_score=high_score,
                needs_review=False,
            ),
        ),
        ungrouped=(),
        edges=(
            SimilarityEdge(a_key=keys[0], b_key=keys[1], score=score, match_count=300),
            SimilarityEdge(
                a_key=keys[2], b_key=keys[3], score=high_score, match_count=500
            ),
        ),
    )
    return GroupingResult(clustering=clustering, documents=tuple(docs))


# --------------------------------------------------------------------- basics
def test_view_starts_empty(view):
    assert view.current_result() is None
    assert view.current_review() is None
    assert not view._cluster_button.isEnabled()


def test_set_documents_enables_cluster_button(view):
    view.set_documents([_info("a.pdf"), _info("b.pdf")])
    assert view._cluster_button.isEnabled()


def test_single_document_disables_button(view):
    view.set_documents([_info("a.pdf")])
    assert not view._cluster_button.isEnabled()


def test_export_buttons_disabled_before_review(view):
    view.set_documents([_info("a.pdf"), _info("b.pdf")])
    assert not view._export_button.isEnabled()
    assert not view._copy_button.isEnabled()


# ------------------------------------------------------------------ populate
def test_finished_builds_review_state(view):
    result = _result()
    view.set_documents([d.info for d in result.documents])
    view._on_finished(result)

    state = view.current_review()
    assert state is not None
    assert state.total_groups == 2
    assert state.verified_count == 0


def test_populate_builds_group_tree(view):
    result = _result()
    view.set_documents([d.info for d in result.documents])
    view._on_finished(result)

    assert view._tree.topLevelItemCount() == 2
    parent = view._tree.topLevelItem(0)
    assert "KELOMPOK_001" in parent.text(0)
    assert parent.childCount() == 2
    assert parent.isExpanded()


def test_export_enabled_after_grouping(view):
    result = _result()
    view.set_documents([d.info for d in result.documents])
    view._on_finished(result)

    assert view._export_button.isEnabled()
    assert view._copy_button.isEnabled()


def test_populate_includes_ungrouped_section(view):
    docs = [_grouped_document("lonely.pdf")]
    result = GroupingResult(
        clustering=ClusteringResult(groups=(), ungrouped=(docs[0].key,), edges=()),
        documents=tuple(docs),
    )

    view.set_documents([d.info for d in docs])
    view._on_finished(result)

    labels = [
        view._tree.topLevelItem(i).text(0)
        for i in range(view._tree.topLevelItemCount())
    ]
    assert any("Belum Terkelompok" in label for label in labels)


def test_group_confidence_colours_are_applied(view):
    result = _result()
    view.set_documents([d.info for d in result.documents])
    view._on_finished(result)

    colour = view._tree.topLevelItem(0).foreground(0).color()
    assert colour.isValid()


# ------------------------------------------------------------------ verifying
def test_verify_selected_group(view):
    result = _result()
    view.set_documents([d.info for d in result.documents])
    view._on_finished(result)

    view._tree.topLevelItem(0).setSelected(True)
    view.verify_selected_group()

    state = view.current_review()
    assert state.group_by_id(1).verified
    assert state.group_by_id(1).status_label == "Terverifikasi"
    assert view._tree.topLevelItem(0).text(2) == "Terverifikasi"


def test_unverify_selected_group(view):
    result = _result()
    view.set_documents([d.info for d in result.documents])
    view._on_finished(result)

    view._tree.topLevelItem(0).setSelected(True)
    view.verify_selected_group()
    view.unverify_selected_group()

    assert not view.current_review().group_by_id(1).verified


def test_verify_without_selection_reports_status(view):
    result = _result()
    view.set_documents([d.info for d in result.documents])
    view._on_finished(result)
    view._tree.clearSelection()
    view._selected_group_id = None

    view.verify_selected_group()
    assert "Pilih kelompok" in view._status_label.text()


# -------------------------------------------------------------------- merging
def test_merge_selected_group_into_target(view):
    result = _result()
    view.set_documents([d.info for d in result.documents])
    view._on_finished(result)

    view._tree.topLevelItem(0).setSelected(True)
    view._merge_target.setCurrentIndex(view._merge_target.findData(2))
    view.merge_into()

    state = view.current_review()
    assert state.total_groups == 1
    assert state.group_by_id(2).size == 4


def test_merge_requires_different_target(view):
    result = _result()
    view.set_documents([d.info for d in result.documents])
    view._on_finished(result)

    view._tree.topLevelItem(0).setSelected(True)
    view._merge_target.setCurrentIndex(view._merge_target.findData(1))
    view.merge_into()

    assert "harus berbeda" in view._status_label.text()


# ------------------------------------------------------------------ splitting
def test_split_member_moves_to_ungrouped(view):
    result = _result()
    view.set_documents([d.info for d in result.documents])
    view._on_finished(result)

    view._tree.topLevelItem(0).child(0).setSelected(True)
    view.split_selected_document()

    state = view.current_review()
    assert state.group_by_id(1).size == 1
    assert len(state.ungrouped) == 1


def test_split_after_verify_still_targets_the_document(view):
    """Regression: verifying rebuilt the tree and silently selected the header.

    A tree can report the previously selected parent before the new child, so the
    lookup must scan for the row that actually carries a document key.
    """
    result = _result()
    view.set_documents([d.info for d in result.documents])
    view._on_finished(result)

    view._tree.topLevelItem(0).setSelected(True)
    view.verify_selected_group()

    view._tree.topLevelItem(0).child(0).setSelected(True)
    assert view._current_document_key() is not None

    view.split_selected_document()

    state = view.current_review()
    assert state.group_by_id(1).size == 1
    assert len(state.ungrouped) == 1
    labels = [
        view._tree.topLevelItem(i).text(0)
        for i in range(view._tree.topLevelItemCount())
    ]
    assert any("Belum Terkelompok" in label for label in labels)


def test_group_header_selection_still_resolves_group(view):
    result = _result()
    view.set_documents([d.info for d in result.documents])
    view._on_finished(result)

    view._tree.topLevelItem(1).setSelected(True)
    assert view._current_group_id() == 2
    assert view._current_document_key() is None


# ---------------------------------------------------------------------- reset
def test_reset_restores_original_clustering(view):
    result = _result()
    view.set_documents([d.info for d in result.documents])
    view._on_finished(result)

    view._tree.topLevelItem(0).setSelected(True)
    view.verify_selected_group()
    view.reset_review()

    state = view.current_review()
    assert state.verified_count == 0
    assert state.total_groups == 2


# --------------------------------------------------------------------- detail
def test_selecting_member_shows_detail(view):
    result = _result()
    view.set_documents([d.info for d in result.documents])
    view._on_finished(result)

    view._tree.topLevelItem(0).child(0).setSelected(True)

    text = view._detail.text()
    assert "Kelompok: KELOMPOK_001" in text
    assert "Dokumen dalam kelompok: 2" in text
    assert "Pasangan terukur" in text
    assert "kecocokan" in text


def test_selecting_ungrouped_member_shows_status(view):
    docs = [_grouped_document("lonely.pdf")]
    result = GroupingResult(
        clustering=ClusteringResult(groups=(), ungrouped=(docs[0].key,), edges=()),
        documents=tuple(docs),
    )
    view.set_documents([d.info for d in docs])
    view._on_finished(result)

    view._tree.topLevelItem(0).child(0).setSelected(True)
    assert "belum terkelompok" in view._detail.text().lower()


def test_document_selected_signal_emitted(view):
    result = _result()
    view.set_documents([d.info for d in result.documents])
    view._on_finished(result)

    captured = []
    view.document_selected.connect(captured.append)
    # Selecting already triggers ``itemSelectionChanged``; do not call manually.
    view._tree.topLevelItem(0).child(0).setSelected(True)

    assert len(captured) == 1


def test_candidate_pairs_are_shown_but_not_grouped(view):
    """Pairs in the review band appear as candidates, not as a group."""
    from core.clustering_engine import CandidatePair

    docs = [_grouped_document("SU_070.pdf"), _grouped_document("SU_071.pdf")]
    keys = [d.key for d in docs]
    clustering = ClusteringResult(
        groups=(),
        ungrouped=tuple(keys),
        edges=(SimilarityEdge(a_key=keys[0], b_key=keys[1], score=75.0),),
        candidates=(CandidatePair(a_key=keys[0], b_key=keys[1], score=75.0),),
    )
    result = GroupingResult(clustering=clustering, documents=tuple(docs))

    view.set_documents([d.info for d in docs])
    view._on_finished(result)

    labels = [
        view._tree.topLevelItem(i).text(0)
        for i in range(view._tree.topLevelItemCount())
    ]
    assert any("Kandidat" in label for label in labels)
    assert view.current_review().total_groups == 0
    assert "kandidat" in view._status_label.text().lower()


# -------------------------------------------------------------------- errors
def test_failure_shows_message(view):
    view.set_documents([_info("a.pdf"), _info("b.pdf")])
    view._on_failed("contoh galat")
    assert "gagal" in view._status_label.text().lower()


def test_grouping_needs_two_documents(view):
    view.set_documents([_info("a.pdf")])
    view.start_grouping()
    assert view._worker is None
