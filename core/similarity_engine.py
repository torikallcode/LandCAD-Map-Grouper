from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from core.feature_extractor import OrbFeatures
from core.image_alignment import (
    AlignmentConfig,
    AlignmentResult,
    TransformModel,
    align_features,
    ink_mask,
    match_features,
    warp_to_target,
)
from utils.image_utils import hough_segments, to_grayscale
from utils.logging_utils import get_logger

logger = get_logger("similarity_engine")

SIMILARITY_VERSION = "similarity-v1"


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class SimilarityWeights:
    """Weights for each indicator. PRD requires more than one indicator.

    ``interior_coverage`` replaced whole-page coverage as the dominant indicator
    after measurement showed why whole-page coverage is wrong twice over:

    * It counts page furniture (frame, north arrow, legend, scale text), which is
      identical in every document. A DIFFERENT map inside the same frame scored
      border coverage 100.0 with 786 inliers there and 0 in the map body.
    * It punishes rotation: rotating a genuine pair 8 degrees dropped whole-page
      coverage to 0.53 while interior coverage stayed at 0.82.

    Measured interior coverage:

    ==========================  =======================
    Pair                        interior coverage
    ==========================  =======================
    same map, rotation 0-10 deg  0.816 - 0.825
    different map, same frame    0.018 - 0.057
    unrelated map                0.000
    ==========================  =======================

    One indicator still cannot decide on its own, so alignment quality, inlier
    support, line structure and inlier spread keep their supporting role.
    """

    interior_coverage: float = 0.45
    alignment: float = 0.20
    inlier_support: float = 0.15
    line_structure: float = 0.12
    inlier_spread: float = 0.08

    def total(self) -> float:
        return (
            self.interior_coverage
            + self.alignment
            + self.inlier_support
            + self.line_structure
            + self.inlier_spread
        )

    def normalised(self) -> "SimilarityWeights":
        total = self.total() or 1.0
        return SimilarityWeights(
            interior_coverage=self.interior_coverage / total,
            alignment=self.alignment / total,
            inlier_support=self.inlier_support / total,
            line_structure=self.line_structure / total,
            inlier_spread=self.inlier_spread / total,
        )


DEFAULT_WEIGHTS = SimilarityWeights()

# PRD section 9 display bands (initial values, not validated accuracy).
BAND_HIGH = 85.0
BAND_REVIEW = 65.0


@dataclass(frozen=True)
class SimilarityConfig:
    weights: SimilarityWeights = DEFAULT_WEIGHTS
    reference_coverage_saturation: float = 0.85
    inlier_saturation: int = 250
    ink_threshold: int = 225
    tolerance: float = 6.0
    min_reference_coverage: float = 0.40
    min_inliers: int = 30
    # Evidence must come from the map body, not the page furniture. Measured on
    # the reference documents: a DIFFERENT map drawn inside the same frame scored
    # border coverage 100.0 with 786 inliers there, but interior coverage 1.8-5.8
    # with ZERO interior inliers. Without this gate the template alone lifted such
    # pairs to ~65 points.
    interior_margin: float = 0.12
    min_interior_coverage: float = 0.45
    min_interior_inliers: int = 25


DEFAULT_SIMILARITY_CONFIG = SimilarityConfig()


@dataclass(frozen=True)
class SimilarityIndicators:
    """Raw measurements behind a similarity score."""

    reference_coverage: float = 0.0
    candidate_coverage: float = 0.0
    alignment_quality: float = 0.0
    inlier_count: int = 0
    inlier_ratio: float = 0.0
    line_structure: float = 0.0
    inlier_spread: float = 0.0
    reprojection_error: float = float("inf")
    interior_coverage: float = 0.0
    border_coverage: float = 0.0
    interior_inliers: int = 0
    border_inliers: int = 0

    def as_dict(self) -> dict[str, float]:
        return {
            "reference_coverage": round(self.reference_coverage, 4),
            "candidate_coverage": round(self.candidate_coverage, 4),
            "alignment_quality": round(self.alignment_quality, 4),
            "inlier_count": float(self.inlier_count),
            "inlier_ratio": round(self.inlier_ratio, 4),
            "line_structure": round(self.line_structure, 4),
            "inlier_spread": round(self.inlier_spread, 4),
            "reprojection_error": round(self.reprojection_error, 4),
            "interior_coverage": round(self.interior_coverage, 4),
            "border_coverage": round(self.border_coverage, 4),
            "interior_inliers": float(self.interior_inliers),
            "border_inliers": float(self.border_inliers),
        }


