"""Threshold semantics: PRD 9 bands are display categories, not a joining rule.

Regression context: using PRD's 65 (the floor of "needs review") as the join
threshold merged maps that only shared the page frame. Now:
  * >= 82  -> may join a group
  * 65-81  -> reported as a candidate for manual review, never merged
  * < 65   -> no relationship
"""

import numpy as np
import pytest

from core.clustering_engine import (
    DEFAULT_CLUSTER_CONFIG,
    CandidatePair,
    ClusterConfig,
    MapDocument,
    cluster_documents,
)


class _F:
    is_empty = False


def _doc(key: str) -> MapDocument:
    return MapDocument(
        key=key, filename=f"{key}.pdf", image=np.zeros((4, 4), np.uint8), features=_F()
    )


def _matrix(scores: dict[tuple[int, int], float], size: int) -> np.ndarray:
    matrix = np.zeros((size, size))
    np.fill_diagonal(matrix, 100.0)
    for (i, j), value in scores.items():
        matrix[i, j] = value
        matrix[j, i] = value
    return matrix


def _scorer(matrix: np.ndarray):
    from core.similarity_engine import SimilarityIndicators, SimilarityScore

    def score(left, right):
        value = float(matrix[int(left.key[1:]), int(right.key[1:])])
        return type(
            "R",
            (),
            {
                "score": SimilarityScore(
                    score=value, aligned=value > 0, indicators=SimilarityIndicators()
                ),
                "alignment": type("A", (), {"match_count": 10, "inlier_count": 5})(),
                "warped": None,
            },
        )()

    return score


# --------------------------------------------------------------- defaults
def test_default_join_threshold_is_82():
    assert DEFAULT_CLUSTER_CONFIG.link_threshold == 82.0


def test_candidate_floor_is_prd_review_band():
    assert DEFAULT_CLUSTER_CONFIG.candidate_threshold == 65.0


# ---------------------------------------------------- candidate, not merged
def test_pair_in_candidate_band_is_not_merged():
    docs = [_doc("d0"), _doc("d1")]
    matrix = _matrix({(0, 1): 75.0}, 2)

    result = cluster_documents(
        docs, scorer=_scorer(matrix), config=ClusterConfig(prefilter_enabled=False)
    )

    assert result.total_groups == 0, "skor 75 tidak boleh langsung menjadi kelompok"
    assert result.ungrouped_count == 2
    assert result.candidate_count == 1
    assert result.candidates[0].score == 75.0


def test_pair_at_join_threshold_is_merged():
    docs = [_doc("d0"), _doc("d1")]
    matrix = _matrix({(0, 1): 82.0}, 2)

    result = cluster_documents(
        docs, scorer=_scorer(matrix), config=ClusterConfig(prefilter_enabled=False)
    )

    assert result.total_groups == 1
    assert result.candidate_count == 0


def test_pair_below_candidate_floor_is_ignored():
    docs = [_doc("d0"), _doc("d1")]
    matrix = _matrix({(0, 1): 50.0}, 2)

    result = cluster_documents(
        docs, scorer=_scorer(matrix), config=ClusterConfig(prefilter_enabled=False)
    )

    assert result.total_groups == 0
    assert result.candidate_count == 0


def test_candidates_are_lookupable_per_document():
    docs = [_doc("d0"), _doc("d1"), _doc("d2")]
    matrix = _matrix({(0, 1): 70.0, (1, 2): 90.0}, 3)

    result = cluster_documents(
        docs, scorer=_scorer(matrix), config=ClusterConfig(prefilter_enabled=False)
    )

    assert result.candidate_count == 1
    assert len(result.candidates_of("d0")) == 1
    assert result.candidates_of("d2") == ()


def test_candidate_dataclass_label():
    pair = CandidatePair(a_key="a", b_key="b", score=70.0)
    assert "a" in pair.label and "b" in pair.label


# ---------------------------------------------------------------- validation
def test_candidate_threshold_must_be_lower_than_join():
    with pytest.raises(ValueError):
        ClusterConfig(link_threshold=70.0, candidate_threshold=70.0)


def test_candidate_threshold_may_equal_prd_floor():
    config = ClusterConfig(link_threshold=82.0, candidate_threshold=65.0)
    assert config.candidate_threshold < config.link_threshold


# ---------------------------------------------- the reported failure scenario
def test_frame_only_matches_no_longer_merge():
    """Maps that only share the page frame score 0 after the interior gate.

    Before the fix such pairs reached ~65 and merged; the threshold alone would
    not have been enough, which is why the interior gate exists too.
    """
    docs = [_doc("d0"), _doc("d1")]
    matrix = _matrix({(0, 1): 0.0}, 2)

    result = cluster_documents(
        docs, scorer=_scorer(matrix), config=ClusterConfig(prefilter_enabled=False)
    )
    assert result.total_groups == 0
    assert result.candidate_count == 0


def test_three_locations_with_borderline_pairs_stay_separate():
    """65-81 pairs between locations must not chain them together."""
    locations = [i // 4 for i in range(12)]
    size = len(locations)
    rng = np.random.default_rng(5)
    matrix = np.zeros((size, size))
    for i in range(size):
        for j in range(i + 1, size):
            if locations[i] == locations[j]:
                value = float(rng.uniform(83, 92))
            else:
                value = float(rng.uniform(66, 81))   # "needs review" neighbours
            matrix[i, j] = matrix[j, i] = value
    np.fill_diagonal(matrix, 100.0)

    docs = [_doc(f"d{i:02d}") for i in range(size)]
    result = cluster_documents(
        docs, scorer=_scorer(matrix), config=ClusterConfig(prefilter_enabled=False)
    )

    assert result.total_groups == 3
    for group in result.groups:
        assert len({locations[int(k[1:])] for k in group.member_keys}) == 1
