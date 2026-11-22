from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import cv2
import numpy as np

from core.feature_extractor import (
    DEFAULT_FEATURE_CONFIG,
    FeatureConfig,
    OrbFeatures,
)
from utils.image_utils import to_grayscale
from utils.logging_utils import get_logger

logger = get_logger("image_alignment")

ALIGNMENT_VERSION = "align-v1"


class TransformModel(str, Enum):
    NONE = "none"
    AFFINE = "affine"
    HOMOGRAPHY = "homography"

    @property
    def label(self) -> str:
        return {
            TransformModel.NONE: "Tidak sejajar",
            TransformModel.AFFINE: "Affine",
            TransformModel.HOMOGRAPHY: "Homografi",
        }[self]


@dataclass(frozen=True)
class AlignmentConfig:
    ratio_test: float = 0.75
    ransac_threshold: float = 4.0
    ransac_iterations: int = 3000
    ransac_confidence: float = 0.995
    min_matches: int = 8
    use_homography: bool = True
    # Homography is only kept when it stays well conditioned; otherwise the
    # affine model is preferred (PRD: do not force a homography).
    max_perspective_offset: float = 0.05
    max_inlier_error: float = 8.0


DEFAULT_ALIGNMENT_CONFIG = AlignmentConfig()


@dataclass(frozen=True)
class AlignmentResult:
    model: TransformModel
    matrix: np.ndarray | None
    match_count: int
    inlier_count: int
    inlier_ratio: float
    reprojection_error: float
    quality: float
    inlier_mask: np.ndarray | None = None
    source_points: np.ndarray | None = None
    target_points: np.ndarray | None = None
    homography_rejected: bool = False
    reason: str = ""

    @property
    def is_aligned(self) -> bool:
        return self.model is not TransformModel.NONE and self.matrix is not None

    @property
    def confidence_label(self) -> str:
        if not self.is_aligned:
            return "Gagal"
        if self.quality >= 0.75:
            return "Tinggi"
        if self.quality >= 0.5:
            return "Sedang"
        return "Rendah"

    def transform_points(self, points: np.ndarray) -> np.ndarray:
        if self.matrix is None or len(points) == 0:
            return np.empty((0, 2), dtype=np.float32)
        pts = np.asarray(points, dtype=np.float64).reshape(-1, 1, 2)
        if self.model is TransformModel.HOMOGRAPHY:
            projected = cv2.perspectiveTransform(pts, self.matrix)
        else:
            projected = cv2.transform(pts, self.matrix)
        return projected.reshape(-1, 2)


def match_features(
    source: OrbFeatures,
    target: OrbFeatures,
    config: AlignmentConfig = DEFAULT_ALIGNMENT_CONFIG,
) -> list[cv2.DMatch]:
    """Mutual knn matching with Lowe's ratio test."""
    if source.is_empty or target.is_empty:
        return []

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    knn = matcher.knnMatch(source.descriptors, target.descriptors, k=2)
    matches = []
    for pair in knn:
        if len(pair) != 2:
            continue
        first, second = pair
        if first.distance < config.ratio_test * second.distance:
            matches.append(first)
    return matches