@dataclass(frozen=True)
class SimilarityScore:
    """Similarity decision for one document pair."""

    score: float = 0.0
    indicators: SimilarityIndicators = field(default_factory=SimilarityIndicators)
    aligned: bool = False
    blocked_reason: str = ""
    version: str = SIMILARITY_VERSION

    @property
    def band(self) -> str:
        if self.score >= BAND_HIGH:
            return "Kemiripan tinggi"
        if self.score >= BAND_REVIEW:
            return "Perlu pemeriksaan"
        return "Kemiripan rendah"

    @property
    def needs_review(self) -> bool:
        return BAND_REVIEW <= self.score < BAND_HIGH

    @property
    def is_candidate(self) -> bool:
        """Whether the pair passes the minimum bar to be worth grouping."""
        return self.aligned and self.score >= BAND_REVIEW

    @property
    def confidence(self) -> str:
        if not self.aligned:
            return "Tidak ada"
        if self.score >= BAND_HIGH:
            return "Tinggi"
        if self.score >= BAND_REVIEW:
            return "Sedang"
        return "Rendah"


@dataclass(frozen=True)
class SimilarityResult:
    score: SimilarityScore
    alignment: AlignmentResult
    warped: np.ndarray | None = field(default=None, repr=False)


def _directional_coverage(
    reference: np.ndarray,
    warped: np.ndarray,
    threshold: int,
    tolerance: float,
) -> tuple[float, float]:
    ref_ink = ink_mask(to_grayscale(reference), threshold) > 0
    cand_ink = ink_mask(to_grayscale(warped), threshold) > 0
    if not ref_ink.any() or not cand_ink.any():
        return 0.0, 0.0

    if ref_ink.shape != cand_ink.shape:
        cand_ink = cv2.resize(
            cand_ink.astype(np.uint8),
            (ref_ink.shape[1], ref_ink.shape[0]),
            interpolation=cv2.INTER_NEAREST,
        ) > 0

    dist_to_cand = cv2.distanceTransform((~cand_ink).astype(np.uint8), cv2.DIST_L2, 3)
    dist_to_ref = cv2.distanceTransform((~ref_ink).astype(np.uint8), cv2.DIST_L2, 3)

    ref_covered = float((dist_to_cand[ref_ink] <= tolerance).mean())
    cand_covered = float((dist_to_ref[cand_ink] <= tolerance).mean())
    return ref_covered, cand_covered


def _region_coverage(
    reference_ink: np.ndarray,
    warped_ink: np.ndarray,
    region: np.ndarray,
    tolerance: float,
) -> float:
    """Symmetric ink coverage measured only inside ``region``."""
    ref = reference_ink & (region > 0)
    cand = warped_ink & (region > 0)
    if not ref.any() or not cand.any():
        return 0.0

    dist_to_cand = cv2.distanceTransform((~warped_ink).astype(np.uint8), cv2.DIST_L2, 3)
    dist_to_ref = cv2.distanceTransform((~reference_ink).astype(np.uint8), cv2.DIST_L2, 3)
    ref_covered = float((dist_to_cand[ref] <= tolerance).mean())
    cand_covered = float((dist_to_ref[cand] <= tolerance).mean())
    return min(ref_covered, cand_covered)


def _split_inliers(
    points: np.ndarray | None, interior: np.ndarray
) -> tuple[int, int]:
    """Count inliers inside the map body versus the page furniture."""
    if points is None or len(points) == 0:
        return 0, 0
    points = np.asarray(points, dtype=np.float64)
    height, width = interior.shape[:2]
    ys = np.clip(points[:, 1].astype(int), 0, height - 1)
    xs = np.clip(points[:, 0].astype(int), 0, width - 1)
    inside = int(np.count_nonzero(interior[ys, xs] > 0))
    return inside, int(len(points) - inside)


