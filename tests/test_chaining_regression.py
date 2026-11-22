"""Regression tests for the chaining bug found on a 118-document batch.

Symptom reported by the user: every one of 118 PDFs landed in a single group
even though several maps were clearly different. Cause: single linkage
(connected components) merges two clusters through *one* borderline pair, so a
chain of neighbouring maps collapses the whole batch into one KELOMPOK.
"""

import numpy as np
import pytest

from core.clustering_engine import (
    ClusterConfig,
    MapDocument,
    _average_linkage_clusters,
    build_groups,
    cluster_documents,
)


class _FakeFeatures:
    is_empty = False


def _doc(index: int) -> MapDocument:
    return MapDocument(
        key=f"d{index:04d}",
        filename=f"SU_{index:04d}.pdf",
        image=np.zeros((4, 4), np.uint8),
        features=_FakeFeatures(),
    )


def _matrix(location_of: list[int], seed: int, genuine=(83, 96), neighbour=(66, 80),
            bridge_chance: float = 0.10) -> np.ndarray:
    """Synthetic score matrix.

    Ranges mirror the measured behaviour after the interior-coverage fix:
    genuine pairs scored 79.9-85.2, frame-only matches scored 0, and neighbouring
    maps can reach the 65-81 "needs review" band.
    """
    rng = np.random.default_rng(seed)
    count = len(location_of)
    matrix = np.zeros((count, count))
    for i in range(count):
        for j in range(i + 1, count):
            if location_of[i] == location_of[j]:
                value = float(rng.uniform(*genuine))
            elif rng.random() < bridge_chance:
                value = float(rng.uniform(*neighbour))
            else:
                value = float(rng.uniform(0, 30))
            matrix[i, j] = matrix[j, i] = value
    np.fill_diagonal(matrix, 100.0)
    return matrix


def _score(index: int, location_of: list[int], matrix: np.ndarray):
    from core.similarity_engine import SimilarityIndicators, SimilarityScore

    value = float(matrix[index[0], index[1]])
    return type(
        "R",
        (),
        {
            "score": SimilarityScore(
                score=value, aligned=value > 0, indicators=SimilarityIndicators()
            ),
            "alignment": type("A", (), {"match_count": 300, "inlier_count": 200})(),
            "warped": None,
        },
    )()


