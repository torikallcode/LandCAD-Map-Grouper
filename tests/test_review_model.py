import pytest

from core.clustering_engine import ClusteringResult, Group, SimilarityEdge
from core.review_model import (
    REVIEW_VERSION,
    UNGROUPED_ID,
    ReviewedGroup,
    ReviewState,
)


def _state() -> ReviewState:
    clustering = ClusteringResult(
        groups=(
            Group(group_id=1, member_keys=("a", "b"), confidence="Sedang",
                  min_pair_score=70.0, needs_review=True),
            Group(group_id=2, member_keys=("c", "d"), confidence="Tinggi",
                  min_pair_score=90.0, needs_review=False),
        ),
        ungrouped=("e",),
        edges=(
            SimilarityEdge(a_key="a", b_key="b", score=70.0, match_count=300),
            SimilarityEdge(a_key="c", b_key="d", score=90.0, match_count=500),
            SimilarityEdge(a_key="a", b_key="c", score=20.0, match_count=10),
        ),
    )
    return ReviewState.from_clustering(
        clustering, {"a": "A.pdf", "b": "B.pdf", "c": "C.pdf", "d": "D.pdf", "e": "E.pdf"}
    )


# ------------------------------------------------------------------- factory
def test_from_clustering_builds_state():
    state = _state()
    assert state.total_groups == 2
    assert state.grouped_count == 4
    assert state.ungrouped == ("e",)
    assert state.needs_review_count == 2
    assert state.verified_count == 0


def test_group_lookup_and_names():
    state = _state()
    assert state.group_of("a").name == "KELOMPOK_001"
    assert state.group_of("e") is None
    assert state.name_of("a") == "A.pdf"
    assert state.name_of("zzz") == "zzz"


def test_score_and_match_count_lookup():
    state = _state()
    assert state.score_between("a", "b") == 70.0
    assert state.score_between("b", "a") == 70.0
    assert state.score_between("a", "e") == 0.0
    assert state.match_count_of("a") == 300


def test_member_scores_use_best_pair_in_group():
    state = _state()
    group = state.group_by_id(1)
    scores = state.member_scores(group)
    assert scores["a"] == 70.0
    assert scores["b"] == 70.0


# ------------------------------------------------------------------- moving
def test_move_document_between_groups():
    state = _state().move_document("a", 2)

    assert "a" in state.group_by_id(2).member_keys
    assert "a" not in state.group_by_id(1).member_keys
    # the modified groups must be re-verified
    assert not state.group_by_id(2).verified


def test_move_document_to_ungrouped():
    state = _state().move_document("a", None)
    assert "a" in state.ungrouped
    assert state.group_of("a") is None


def test_move_rejects_unknown_document():
    with pytest.raises(KeyError):
        _state().move_document("zzz", 1)


def test_move_rejects_unknown_group():
    with pytest.raises(KeyError):
        _state().move_document("a", 99)


def test_emptied_group_is_removed():
    state = _state()
    state = state.move_document("a", None)
    state = state.move_document("b", None)
    assert state.group_by_id(1) is None


def test_split_document_is_move_to_ungrouped():
    state = _state().split_document("b")
    assert "b" in state.ungrouped


def test_move_to_ungrouped_id_constant():
    state = _state().move_document("a", UNGROUPED_ID)
    assert "a" in state.ungrouped


# ------------------------------------------------------------------- merging
def test_merge_groups_combines_members():
    state = _state().merge_groups((2,), 1)

    merged = state.group_by_id(1)
    assert set(merged.member_keys) == {"a", "b", "c", "d"}
    assert state.group_by_id(2) is None
    assert state.total_groups == 1


def test_merge_marks_unverified():
    state = _state().set_verified(1, True).merge_groups((2,), 1)
    assert not state.group_by_id(1).verified


def test_merge_rejects_unknown_target():
    with pytest.raises(KeyError):
        _state().merge_groups((2,), 99)


def test_merge_rejects_unknown_source():
    with pytest.raises(KeyError):
        _state().merge_groups((99,), 1)


def test_merge_single_source_is_noop():
    state = _state().merge_groups((1,), 1)
    assert state.group_by_id(1).size == 2


# ------------------------------------------------------------------ creation
def test_create_group_from_mixed_documents():
    state = _state().create_group(("a", "e"))

    new_group = state.group_by_id(3)
    assert new_group is not None
    assert set(new_group.member_keys) == {"a", "e"}
    assert "e" not in state.ungrouped
    assert "a" not in state.group_by_id(1).member_keys


def test_create_group_rejects_empty_selection():
    with pytest.raises(ValueError):
        _state().create_group(())


def test_create_group_ids_increase():
    state = _state().create_group(("e",))
    assert max(group.group_id for group in state.groups) == 3


# --------------------------------------------------------------- verification
def test_set_verified_marks_group():
    state = _state().set_verified(1, True)
    assert state.group_by_id(1).verified
    assert state.group_by_id(1).status_label == "Terverifikasi"
    assert state.verified_count == 1


def test_verify_all():
    state = _state().verify_all()
    assert state.verified_count == 2
    assert state.needs_review_count == 0


def test_set_verified_rejects_unknown_group():
    with pytest.raises(KeyError):
        _state().set_verified(99, True)


def test_unverify_clears_status():
    state = _state().set_verified(1, True).set_verified(1, False)
    assert not state.group_by_id(1).verified
    assert state.group_by_id(1).status_label == "Perlu verifikasi"


def test_note_can_be_set():
    state = _state().set_note(1, "sudah dicek")
    assert state.group_by_id(1).note == "sudah dicek"


# ------------------------------------------------------------------ integrity
def test_every_document_appears_exactly_once():
    state = _state().move_document("a", None).merge_groups((2,), 1).create_group(("e",))
    keys = state.all_keys()
    assert len(keys) == len(set(keys))
    assert set(keys) == {"a", "b", "c", "d", "e"}


def test_operations_are_immutable():
    original = _state()
    moved = original.move_document("a", None)
    assert "a" in original.group_by_id(1).member_keys
    assert moved is not original


def test_confidence_recomputed_after_review():
    state = _state().move_document("e", 1)
    # 'e' has no measured edge, so the group can no longer claim high confidence
    assert state.group_by_id(1).min_pair_score == 70.0
    assert state.group_by_id(1).confidence == "Sedang"


def test_reset_restores_original_clustering():
    state = _state()
    clustering = ClusteringResult(
        groups=(Group(group_id=1, member_keys=("a", "b")),),
        ungrouped=("c",),
    )
    reset = state.reset(clustering)
    assert reset.total_groups == 1
    assert reset.ungrouped == ("c",)


def test_version_constant():
    assert REVIEW_VERSION == "review-v1"
    assert ReviewedGroup(group_id=1).is_empty
