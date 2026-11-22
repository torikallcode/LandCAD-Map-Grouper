from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from utils.image_utils import (
    binarize,
    hough_segments,
    projection_variance_ratio,
    resize_long_side,
    rotate,
    segment_angle_degrees,
    text_mask,
    to_grayscale,
)

PREPROCESSING_VERSION = "normalize-v2"


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class NormalizationConfig:
    """Tunable knobs for the normalization pipeline."""

    target_long_side: int = 1600
    clahe_clip_limit: float = 2.0
    clahe_tile_grid: int = 8
    denoise_kernel: int = 3
    max_skew_degrees: float = 15.0
    min_skew_lines: int = 6
    max_skew_mad: float = 2.5
    min_skew_correction: float = 0.10
    background_value: int = 255
    orientation_min_ink: int = 120
    orientation_low_ratio: float = 0.60
    orientation_high_ratio: float = 1.60
    orientation_analysis_side: int = 1400


DEFAULT_CONFIG = NormalizationConfig()


@dataclass(frozen=True)
class SkewEstimate:
    angle: float = 0.0
    confidence: float = 0.0
    line_count: int = 0
    mad: float = 0.0

    @property
    def is_reliable(self) -> bool:
        return self.line_count > 0 and self.confidence >= 0.5


@dataclass(frozen=True)
class OrientationEstimate:
    quarter_turns: int = 0
    confidence: float = 0.0
    ratio: float = 1.0
    reason: str = "tidak dianalisis"

    @property
    def is_reliable(self) -> bool:
        return self.quarter_turns != 0 and self.confidence >= 0.5


@dataclass(frozen=True)
class NormalizedImage:
    image: np.ndarray
    original: np.ndarray
    scale: float
    skew: SkewEstimate
    orientation: OrientationEstimate
    steps: tuple[str, ...]

    @property
    def long_side(self) -> int:
        return max(self.image.shape[:2])


def estimate_orientation(
    gray: np.ndarray, config: NormalizationConfig = DEFAULT_CONFIG
) -> OrientationEstimate:
    """Detect sideways pages (90/180/270) from glyph layout direction.

    Whole-page projections are useless for this: on the reference map pages the
    page frame dominates the profile and always looks "horizontal". Restricting
    the projection to the glyph mask removes frames and long map lines, which
    made the row/column variance ratio a stable signal (0.32-0.46 sideways versus
    2.19-3.47 upright across blur, low resolution and contrast variants).
    """
    working, _ = resize_long_side(gray, config.orientation_analysis_side)
    mask = text_mask(binarize(working))
    ink = int(np.count_nonzero(mask))

    if ink < config.orientation_min_ink:
        return OrientationEstimate(ratio=0.0, reason="teks terlalu sedikit")

    ratio = projection_variance_ratio(mask)
    if ratio <= config.orientation_low_ratio:
        confidence = round(_clamp((config.orientation_low_ratio - ratio) / 0.30), 3)
        return OrientationEstimate(
            quarter_turns=1,
            confidence=max(confidence, 0.5),
            ratio=ratio,
            reason="teks vertikal",
        )
    if ratio >= config.orientation_high_ratio:
        confidence = round(
            _clamp((ratio - config.orientation_high_ratio) / 1.20), 3
        )
        return OrientationEstimate(
            quarter_turns=0,
            confidence=max(confidence, 0.5),
            ratio=ratio,
            reason="teks horizontal",
        )
    return OrientationEstimate(ratio=ratio, reason="orientasi ambigu")


def estimate_skew(
    gray: np.ndarray, config: NormalizationConfig = DEFAULT_CONFIG
) -> SkewEstimate:
    """Estimate page skew from the dominant straight segments.

    Projection-variance deskewing was rejected for these documents: on a sparse
    map page it reported a 16 degree tilt on an already upright scan. The median
    angle of Hough segments tracked the true skew within ~0.5 degree instead.
    """
    binary = binarize(gray)
    segments = hough_segments(binary)
    if segments.shape[0] == 0:
        return SkewEstimate()

    offsets: list[float] = []
    for segment in segments:
        angle = segment_angle_degrees(segment)
        for reference in (0.0, 90.0, -90.0):
            delta = angle - reference
            if abs(delta) <= config.max_skew_degrees:
                offsets.append(delta)
                break

    if len(offsets) < config.min_skew_lines:
        return SkewEstimate(line_count=len(offsets))

    data = np.asarray(offsets, dtype=np.float64)
    median = float(np.median(data))
    mad = float(np.median(np.abs(data - median)))

    agreement = _clamp(1.0 - mad / config.max_skew_mad)
    support = _clamp(len(offsets) / 40.0)
    confidence = round(0.65 * agreement + 0.35 * support, 3)

    return SkewEstimate(
        angle=median, confidence=confidence, line_count=len(offsets), mad=mad
    )


def enhance_contrast(
    gray: np.ndarray, config: NormalizationConfig = DEFAULT_CONFIG
) -> np.ndarray:
    clahe = cv2.createCLAHE(
        clipLimit=config.clahe_clip_limit,
        tileGridSize=(config.clahe_tile_grid, config.clahe_tile_grid),
    )
    return clahe.apply(gray)


def denoise(gray: np.ndarray, config: NormalizationConfig = DEFAULT_CONFIG) -> np.ndarray:
    kernel = max(1, config.denoise_kernel | 1)
    return cv2.medianBlur(gray, kernel)


def normalize_page(
    image: np.ndarray, config: NormalizationConfig = DEFAULT_CONFIG
) -> NormalizedImage:
    """Grayscale -> contrast -> denoise -> quarter-turn fix -> deskew -> resize.

    Orientation is corrected before deskewing so the Hough angles are measured
    in the final frame. The untouched grayscale render is returned alongside the
    normalized image because the PRD requires both to be stored separately.
    """
    original = to_grayscale(image)

    gray = enhance_contrast(original, config)
    gray = denoise(gray, config)

    steps: list[str] = ["grayscale", "clahe", "median_denoise"]

    orientation = estimate_orientation(gray, config)
    if orientation.quarter_turns:
        gray = np.ascontiguousarray(np.rot90(gray, orientation.quarter_turns))
        steps.append(f"rotate90x{orientation.quarter_turns}")

    skew = estimate_skew(gray, config)
    if skew.is_reliable and abs(skew.angle) >= config.min_skew_correction:
        gray = rotate(gray, skew.angle, border_value=config.background_value)
        steps.append(f"deskew({skew.angle:+.2f}deg)")

    scaled, scale = resize_long_side(gray, config.target_long_side)
    steps.append("resize")

    return NormalizedImage(
        image=scaled,
        original=original,
        scale=scale,
        skew=skew,
        orientation=orientation,
        steps=tuple(steps),
    )
