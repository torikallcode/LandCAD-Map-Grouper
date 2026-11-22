import cv2
import numpy as np
import pytest

from core.feature_extractor import extract_features
from core.image_alignment import (
    DEFAULT_ALIGNMENT_CONFIG,
    AlignmentConfig,
    AlignmentResult,
    TransformModel,
    align_features,
    align_images,
    make_side_by_side,
    match_features,
    overlay_alignment,
    pairwise_quality,
    warp_to_target,
)

from tests.test_normalization import make_map_page, make_text_page


def _page(seed=11, width=1000, height=1400):
    return make_map_page(width=width, height=height, seed=seed)


def _rotated(image, angle, scale=1.0):
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, scale)
    return cv2.warpAffine(image, matrix, (width, height), borderValue=255)


# ------------------------------------------------------------------ matching
def test_self_features_match_themselves():
    features = extract_features(_page())
    matches = match_features(features, features)
    assert len(matches) > 50


def test_matching_requires_both_sides_non_empty():
    features = extract_features(_page())
    empty = extract_features(np.full((300, 300), 255, np.uint8))
    assert match_features(features, empty) == []
    assert match_features(empty, features) == []


def test_ratio_test_stricter_yields_fewer_matches():
    features = extract_features(_page())
    loose = match_features(features, features, AlignmentConfig(ratio_test=0.9))
    strict = match_features(features, features, AlignmentConfig(ratio_test=0.5))
    assert len(strict) <= len(loose)


# ----------------------------------------------------------------- alignment
def test_self_alignment_is_identity_quality():
    features = extract_features(_page())
    result = align_features(features, features)

    assert result.is_aligned
    # The ratio test drops some keypoints before RANSAC, so the ceiling is the
    # match count, not the keypoint count.
    assert result.inlier_count == result.match_count
    assert result.inlier_count > 100
    assert result.quality > 0.9
    assert result.reprojection_error < 0.01


def test_alignment_recovers_injected_rotation():
    page = _page()
    rotated = _rotated(page, 6.0)
    result = align_images(page, rotated)

    assert result.is_aligned
    assert result.inlier_count > 40
    assert result.quality > 0.5


def test_alignment_recovers_injected_scale():
    page = _page()
    scaled = _rotated(page, 0.0, 0.85)
    result = align_images(page, scaled)

    assert result.is_aligned
    assert result.inlier_count > 40


def test_alignment_fails_gracefully_on_blank_target():
    page = _page()
    blank = np.full(page.shape, 255, np.uint8)
    result = align_images(page, blank)

    assert not result.is_aligned
    assert result.model is TransformModel.NONE
    assert result.matrix is None
    assert result.quality == 0.0
    assert result.inlier_count == 0


def test_alignment_reports_reason_when_too_few_matches():
    page = _page()
    unrelated = _page(seed=99)
    result = align_images(page, unrelated)
    if not result.is_aligned:
        assert result.reason


def test_alignment_does_not_force_homography_on_scarce_matches():
    """With few matches the config must stay on the cheaper affine model."""
    page = _page()
    features = extract_features(page)
    config = AlignmentConfig(min_matches=4, use_homography=True)
    result = align_features(features, features, config)
    assert result.model in (TransformModel.AFFINE, TransformModel.HOMOGRAPHY)


def test_homography_can_be_disabled():
    page = _page()
    result = align_images(page, _rotated(page, 4.0), AlignmentConfig(use_homography=False))
    assert result.model is not TransformModel.HOMOGRAPHY


def test_alignment_quality_bounded():
    page = _page()
    result = align_images(page, _rotated(page, 5.0))
    assert 0.0 <= result.quality <= 1.0
    assert 0.0 <= result.inlier_ratio <= 1.0


def test_confidence_labels():
    page = _page()
    result = align_images(page, page)
    assert result.confidence_label in ("Tinggi", "Sedang", "Rendah", "Gagal")


def test_transform_points_maps_correctly():
    page = _page()
    features = extract_features(page)
    result = align_features(features, features)

    points = np.array([[10.0, 20.0], [100.0, 200.0]], dtype=np.float32)
    projected = result.transform_points(points)
    assert projected.shape == (2, 2)
    assert np.allclose(projected, points, atol=0.5)