def _line_length(gray: np.ndarray) -> float:
    binary = ink_mask(gray, 225)
    segments = hough_segments(binary)
    height, width = gray.shape[:2]
    diagonal = 2.0 * float(np.hypot(width, height)) or 1.0
    total = 0.0
    for segment in segments:
        x1, y1, x2, y2 = (float(value) for value in segment)
        total += float(np.hypot(x2 - x1, y2 - y1))
    return total / diagonal


def _inlier_spread(points: np.ndarray | None, shape: tuple[int, int]) -> float:
    """Fraction of a 3x3 grid containing inliers.

    A correct match spreads anchors over the whole page; a crop or a repeated
    pattern concentrates them in one area.
    """
    if points is None or len(points) < 3:
        return 0.0
    height, width = shape
    grid = np.zeros((3, 3), dtype=int)
    for x, y in points:
        row = min(int(y / max(height, 1) * 3), 2)
        column = min(int(x / max(width, 1) * 3), 2)
        grid[row, column] += 1
    return float((grid > 0).sum()) / 9.0


def _score_from_indicators(
    indicators: SimilarityIndicators, config: SimilarityConfig
) -> float:
    weights = config.weights.normalised()

    # Only the map body counts as evidence; the page frame is shared by every
    # document and would otherwise lift unrelated pairs (measured: +64 points).
    coverage = _clamp(
        indicators.interior_coverage
        / max(config.reference_coverage_saturation, 1e-6)
    )
    alignment = _clamp(indicators.alignment_quality)
    support = _clamp(indicators.interior_inliers / max(config.inlier_saturation, 1))
    structure = _clamp(indicators.line_structure)
    spread = _clamp(indicators.inlier_spread)

    combined = (
        weights.interior_coverage * coverage
        + weights.alignment * alignment
        + weights.inlier_support * support
        + weights.line_structure * structure
        + weights.inlier_spread * spread
    )
    return round(_clamp(combined) * 100.0, 1)


