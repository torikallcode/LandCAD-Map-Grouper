import numpy as np
import pytest

from core.clustering_engine import (
    DEFAULT_CLUSTER_CONFIG,
    UNGROUPED_LABEL,
    ClusterConfig,
    MapDocument,
    build_groups,
    build_similarity_matrix,
    cluster_documents,
    clustering_summary,
)


def _doc(key: str) -> MapDocument:
    """A synthetic document whose features are trivially non-empty."""

    class _Features:
        is_empty = False

    return MapDocument(
        key=key, filename=f"{key}.pdf", image=np.zeros((10, 10), np.uint8), features=_Features()
    )


def _matrix(scores: dict[tuple[int, int], float], size: int) -> np.ndarray:
    matrix = np.zeros((size, size))
    np.fill_diagonal(matrix, 100.0)
    for (i, j), value in scores.items():
        matrix[i, j] = value
        matrix[j, i] = value
    return matrix


# ------------------------------------------------------------------- grouping
def test_two_matching_documents_form_one_group():
    docs = [_doc("a"), _doc("b")]
    matrix = _matrix({(0, 1): 90.0}, 2)
    groups, ungrouped = build_groups(docs, matrix)

    assert len(groups) == 1
    assert groups[0].member_keys == ("a", "b")
    assert groups[0].confidence == "Tinggi"
    assert ungrouped == ()


def test_dissimilar_documents_stay_ungrouped():
    docs = [_doc("a"), _doc("b")]
    matrix = _matrix({(0, 1): 30.0}, 2)
    groups, ungrouped = build_groups(docs, matrix)

    assert groups == ()
    assert set(ungrouped) == {"a", "b"}


def test_exactly_threshold_links():
    docs = [_doc("a"), _doc("b")]
    matrix = _matrix({(0, 1): DEFAULT_CLUSTER_CONFIG.link_threshold}, 2)
    groups, _ = build_groups(docs, matrix)
    assert len(groups) == 1


def test_just_below_threshold_does_not_link():
    docs = [_doc("a"), _doc("b")]
    matrix = _matrix({(0, 1): DEFAULT_CLUSTER_CONFIG.link_threshold - 0.1}, 2)
    groups, ungrouped = build_groups(docs, matrix)
    assert groups == ()
    assert len(ungrouped) == 2


def test_transitive_grouping():
    """A~B and B~C should place all three together even if A~C is weak."""
    docs = [_doc("a"), _doc("b"), _doc("c")]
    matrix = _matrix({(0, 1): 90.0, (1, 2): 88.0, (0, 2): 40.0}, 3)
    groups, _ = build_groups(docs, matrix)

    assert len(groups) == 1
    assert set(groups[0].member_keys) == {"a", "b", "c"}
    # The weak a-c pair is a transitive artifact and must not define group strength.
    assert groups[0].min_pair_score == pytest.approx(88.0, abs=0.1)
    assert groups[0].confidence == "Tinggi"


def test_multiple_disjoint_groups():
    docs = [_doc(k) for k in ("a", "b", "c", "d", "e")]
    matrix = _matrix(
        {(0, 1): 95.0, (2, 3): 80.0, (0, 2): 10.0, (1, 4): 5.0, (3, 4): 12.0}, 5
    )
    groups, ungrouped = build_groups(docs, matrix)

    assert len(groups) == 2
    sizes = sorted(group.size for group in groups)
    assert sizes == [2, 2]
    assert ungrouped == ("e",)


def test_group_needing_review_when_min_score_below_high_band():
    docs = [_doc("a"), _doc("b"), _doc("c")]
    matrix = _matrix({(0, 1): 95.0, (1, 2): 70.0}, 3)
    groups, _ = build_groups(docs, matrix)

    assert groups[0].confidence == "Sedang"
    assert groups[0].needs_review


def test_group_names_are_numbered():
    docs = [_doc(k) for k in ("a", "b", "c", "d")]
    matrix = _matrix({(0, 1): 90.0, (2, 3): 90.0}, 4)
    groups, _ = build_groups(docs, matrix)

    names = sorted(group.name for group in groups)
    assert names == ["KELOMPOK_001", "KELOMPOK_002"]


def test_singleton_documents_are_ungrouped_not_forced():
    docs = [_doc("solo")]
    groups, ungrouped = build_groups(docs, _matrix({}, 1))
    assert groups == ()
    assert ungrouped == ("solo",)