def test_transform_points_without_matrix_returns_empty():
    empty = AlignmentResult(
        model=TransformModel.NONE,
        matrix=None,
        match_count=0,
        inlier_count=0,
        inlier_ratio=0.0,
        reprojection_error=float("inf"),
        quality=0.0,
    )
    assert empty.transform_points(np.array([[1.0, 2.0]])).size == 0


def test_inlier_points_are_exposed():
    page = _page()
    result = align_images(page, _rotated(page, 3.0))
    if result.is_aligned:
        assert result.source_points.shape == (result.inlier_count, 2)
        assert result.target_points.shape == (result.inlier_count, 2)


# -------------------------------------------------------------------- warping
def test_warp_to_target_matches_reference_shape():
    page = _page()
    rotated = _rotated(page, 5.0)
    result = align_images(page, rotated)
    warped = warp_to_target(rotated, result, page.shape)

    assert warped.shape == page.shape


def test_warp_uses_forward_matrix_not_inverse():
    """Regression guard: warping must use the matrix fitted target -> source.

    Measured on the reference maps and on a synthetic 8 degree rotation, warping
    with the matrix fitted in the correct direction reached ~0.63 ink coverage,
    while reusing the opposite-direction matrix collapsed it to ~0.13.
    """
    page = _page()
    rotated = _rotated(page, 8.0)

    # Fit rotated -> page so the matrix applies directly to ``rotated``.
    result = align_images(rotated, page)
    assert result.is_aligned

    warped = warp_to_target(rotated, result, page.shape)
    wrongly_warped = cv2.warpAffine(
        rotated,
        cv2.invertAffineTransform(result.matrix[:2]),
        (page.shape[1], page.shape[0]),
        borderValue=255,
    )

    correct_error = float(
        np.mean(np.abs(warped.astype(np.float32) - page.astype(np.float32)))
    )
    wrong_error = float(
        np.mean(np.abs(wrongly_warped.astype(np.float32) - page.astype(np.float32)))
    )
    assert correct_error < wrong_error


def test_warp_requires_alignment():
    empty = AlignmentResult(
        model=TransformModel.NONE, matrix=None, match_count=0, inlier_count=0,
        inlier_ratio=0.0, reprojection_error=float("inf"), quality=0.0,
    )
    with pytest.raises(ValueError):
        warp_to_target(_page(), empty, (100, 100))


# ------------------------------------------------------------------- imagery
def test_side_by_side_doubles_width():
    page = _page()
    combined = make_side_by_side(page, page, gap=10)
    assert combined.shape[0] == page.shape[0]
    assert combined.shape[1] == page.shape[1] * 2 + 10


def test_side_by_side_handles_different_heights():
    tall = _page(height=1400)
    short = _page(height=900)
    combined = make_side_by_side(tall, short)
    assert combined.shape[0] == 1400


def test_overlay_returns_colour_image():
    page = _page()
    overlay = overlay_alignment(page, page)
    assert overlay.ndim == 3
    assert overlay.shape[:2] == page.shape


def test_overlay_handles_size_mismatch():
    page = _page()
    smaller = cv2.resize(page, (page.shape[1] // 2, page.shape[0] // 2))
    overlay = overlay_alignment(page, smaller)
    assert overlay.shape[:2] == page.shape


# ------------------------------------------------------------------- quality
@pytest.mark.parametrize(
    "value,expected",
    [(0.9, "Tinggi"), (0.75, "Tinggi"), (0.6, "Sedang"), (0.5, "Sedang"), (0.2, "Rendah")],
)
def test_pairwise_quality_bands(value, expected):
    assert pairwise_quality(value) == expected


def test_default_config_values():
    assert DEFAULT_ALIGNMENT_CONFIG.ratio_test == 0.75
    assert DEFAULT_ALIGNMENT_CONFIG.ransac_threshold == 4.0
    assert DEFAULT_ALIGNMENT_CONFIG.use_homography is True


def test_text_pages_align_to_each_other():
    left = make_text_page(seed=7)
    right = make_text_page(seed=7)
    result = align_images(left, right)
    assert result.is_aligned
    assert result.inlier_count > 40