def score_pair(
    reference_image: np.ndarray,
    candidate_image: np.ndarray,
    reference_features: OrbFeatures,
    candidate_features: OrbFeatures,
    config: SimilarityConfig = DEFAULT_SIMILARITY_CONFIG,
    alignment_config: AlignmentConfig | None = None,
) -> SimilarityResult:
    """Compare two parcel-free map images and return a 0-100 similarity score."""
    if reference_features.is_empty or candidate_features.is_empty:
        return SimilarityResult(
            score=SimilarityScore(
                aligned=False, blocked_reason="fitur ORB tidak mencukupi"
            ),
            alignment=AlignmentResult(
                model=TransformModel.NONE,
                matrix=None,
                match_count=0,
                inlier_count=0,
                inlier_ratio=0.0,
                reprojection_error=float("inf"),
                quality=0.0,
                reason="fitur ORB tidak mencukupi",
            ),
        )

    alignment_kwargs = alignment_config or AlignmentConfig()
    alignment = align_features(candidate_features, reference_features, alignment_kwargs)

    if not alignment.is_aligned:
        return SimilarityResult(
            score=SimilarityScore(
                aligned=False,
                blocked_reason=alignment.reason or "alignment gagal",
            ),
            alignment=alignment,
        )

    warped = warp_to_target(candidate_image, alignment, reference_image.shape)
    ref_cov, cand_cov = _directional_coverage(
        reference_image, warped, config.ink_threshold, config.tolerance
    )

    # Split the evidence: page furniture (frame, north arrow, legend) is identical
    # in every document, so agreement there is not evidence of a shared map.
    from core.diagnose_pair import region_split

    split = region_split(
        to_grayscale(reference_image), config.interior_margin
    )
    ref_ink = ink_mask(to_grayscale(reference_image), config.ink_threshold) > 0
    warped_ink = ink_mask(to_grayscale(warped), config.ink_threshold) > 0
    interior_cov = _region_coverage(
        ref_ink, warped_ink, split.interior, config.tolerance
    )
    border_cov = _region_coverage(ref_ink, warped_ink, split.border, config.tolerance)

    interior_inliers, border_inliers = _split_inliers(
        alignment.source_points, split.interior
    )

    ref_line = _line_length(reference_image)
    warped_line = _line_length(warped)
    line_similarity = (
        1.0 - abs(ref_line - warped_line) / max(ref_line, warped_line, 1e-6)
        if max(ref_line, warped_line) > 0
        else 0.0
    )

    indicators = SimilarityIndicators(
        reference_coverage=ref_cov,
        candidate_coverage=cand_cov,
        alignment_quality=alignment.quality,
        inlier_count=alignment.inlier_count,
        inlier_ratio=alignment.inlier_ratio,
        line_structure=line_similarity,
        inlier_spread=_inlier_spread(alignment.source_points, reference_image.shape),
        reprojection_error=alignment.reprojection_error,
        interior_coverage=interior_cov,
        border_coverage=border_cov,
        interior_inliers=interior_inliers,
        border_inliers=border_inliers,
    )

    # Hard gates: a well-aligned crop still explains almost none of the page, and
    # a pair that only shares the page frame has no interior evidence at all.
    reasons: list[str] = []
    if ref_cov < config.min_reference_coverage:
        reasons.append(
            f"cakupan peta acuan rendah ({ref_cov:.2f} < {config.min_reference_coverage:.2f})"
        )
    if alignment.inlier_count < config.min_inliers:
        reasons.append(
            f"inlier terlalu sedikit ({alignment.inlier_count} < {config.min_inliers})"
        )
    if interior_cov < config.min_interior_coverage:
        reasons.append(
            f"cakupan area isi peta rendah ({interior_cov:.2f} < "
            f"{config.min_interior_coverage:.2f})"
        )
    if interior_inliers < config.min_interior_inliers:
        reasons.append(
            f"bukti di area isi terlalu sedikit ({interior_inliers} < "
            f"{config.min_interior_inliers}); kecocokan hanya di bingkai/legenda"
        )

    if reasons:
        score = SimilarityScore(
            score=0.0,
            indicators=indicators,
            aligned=True,
            blocked_reason="; ".join(reasons),
        )
        return SimilarityResult(score=score, alignment=alignment, warped=warped)

    raw = _score_from_indicators(indicators, config)
    return SimilarityResult(
        score=SimilarityScore(score=raw, indicators=indicators, aligned=True),
        alignment=alignment,
        warped=warped,
    )


def prefilter_pair(
    reference_features: OrbFeatures,
    candidate_features: OrbFeatures,
    ratio: float = 0.75,
    min_matches: int = 12,
) -> bool:
    """Cheap gate: skip the expensive RANSAC alignment for obvious non-matches.

    Measured cost is ~9 ms per pair versus ~14 ms for a full alignment, and the
    reference pair produced 463 ratio-test matches while synthetic unrelated maps
    produced 15-220 (median 25). The gate therefore only removes the clearly
    hopeless pairs, it does not decide grouping on its own.
    """
    if reference_features.is_empty or candidate_features.is_empty:
        return False

    config = AlignmentConfig(ratio_test=ratio, min_matches=min_matches)
    matches = match_features(reference_features, candidate_features, config)
    return len(matches) >= min_matches


def similarity_summary(result: SimilarityResult) -> str:
    score = result.score
    if not score.aligned:
        return f"Tidak dapat dibandingkan: {score.blocked_reason}"

    indicators = score.indicators
    lines = [
        f"Skor kemiripan: {score.score:.1f} / 100 — {score.band}",
        f"Cakupan peta acuan: {indicators.reference_coverage:.3f}",
        f"Cakupan peta pembanding: {indicators.candidate_coverage:.3f}",
        f"Kualitas alignment: {indicators.alignment_quality:.3f}",
        f"Inlier: {indicators.inlier_count} ({indicators.inlier_ratio * 100:.1f}%)",
        f"Kemiripan struktur garis: {indicators.line_structure:.3f}",
        f"Sebaran inlier: {indicators.inlier_spread:.2f}",
        f"Galat reproyeksi: {indicators.reprojection_error:.2f} px",
    ]
    if score.blocked_reason:
        lines.append(f"Ditolak: {score.blocked_reason}")
    if score.needs_review:
        lines.append("Status: perlu pemeriksaan manual.")
    return "\n".join(lines)
