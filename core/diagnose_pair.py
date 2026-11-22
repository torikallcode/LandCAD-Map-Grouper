"""Diagnostics: how much of a pair's similarity comes from page template?

Every map page in this document family carries the same furniture: an outer
frame, a north arrow, ``SKALA 1 : 500``, ``PENJELASAN`` and ``batas tanah ini``.
ORB happily matches that furniture in *every* pair, which would inflate all
scores equally and make different maps look alike.

This module measures that effect instead of assuming it. It changes no behaviour;
it only reports numbers so a fix can be justified.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from core.feature_extractor import OrbFeatures
from core.image_alignment import ink_mask, warp_to_target
from utils.image_utils import to_grayscale

DIAGNOSTIC_VERSION = "diagnose-v1"


@dataclass(frozen=True)
class RegionSplit:
    """Border and interior masks for one page."""

    border: np.ndarray
    interior: np.ndarray
    margin_fraction: float

    @property
    def border_fraction(self) -> float:
        return float(np.count_nonzero(self.border)) / self.border.size

    @property
    def interior_fraction(self) -> float:
        return float(np.count_nonzero(self.interior)) / self.interior.size


@dataclass(frozen=True)
class RegionScore:
    """Similarity measured separately for the page border and the map interior."""

    overall: float
    interior: float
    border: float
    interior_inliers: int
    border_inliers: int
    interior_ink: int
    border_ink: int

    @property
    def border_ink_share(self) -> float:
        total = self.interior_ink + self.border_ink
        return self.border_ink / total if total else 0.0

    @property
    def template_dominated(self) -> bool:
        """Border matches clearly better than the map body itself."""
        return self.border - self.interior >= 10.0

    def as_dict(self) -> dict[str, float]:
        return {
            "overall": round(self.overall, 2),
            "interior": round(self.interior, 2),
            "border": round(self.border, 2),
            "interior_inliers": float(self.interior_inliers),
            "border_inliers": float(self.border_inliers),
            "border_ink_share": round(self.border_ink_share, 3),
        }


def region_split(image: np.ndarray, margin_fraction: float = 0.12) -> RegionSplit:
    """Split a page into an outer border band and the interior."""
    gray = to_grayscale(image)
    height, width = gray.shape[:2]
    margin_y = max(int(round(height * margin_fraction)), 1)
    margin_x = max(int(round(width * margin_fraction)), 1)

    interior = np.zeros((height, width), dtype=np.uint8)
    interior[margin_y:height - margin_y, margin_x:width - margin_x] = 1
    border = (interior == 0).astype(np.uint8)
    return RegionSplit(border=border, interior=interior, margin_fraction=margin_fraction)


def _coverage_in_region(
    reference_ink: np.ndarray,
    candidate_ink: np.ndarray,
    region: np.ndarray,
    tolerance: float,
) -> float:
    """Symmetric ink coverage restricted to ``region``."""
    ref = reference_ink & (region > 0)
    cand = candidate_ink & (region > 0)
    if not ref.any() or not cand.any():
        return 0.0

    distance_to_cand = cv2.distanceTransform(
        (~(candidate_ink > 0)).astype(np.uint8), cv2.DIST_L2, 3
    )
    distance_to_ref = cv2.distanceTransform(
        (~(reference_ink > 0)).astype(np.uint8), cv2.DIST_L2, 3
    )
    ref_covered = float((distance_to_cand[ref] <= tolerance).mean())
    cand_covered = float((distance_to_ref[cand] <= tolerance).mean())
    return min(ref_covered, cand_covered)


def score_by_region(
    reference_image: np.ndarray,
    candidate_image: np.ndarray,
    alignment,
    margin_fraction: float = 0.12,
    threshold: int = 225,
    tolerance: float = 6.0,
) -> RegionScore:
    """Measure border vs interior agreement for an already-fitted alignment.

    ``alignment`` must map ``candidate_image`` onto ``reference_image`` (the same
    convention used by :func:`core.image_alignment.warp_to_target`).
    """
    warped = warp_to_target(candidate_image, alignment, reference_image.shape)
    ref_gray = to_grayscale(reference_image)
    warped_gray = to_grayscale(warped)

    ref_ink = ink_mask(ref_gray, threshold) > 0
    cand_ink = ink_mask(warped_gray, threshold) > 0

    split = region_split(ref_gray, margin_fraction)
    overall = _coverage_in_region(ref_ink, cand_ink, np.ones_like(split.border), tolerance)
    interior = _coverage_in_region(ref_ink, cand_ink, split.interior, tolerance)
    border = _coverage_in_region(ref_ink, cand_ink, split.border, tolerance)

    points = alignment.source_points
    if points is None or len(points) == 0:
        interior_inliers = border_inliers = 0
    else:
        points = np.asarray(points, dtype=np.float64)
        ys = np.clip(points[:, 1].astype(int), 0, ref_gray.shape[0] - 1)
        xs = np.clip(points[:, 0].astype(int), 0, ref_gray.shape[1] - 1)
        inside = split.interior[ys, xs] > 0
        interior_inliers = int(inside.sum())
        border_inliers = int(len(points) - interior_inliers)

    return RegionScore(
        overall=overall * 100.0,
        interior=interior * 100.0,
        border=border * 100.0,
        interior_inliers=interior_inliers,
        border_inliers=border_inliers,
        interior_ink=int(np.count_nonzero(ref_ink & (split.interior > 0))),
        border_ink=int(np.count_nonzero(ref_ink & (split.border > 0))),
    )


def isolate_template(
    image: np.ndarray, margin_fraction: float = 0.12
) -> np.ndarray:
    """Keep only the page border band; blank the interior.

    Comparing a full page against this "hollow" version shows how much score the
    template furniture alone can produce.
    """
    gray = to_grayscale(image).copy()
    split = region_split(gray, margin_fraction)
    gray[split.interior > 0] = 255
    return gray


def diagnose_pair(
    reference_image: np.ndarray,
    candidate_image: np.ndarray,
    reference_features: OrbFeatures,
    candidate_features: OrbFeatures,
    margin_fraction: float = 0.12,
) -> dict[str, float]:
    """Full diagnostic for one pair (alignment + region breakdown)."""
    from core.image_alignment import align_features

    alignment = align_features(candidate_features, reference_features)
    if not alignment.is_aligned:
        return {
            "aligned": 0.0,
            "match_count": float(alignment.match_count),
            "inlier_count": float(alignment.inlier_count),
            "overall": 0.0,
            "interior": 0.0,
            "border": 0.0,
        }

    regions = score_by_region(
        reference_image, candidate_image, alignment, margin_fraction
    )
    payload = {"aligned": 1.0, "match_count": float(alignment.match_count),
               "inlier_count": float(alignment.inlier_count)}
    payload.update(regions.as_dict())
    return payload


__all__ = [
    "DIAGNOSTIC_VERSION",
    "RegionScore",
    "RegionSplit",
    "diagnose_pair",
    "isolate_template",
    "region_split",
    "score_by_region",
]