# --------------------------------------------------------------- the bug
def test_118_documents_do_not_collapse_into_one_group():
    """The reported failure: 118 files -> 1 group instead of 3."""
    location_of = [i // 40 for i in range(118)]
    docs = [_doc(i) for i in range(118)]
    matrix = _matrix(location_of, seed=7)

    groups, ungrouped = build_groups(docs, matrix)

    assert len(groups) == 3, f"harus 3 kelompok, dapat {len(groups)}"
    assert sorted(g.size for g in groups) == [38, 40, 40]
    assert ungrouped == ()


def test_no_group_mixes_different_locations():
    location_of = [i // 40 for i in range(118)]
    docs = [_doc(i) for i in range(118)]
    matrix = _matrix(location_of, seed=7)
    groups, _ = build_groups(docs, matrix)

    for group in groups:
        locations = {location_of[int(key[1:])] for key in group.member_keys}
        assert len(locations) == 1, f"{group.name} mencampur lokasi {locations}"


def test_single_linkage_reproduces_the_old_bug():
    """Guards the reason for the change; the old mode is still available.

    The historical bug needed a low join threshold *and* single linkage. With the
    corrected 82 threshold single linkage no longer chains these scores, so the
    old configuration (65 + single) is reproduced explicitly.
    """
    location_of = [i // 40 for i in range(118)]
    docs = [_doc(i) for i in range(118)]
    matrix = _matrix(location_of, seed=7)

    old = ClusterConfig(linkage="single", link_threshold=65.0, candidate_threshold=40.0)
    groups, _ = build_groups(docs, matrix, old)
    assert len(groups) == 1
    assert groups[0].size == 118

    # Average linkage at the same low threshold resists the chain.
    groups_now, _ = build_groups(
        docs, matrix, ClusterConfig(linkage="average", link_threshold=65.0,
                                    candidate_threshold=40.0)
    )
    assert len(groups_now) == 3


def test_average_linkage_resists_a_single_bridge():
    """One pair above threshold must not merge two otherwise separate clusters."""
    # two clusters of 4; exactly one cross pair passes the threshold
    matrix = np.full((8, 8), 10.0)
    np.fill_diagonal(matrix, 100.0)
    for i in range(4):
        for j in range(i + 1, 4):
            matrix[i, j] = matrix[j, i] = 90.0
    for i in range(4, 8):
        for j in range(i + 1, 8):
            matrix[i, j] = matrix[j, i] = 90.0
    matrix[0, 4] = matrix[4, 0] = 80.0      # the single bridge

    docs = [_doc(i) for i in range(8)]
    groups, _ = build_groups(docs, matrix)
    assert len(groups) == 2, "satu jembatan tidak boleh menyatukan dua kelompok"


# ---------------------------------------------------- genuine groups survive
def test_small_two_document_group_is_kept():
    """A real pair scored 79.9-85.2; it must still form a group."""
    matrix = np.full((10, 10), 5.0)
    np.fill_diagonal(matrix, 100.0)
    matrix[0, 1] = matrix[1, 0] = 84.4

    docs = [_doc(i) for i in range(10)]
    groups, ungrouped = build_groups(docs, matrix)

    assert len(groups) == 1
    assert groups[0].size == 2
    assert groups[0].min_pair_score == pytest.approx(84.4)
    assert len(ungrouped) == 8


def test_weak_scan_group_survives_lower_scores():
    location_of = [i // 40 for i in range(118)]
    docs = [_doc(i) for i in range(118)]
    matrix = _matrix(
        location_of, seed=21, genuine=(83, 92), neighbour=(66, 81),
        bridge_chance=0.12,
    )

    groups, _ = build_groups(docs, matrix)
    assert len(groups) == 3
    for group in groups:
        assert len({location_of[int(k[1:])] for k in group.member_keys}) == 1


def test_many_locations_stay_separate():
    location_of = [i // 20 for i in range(118)]
    docs = [_doc(i) for i in range(118)]
    matrix = _matrix(
        location_of, seed=55, genuine=(83, 95), neighbour=(66, 81),
        bridge_chance=0.08,
    )

    groups, _ = build_groups(docs, matrix)
    assert len(groups) == 6
    assert max(g.size for g in groups) <= 20


# ------------------------------------------------------------ lower level
def test_average_linkage_on_empty_matrix():
    assert _average_linkage_clusters(np.zeros((0, 0)), 65.0) == []


def test_average_linkage_keeps_dissimilar_singletons():
    matrix = np.full((4, 4), 10.0)
    np.fill_diagonal(matrix, 100.0)
    clusters = _average_linkage_clusters(matrix, 65.0)
    assert all(len(c) == 1 for c in clusters)


def test_average_linkage_merges_identical_cluster():
    matrix = np.full((5, 5), 90.0)
    np.fill_diagonal(matrix, 100.0)
    clusters = _average_linkage_clusters(matrix, 65.0)
    assert len(clusters) == 1
    assert len(clusters[0]) == 5


def test_linkage_validation_rejects_unknown_value():
    with pytest.raises(ValueError):
        ClusterConfig(linkage="ward")


def test_default_config_uses_average_linkage():
    config = ClusterConfig()
    assert config.linkage == "average"
    # 82 to join, 65 as the floor of the "needs review" band.
    assert config.link_threshold == 82.0
    assert config.candidate_threshold == 65.0


def test_cluster_documents_end_to_end_uses_average():
    location_of = [i // 40 for i in range(118)]
    docs = [_doc(i) for i in range(118)]
    matrix = _matrix(location_of, seed=7)

    def scorer(left, right):
        return _score((int(left.key[1:]), int(right.key[1:])), location_of, matrix)

    result = cluster_documents(docs, scorer=scorer, config=ClusterConfig(
        prefilter_enabled=False
    ))
    assert result.total_groups == 3
    assert result.ungrouped_count == 0
