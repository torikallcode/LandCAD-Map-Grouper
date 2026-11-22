import numpy as np
import pytest

from core.image_normalizer import (
    DEFAULT_CONFIG,
    NormalizationConfig,
    enhance_contrast,
    denoise,
    estimate_orientation,
    estimate_skew,
    normalize_page,
)
from utils.image_utils import binarize, rotate, to_grayscale


def make_text_page(width=1000, height=1400, rows=40, seed=7):
    """Synthetic upright page: horizontal lines of small glyph blocks."""
    rng = np.random.default_rng(seed)
    page = np.full((height, width), 255, np.uint8)
    for row in range(rows):
        y = 80 + row * max(int((height - 160) / rows), 4)
        x = 80
        while x < width - 120:
            word_width = int(rng.integers(18, 70))
            if rng.random() > 0.25:
                page[y:y + 7, x:x + word_width] = 0
            x += word_width + int(rng.integers(6, 14))
    return page


def make_map_page(width=1000, height=1400, seed=11):
    """Synthetic sparse map: a few long lines and polygon edges."""
    rng = np.random.default_rng(seed)
    page = np.full((height, width), 255, np.uint8)
    import cv2

    for _ in range(22):
        x1, y1 = rng.integers(120, width - 120, 2)
        length = int(rng.integers(120, 420))
        angle = float(rng.choice([0, 90, 35, -35, 145]))
        x2 = int(x1 + length * np.cos(np.radians(angle)))
        y2 = int(y1 + length * np.sin(np.radians(angle)))
        cv2.line(page, (int(x1), int(y1)), (x2, y2), 0, 2)
    return page


# ------------------------------------------------------------------ utilities
def test_to_grayscale_accepts_bgr_and_gray():
    bgr = np.zeros((10, 10, 3), np.uint8)
    bgr[:, :, 1] = 128
    assert to_grayscale(bgr).shape == (10, 10)
    gray = np.zeros((10, 10), np.uint8)
    assert to_grayscale(gray) is gray


def test_to_grayscale_rejects_unknown_shape():
    with pytest.raises(ValueError):
        to_grayscale(np.zeros((10, 10, 5), np.uint8))


def test_binarize_marks_ink_as_foreground():
    page = make_text_page()
    binary = binarize(page)
    assert binary.dtype == np.uint8
    assert np.count_nonzero(binary) > 0


def test_rotate_zero_is_identity():
    page = make_text_page()
    assert np.array_equal(rotate(page, 0.0), page)


# ------------------------------------------------------------- normalization
def test_normalize_produces_grayscale_of_target_size():
    page = make_text_page(1200, 1800)
    result = normalize_page(page)

    assert result.image.ndim == 2
    assert result.long_side == DEFAULT_CONFIG.target_long_side
    assert result.original.shape == page.shape
    assert "grayscale" in result.steps
    assert "resize" in result.steps


def test_normalize_keeps_original_separate_from_normalized():
    page = make_text_page()
    result = normalize_page(page)
    assert not np.array_equal(result.original, result.image)
    assert result.original.shape != result.image.shape


def test_normalize_does_not_mutate_input():
    page = make_text_page()
    snapshot = page.copy()
    normalize_page(page)
    assert np.array_equal(page, snapshot)


def test_enhance_contrast_and_denoise_preserve_shape():
    page = make_text_page()
    contrast = enhance_contrast(page)
    assert contrast.shape == page.shape
    assert denoise(contrast).shape == page.shape


def test_custom_config_target_size_respected():
    config = NormalizationConfig(target_long_side=800)
    result = normalize_page(make_text_page(1000, 1400), config)
    assert result.long_side == 800


# ---------------------------------------------------------------- orientation
def test_upright_text_page_has_no_quarter_turn():
    estimate = estimate_orientation(make_text_page())
    assert estimate.quarter_turns == 0
    assert estimate.reason == "teks horizontal"


@pytest.mark.parametrize("turns", [1, 3])
def test_sideways_page_is_detected(turns):
    sideways = np.ascontiguousarray(np.rot90(make_text_page(), turns))
    estimate = estimate_orientation(sideways)
    assert estimate.quarter_turns == 1
    assert estimate.is_reliable


def test_180_rotation_is_not_flagged_as_sideways():
    flipped = np.ascontiguousarray(np.rot90(make_text_page(), 2))
    estimate = estimate_orientation(flipped)
    assert estimate.quarter_turns == 0


def test_blank_page_orientation_is_not_guessed():
    blank = np.full((600, 400), 255, np.uint8)
    estimate = estimate_orientation(blank)
    assert estimate.quarter_turns == 0
    assert "sedikit" in estimate.reason


def test_normalize_applies_quarter_turn_to_sideways_page():
    sideways = np.ascontiguousarray(np.rot90(make_text_page(1000, 1400), 1))
    result = normalize_page(sideways)
    assert any(step.startswith("rotate90") for step in result.steps)
    # rotated source is landscape: after correction it should be portrait again
    height, width = result.image.shape
    assert height > width


# ---------------------------------------------------------------------- skew
def test_upright_page_skew_is_near_zero():
    estimate = estimate_skew(make_text_page())
    assert abs(estimate.angle) <= 1.0


@pytest.mark.parametrize("angle", [3.0, -5.0, 9.0])
def test_skew_estimate_tracks_injected_rotation(angle):
    skewed = rotate(make_text_page(), angle)
    estimate = estimate_skew(skewed)
    assert estimate.line_count >= DEFAULT_CONFIG.min_skew_lines
    assert estimate.angle == pytest.approx(-angle, abs=1.5)


def test_skew_confidence_reported():
    estimate = estimate_skew(rotate(make_text_page(), 4.0))
    assert 0.0 <= estimate.confidence <= 1.0
    assert estimate.is_reliable


def test_blank_image_skew_has_no_lines():
    estimate = estimate_skew(np.full((400, 300), 255, np.uint8))
    assert estimate.line_count == 0
    assert not estimate.is_reliable
    assert estimate.angle == 0.0


def test_deskew_step_applied_when_reliable():
    skewed = rotate(make_text_page(), 6.0)
    result = normalize_page(skewed)
    assert any(step.startswith("deskew") for step in result.steps)


def test_no_deskew_step_for_upright_page():
    """A page already square to the scanner must not be re-rotated."""
    result = normalize_page(make_text_page())
    assert not any(step.startswith("deskew") for step in result.steps)


def test_skew_not_applied_on_sparse_map_page():
    """A sparse map must not be 'corrected' by a bogus large angle."""
    result = normalize_page(make_map_page())
    deskew_steps = [step for step in result.steps if step.startswith("deskew")]
    if deskew_steps:
        angle = float(deskew_steps[0].split("(")[1].split("deg")[0])
        assert abs(angle) <= DEFAULT_CONFIG.max_skew_degrees