def _correspondences(
    source: OrbFeatures,
    target: OrbFeatures,
    matches: list[cv2.DMatch],
) -> tuple[np.ndarray, np.ndarray]:
    src = np.float32([source.keypoints[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
    dst = np.float32([target.keypoints[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
    return src, dst


def _median_error(points: np.ndarray, projected: np.ndarray) -> float:
    if len(points) == 0:
        return float("inf")
    deltas = np.linalg.norm(projected - points, axis=1)
    return float(np.median(deltas))


def _is_well_conditioned(
    matrix: np.ndarray,
    source_shape: tuple[int, int],
    config: AlignmentConfig,
) -> bool:
    """Reject homographies whose perspective terms would distort the page.

    A homography fitted from too few (or nearly collinear) points can still pass
    RANSAC while placing corners far outside the page. We reject when the
    perspective terms move a page corner by more than ``max_perspective_offset``
    of the page dimension.
    """
    if matrix is None or not np.all(np.isfinite(matrix)):
        return False
    norm = matrix / matrix[2, 2] if abs(matrix[2, 2]) > 1e-12 else matrix
    height, width = source_shape[:2]
    if height == 0 or width == 0:
        return False
    offset_x = abs(norm[2, 0]) * width
    offset_y = abs(norm[2, 1]) * height
    limit_x = config.max_perspective_offset * width
    limit_y = config.max_perspective_offset * height
    return offset_x <= limit_x and offset_y <= limit_y


def _quality(
    inlier_count: int,
    inlier_ratio: float,
    reprojection_error: float,
    expected_support: int = 150,
    error_limit: float = 8.0,
) -> float:
    support = min(inlier_count / max(expected_support, 1), 1.0)
    consistency = min(max(inlier_ratio, 0.0), 1.0)
    precision = max(0.0, 1.0 - reprojection_error / error_limit)
    return round(0.45 * support + 0.30 * consistency + 0.25 * precision, 4)


def pairwise_quality(similarity: float) -> str:
    if similarity >= 0.75:
        return "Tinggi"
    if similarity >= 0.5:
        return "Sedang"
    return "Rendah"


def align_features(
    source: OrbFeatures,
    target: OrbFeatures,
    config: AlignmentConfig = DEFAULT_ALIGNMENT_CONFIG,
) -> AlignmentResult:
    """Estimate the transform taking ``source`` onto ``target``."""
    matches = match_features(source, target, config)
    if len(matches) < config.min_matches:
        return AlignmentResult(
            model=TransformModel.NONE,
            matrix=None,
            match_count=len(matches),
            inlier_count=0,
            inlier_ratio=0.0,
            reprojection_error=float("inf"),
            quality=0.0,
            reason=f"hanya {len(matches)} kecocokan (minimum {config.min_matches})",
        )

    src, dst = _correspondences(source, target, matches)

    affine, affine_mask = cv2.estimateAffinePartial2D(
        src,
        dst,
        method=cv2.RANSAC,
        ransacReprojThreshold=config.ransac_threshold,
        maxIters=config.ransac_iterations,
        confidence=config.ransac_confidence,
    )

    best = _evaluate(affine, affine_mask, src, dst, TransformModel.AFFINE, len(matches), config)

    homography_rejected = False
    reason = ""
    if config.use_homography and len(matches) >= max(config.min_matches * 2, 16):
        homography, homography_mask = cv2.findHomography(
            src,
            dst,
            cv2.RANSAC,
            config.ransac_threshold,
            maxIters=config.ransac_iterations,
            confidence=config.ransac_confidence,
        )
        if homography is not None and homography_mask is not None:
            if _is_well_conditioned(homography, source.image_shape, config):
                candidate = _evaluate(
                    homography,
                    homography_mask,
                    src,
                    dst,
                    TransformModel.HOMOGRAPHY,
                    len(matches),
                    config,
                )
                if candidate.inlier_count > best.inlier_count:
                    best = candidate
            else:
                homography_rejected = True
                reason = "homografi tidak stabil, memakai affine"

    return AlignmentResult(
        model=best.model,
        matrix=best.matrix,
        match_count=best.match_count,
        inlier_count=best.inlier_count,
        inlier_ratio=best.inlier_ratio,
        reprojection_error=best.reprojection_error,
        quality=best.quality,
        inlier_mask=best.inlier_mask,
        source_points=best.source_points,
        target_points=best.target_points,
        homography_rejected=homography_rejected,
        reason=reason or best.reason,
    )


def _evaluate(
    matrix: np.ndarray | None,
    mask: np.ndarray | None,
    src: np.ndarray,
    dst: np.ndarray,
    model: TransformModel,
    match_count: int,
    config: AlignmentConfig,
) -> AlignmentResult:
    if matrix is None or mask is None:
        return AlignmentResult(
            model=TransformModel.NONE,
            matrix=None,
            match_count=match_count,
            inlier_count=0,
            inlier_ratio=0.0,
            reprojection_error=float("inf"),
            quality=0.0,
            reason="RANSAC tidak menemukan model",
        )

    inlier_mask = mask.ravel().astype(bool)
    inlier_count = int(inlier_mask.sum())
    if inlier_count < config.min_matches or inlier_count == 0:
        return AlignmentResult(
            model=TransformModel.NONE,
            matrix=None,
            match_count=match_count,
            inlier_count=inlier_count,
            inlier_ratio=inlier_count / max(match_count, 1),
            reprojection_error=float("inf"),
            quality=0.0,
            reason=f"inlier {inlier_count} di bawah minimum",
        )

    if model is TransformModel.HOMOGRAPHY:
        projected = cv2.perspectiveTransform(
            src[inlier_mask].reshape(-1, 1, 2), matrix
        ).reshape(-1, 2)
    else:
        projected = cv2.transform(src[inlier_mask].reshape(-1, 1, 2), matrix).reshape(-1, 2)

    error = _median_error(dst[inlier_mask].reshape(-1, 2), projected)
    inlier_ratio = inlier_count / max(match_count, 1)
    quality = _quality(inlier_count, inlier_ratio, error)

    reason = ""
    if error > config.max_inlier_error:
        reason = f"galat proyeksi tinggi ({error:.1f}px)"
        quality = round(quality * 0.5, 4)

    return AlignmentResult(
        model=model,
        matrix=matrix,
        match_count=match_count,
        inlier_count=inlier_count,
        inlier_ratio=inlier_ratio,
        reprojection_error=error,
        quality=quality,
        inlier_mask=inlier_mask,
        source_points=src[inlier_mask].reshape(-1, 2),
        target_points=dst[inlier_mask].reshape(-1, 2),
        reason=reason,
    )


def align_images(
    source_image: np.ndarray,
    target_image: np.ndarray,
    config: AlignmentConfig = DEFAULT_ALIGNMENT_CONFIG,
    feature_config: FeatureConfig = DEFAULT_FEATURE_CONFIG,
) -> AlignmentResult:
    """Convenience wrapper: extract ORB features then align."""
    from core.feature_extractor import extract_features

    source = extract_features(source_image, feature_config)
    target = extract_features(target_image, feature_config)
    return align_features(source, target, config)


def warp_to_target(
    source_image: np.ndarray,
    alignment: AlignmentResult,
    target_shape: tuple[int, int],
    border_value: int = 255,
) -> np.ndarray:
    """Warp ``source_image`` into the target frame.

    ``cv2.warpAffine``/``warpPerspective`` with the forward matrix already pulls
    source pixels into target coordinates, so the matrix is used as fitted. (An
    earlier version inverted it, which buried genuine matches under ~0.15
    coverage instead of ~0.68.)
    """
    if not alignment.is_aligned:
        raise ValueError("Alignment tidak tersedia.")

    height, width = target_shape[:2]
    matrix = alignment.matrix
    if alignment.model is TransformModel.HOMOGRAPHY:
        return cv2.warpPerspective(
            source_image,
            matrix,
            (width, height),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=border_value,
        )
    return cv2.warpAffine(
        source_image,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=border_value,
    )


def make_side_by_side(
    reference: np.ndarray,
    candidate: np.ndarray,
    gap: int = 12,
) -> np.ndarray:
    """Place two same-sized grayscale images side by side."""
    left = to_grayscale(reference)
    right = to_grayscale(candidate)
    height = max(left.shape[0], right.shape[0])

    def _pad(image: np.ndarray) -> np.ndarray:
        if image.shape[0] == height:
            return image
        pad = height - image.shape[0]
        return cv2.copyMakeBorder(
            image, 0, pad, 0, 0, cv2.BORDER_CONSTANT, value=255
        )

    divider = np.full((height, gap), 255, dtype=np.uint8)
    return np.hstack([_pad(left), divider, _pad(right)])


def ink_mask(gray: np.ndarray, threshold: int = 225) -> np.ndarray:
    """Binary ink mask tuned for faint scanned line art.

    Otsu picks a threshold around 175 on these pages, which keeps only the heavy
    parcel outline and drops the faint base-map lines that actually need to be
    compared. A fixed 225 keeps the base map while 245 starts merging glyphs into
    blobs, so 225 is the default.
    """
    if threshold and threshold > 0:
        _, binary = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY_INV)
    else:
        _, binary = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
        )
    return binary


def overlay_alignment(
    reference_image: np.ndarray,
    warped_image: np.ndarray,
    threshold: int = 225,
) -> np.ndarray:
    """BGR overlay: reference ink in blue, warped candidate ink in red.

    Overlapping ink appears dark purple, so agreement is visible at a glance.
    """
    ref = to_grayscale(reference_image)
    cand = to_grayscale(warped_image)
    if ref.shape != cand.shape:
        cand = cv2.resize(cand, (ref.shape[1], ref.shape[0]), interpolation=cv2.INTER_AREA)

    ref_ink = ink_mask(ref, threshold)
    cand_ink = ink_mask(cand, threshold)

    canvas = np.full((ref.shape[0], ref.shape[1], 3), 255, dtype=np.uint8)
    canvas[ref_ink > 0] = (255, 120, 40)      # BGR -> blue-ish
    canvas[cand_ink > 0] = (40, 60, 210)      # BGR -> red-ish
    overlap = (ref_ink > 0) & (cand_ink > 0)
    canvas[overlap] = (60, 30, 30)
    return canvas