# --------------------------------------------------------------------- matrix
def test_matrix_is_symmetric_with_unit_diagonal():
    docs = [_doc(k) for k in ("a", "b", "c")]
    matrix, edges, skipped = build_similarity_matrix(
        docs, scorer=lambda x, y: _result(70.0), config=ClusterConfig(prefilter_enabled=False)
    )

    assert matrix.shape == (3, 3)
    assert np.allclose(matrix, matrix.T)
    assert np.allclose(np.diag(matrix), 100.0)
    assert len(edges) == 3
    assert skipped == 0


def test_matrix_scores_each_pair_once():
    docs = [_doc(k) for k in ("a", "b", "c", "d")]
    matrix, edges, _ = build_similarity_matrix(
        docs, scorer=lambda x, y: _result(55.0), config=ClusterConfig(prefilter_enabled=False)
    )
    assert len(edges) == 6          # 4 choose 2
    assert matrix[0, 3] == pytest.approx(55.0)


def test_prefilter_marks_skipped_pairs():
    docs = [_doc("a"), _doc("b")]
    empty = MapDocument(
        key="b", filename="b.pdf", image=np.zeros((10, 10), np.uint8),
        features=type("F", (), {"is_empty": True})(),
    )
    matrix, edges, skipped = build_similarity_matrix(
        [docs[0], empty], scorer=lambda x, y: _result(90.0), config=ClusterConfig()
    )
    assert skipped == 1
    assert edges[0].skipped
    assert matrix[0, 1] == 0.0


def test_matrix_reports_progress():
    docs = [_doc(k) for k in ("a", "b", "c")]
    events = []
    build_similarity_matrix(
        docs,
        scorer=lambda x, y: _result(60.0),
        config=ClusterConfig(prefilter_enabled=False),
        progress=lambda c, t, label: events.append((c, t)),
    )
    assert events[-1] == (3, 3)


def test_matrix_supports_cancellation():
    docs = [_doc(k) for k in ("a", "b", "c", "d")]
    _matrix_result, edges, _ = build_similarity_matrix(
        docs,
        scorer=lambda x, y: _result(60.0),
        config=ClusterConfig(prefilter_enabled=False),
        should_cancel=lambda: True,
    )
    assert edges == ()


# ----------------------------------------------------------------- end-to-end
def test_cluster_documents_end_to_end():
    docs = [_doc(k) for k in ("a", "b", "c")]

    def scorer(left, right):
        if {left.key, right.key} == {"a", "b"}:
            return _result(92.0)
        return _result(10.0)

    result = cluster_documents(
        docs, scorer=scorer, config=ClusterConfig(prefilter_enabled=False)
    )

    assert result.total_groups == 1
    assert result.grouped_count == 2
    assert result.ungrouped == ("c",)
    assert result.group_of("a") is not None
    assert result.group_of("c") is None


def test_unusable_documents_are_ungrouped():
    good_a, good_b = _doc("a"), _doc("b")
    bad = MapDocument(
        key="bad", filename="bad.pdf", image=np.zeros((10, 10), np.uint8),
        features=type("F", (), {"is_empty": True})(),
    )
    result = cluster_documents(
        [good_a, good_b, bad],
        scorer=lambda x, y: _result(90.0),
        config=ClusterConfig(prefilter_enabled=False),
    )
    assert "bad" in result.ungrouped
    assert result.total_groups == 1


def test_empty_document_list():
    result = cluster_documents([], config=ClusterConfig(prefilter_enabled=False))
    assert result.groups == ()
    assert result.ungrouped == ()


# ------------------------------------------------------------------ reporting
def test_summary_lists_groups_and_ungrouped():
    docs = [_doc("a"), _doc("b"), _doc("c")]
    result = cluster_documents(
        docs,
        scorer=lambda x, y: _result(92.0) if {x.key, y.key} == {"a", "b"} else _result(5.0),
        config=ClusterConfig(prefilter_enabled=False),
    )
    summary = clustering_summary(result, {"a": "A.pdf", "b": "B.pdf", "c": "C.pdf"})

    assert "Jumlah kelompok: 1" in summary
    assert "KELOMPOK_001" in summary
    assert "A.pdf" in summary
    assert "Belum terkelompok" in summary


def test_version_and_label_constants():
    from core.clustering_engine import CLUSTERING_VERSION

    assert CLUSTERING_VERSION == "cluster-v1"
    assert UNGROUPED_LABEL == "BELUM_TERKELOMPOK"


def _result(score: float):
    """Minimal stand-in for SimilarityResult."""
    from core.similarity_engine import SimilarityScore

    return type(
        "R",
        (),
        {
            "score": SimilarityScore(score=score, aligned=True),
            "alignment": None,
            "warped": None,
        },
    )()
