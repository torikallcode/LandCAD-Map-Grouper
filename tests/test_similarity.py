import cv2
import numpy as np
import pytest

from core.feature_extractor import extract_features
from core.similarity_engine import (
    BAND_REVIEW,
    DEFAULT_SIMILARITY_CONFIG,
    SimilarityConfig,
    SimilarityIndicators,
    SimilarityWeights,
    _score_from_indicators,
    prefilter_pair,
    score_pair,
    similarity_summary,
)


def map_page(width=1000, height=1400, seed=11):
    from tests.test_normalization import make_map_page

    return make_map_page(width=width, height=height, seed=seed)


def rotated(image, angle=0.0, scale=1.0):
    h, w = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, scale)
    return cv2.warpAffine(image, matrix, (w, h), borderValue=255)


def keep_region(image, fraction=0.3):
    h, w = image.shape[:2]
    out = np.full_like(image, 255)
    x0 = int(w * (1 - fraction) / 2)
    y0 = int(h * (1 - fraction) / 2)
    out[y0:y0 + int(h * fraction), x0:x0 + int(w * fraction)] = image[
        y0:y0 + int(h * fraction), x0:x0 + int(w * fraction)
    ]
    return out


# ------------------------------------------------------------------- scoring
def test_identical_images_score_high():
    page = map_page()
    features = extract_features(page)
    result = score_pair(page, page, features, features)

    assert result.score.aligned
    assert result.score.score >= 90
    assert result.score.band == "Kemiripan tinggi"


def test_rotated_copy_scores_in_review_band():
    page = map_page()
    copy = rotated(page, 5.0)
    result = score_pair(page, copy, extract_features(page), extract_features(copy))

    assert result.score.aligned
    assert result.score.score >= BAND_REVIEW


def test_unrelated_map_scores_zero():
    left = map_page(seed=11)
    right = map_page(seed=99)
    result = score_pair(left, right, extract_features(left), extract_features(right))
    assert result.score.score == 0.0


def test_partial_region_is_rejected_by_coverage_gate():
    """A crop aligns perfectly yet covers almost none of the page."""
    page = map_page()
    partial = keep_region(page, 0.3)
    result = score_pair(page, partial, extract_features(page), extract_features(partial))

    assert result.score.aligned
    assert result.score.score == 0.0
    assert "cakupan" in result.score.blocked_reason


def test_blank_image_cannot_be_compared():
    page = map_page()
    blank = np.full_like(page, 255)
    result = score_pair(page, blank, extract_features(page), extract_features(blank))

    assert not result.score.aligned
    assert result.score.score == 0.0
    assert result.score.blocked_reason


def test_symmetry_of_score():
    left = map_page(seed=11)
    right = map_page(seed=12)
    forward = score_pair(left, right, extract_features(left), extract_features(right))
    backward = score_pair(right, left, extract_features(right), extract_features(left))
    assert abs(forward.score.score - backward.score.score) < 15


# ------------------------------------------------------------------- bands
@pytest.mark.parametrize(
    "score,expected",
    [
        (95.0, "Kemiripan tinggi"),
        (85.0, "Kemiripan tinggi"),
        (84.9, "Perlu pemeriksaan"),
        (65.0, "Perlu pemeriksaan"),
        (64.9, "Kemiripan rendah"),
        (0.0, "Kemiripan rendah"),
    ],
)
def test_band_boundaries(score, expected):
    from core.similarity_engine import SimilarityScore

    assert SimilarityScore(score=score).band == expected


def test_review_flag_only_in_middle_band():
    from core.similarity_engine import SimilarityScore

    assert SimilarityScore(score=70.0, aligned=True).needs_review
    assert not SimilarityScore(score=95.0, aligned=True).needs_review
    assert not SimilarityScore(score=30.0, aligned=True).needs_review


def test_candidate_requires_review_threshold():
    from core.similarity_engine import SimilarityScore

    assert SimilarityScore(score=70.0, aligned=True).is_candidate
    assert not SimilarityScore(score=64.0, aligned=True).is_candidate
    assert not SimilarityScore(score=90.0, aligned=False).is_candidate


# ---------------------------------------------------------------- weights
def test_weights_normalise_to_one():
    normalised = DEFAULT_SIMILARITY_CONFIG.weights.normalised()
    assert pytest.approx(normalised.total(), abs=1e-6) == 1.0


def test_custom_weights_accepted():
    weights = SimilarityWeights(reference_coverage=0.9, alignment=0.1, inlier_support=0.0,
                                line_structure=0.0, inlier_spread=0.0)
    config = SimilarityConfig(weights=weights)
    indicators = SimilarityIndicators(reference_coverage=1.0, alignment_quality=0.0)
    assert _score_from_indicators(indicators, config) > 80


def test_coverage_dominates_when_it_is_zero():
    indicators = SimilarityIndicators(
        reference_coverage=0.0, alignment_quality=1.0, inlier_count=1000, inlier_spread=1.0
    )
    score = _score_from_indicators(indicators, DEFAULT_SIMILARITY_CONFIG)
    assert score < 60


# ------------------------------------------------------------- prefilter
def test_prefilter_accepts_related_maps():
    page = map_page()
    copy = rotated(page, 4.0)
    assert prefilter_pair(extract_features(page), extract_features(copy))


def test_prefilter_rejects_empty_content():
    """The prefilter only removes clearly hopeless pairs.

    Measured: unrelated map-like pages still yield 469-576 ratio-test matches,
    because both are sparse line art with similar corner statistics. So the
    prefilter rejects blank/low-content pages but cannot separate two different
    maps - the coverage gate does that. This test documents the real behaviour
    rather than an optimistic assumption.
    """
    page = map_page()
    blank = extract_features(np.full((300, 300), 255, np.uint8))
    assert not prefilter_pair(extract_features(page), blank)
    assert not prefilter_pair(blank, extract_features(page))


def test_prefilter_does_not_separate_unrelated_maps():
    """Documented limitation: the cheap gate lets unrelated maps through."""
    left = map_page(seed=11)
    right = map_page(seed=77)
    # Both are dense line art, so the gate passes them; scoring rejects them.
    assert prefilter_pair(extract_features(left), extract_features(right))
    scored = score_pair(left, right, extract_features(left), extract_features(right))
    assert scored.score.score == 0.0


def test_prefilter_rejects_empty_features():
    page = map_page()
    blank = extract_features(np.full((300, 300), 255, np.uint8))
    assert not prefilter_pair(extract_features(page), blank)
    assert not prefilter_pair(blank, extract_features(page))


# -------------------------------------------------------------- reporting
def test_summary_lists_indicators():
    page = map_page()
    result = score_pair(page, page, extract_features(page), extract_features(page))
    summary = similarity_summary(result)

    assert "Skor kemiripan:" in summary
    assert "Cakupan peta acuan:" in summary
    assert "Kualitas alignment:" in summary
    assert "Inlier:" in summary


def test_summary_for_uncomparable_pair():
    page = map_page()
    blank = np.full_like(page, 255)
    result = score_pair(page, blank, extract_features(page), extract_features(blank))
    assert "Tidak dapat dibandingkan" in similarity_summary(result)


def test_indicators_dict_is_json_friendly():
    payload = SimilarityIndicators(
        reference_coverage=0.5, inlier_count=100
    ).as_dict()
    assert all(isinstance(value, float) for value in payload.values())
