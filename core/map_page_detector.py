from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from core.image_normalizer import NormalizationConfig, DEFAULT_CONFIG
from utils.image_utils import (
    binarize,
    hough_segments,
    resize_long_side,
    segment_angle_degrees,
    to_grayscale,
)

DETECTOR_VERSION = "map-page-v1"


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _decreasing(value: float, low: float, high: float) -> float:
    """1.0 when value <= low, 0.0 when value >= high."""
    if high == low:
        return 0.0
    return _clamp((high - value) / (high - low))


def _increasing(value: float, low: float, high: float) -> float:
    """0.0 when value <= low, 1.0 when value >= high."""
    if high == low:
        return 0.0
    return _clamp((value - low) / (high - low))


@dataclass(frozen=True)
class PageFeatures:
    """Measured visual descriptors for one rendered page."""

    components_per_megapixel: float = 0.0
    text_rows: int = 0
    ink_ratio: float = 0.0
    blank_ratio: float = 0.0
    diagonal_share: float = 0.0
    line_strength: float = 0.0
    width: int = 0
    height: int = 0

    @property
    def megapixels(self) -> float:
        return (self.width * self.height) / 1e6 if self.width and self.height else 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "components_per_mp": round(self.components_per_megapixel, 1),
            "text_rows": float(self.text_rows),
            "ink_ratio": round(self.ink_ratio, 4),
            "blank_ratio": round(self.blank_ratio, 3),
            "diagonal_share": round(self.diagonal_share, 3),
            "line_strength": round(self.line_strength, 3),
        }


@dataclass(frozen=True)
class PageAssessment:
    page_index: int
    is_map: bool
    score: float
    features: PageFeatures
    reasons: tuple[str, ...] = ()

    @property
    def page_number(self) -> int:
        return self.page_index + 1

    @property
    def label(self) -> str:
        return "Peta" if self.is_map else "Bukan peta"


@dataclass(frozen=True)
class MapDetectionResult:
    pages: tuple[PageAssessment, ...]
    map_page: int | None
    detector_version: str = DETECTOR_VERSION

    @property
    def has_map(self) -> bool:
        return self.map_page is not None

    @property
    def map_count(self) -> int:
        return sum(1 for page in self.pages if page.is_map)

    @property
    def map_page_number(self) -> int | None:
        return None if self.map_page is None else self.map_page + 1

    def assessment(self, page_index: int) -> PageAssessment | None:
        for page in self.pages:
            if page.page_index == page_index:
                return page
        return None

    @property
    def best_page(self) -> PageAssessment | None:
        if not self.pages:
            return None
        return max(self.pages, key=lambda page: page.score)


class MapPageDetector:
    """Scores pages by structural-lines-versus-text-layout signals.

    The classifier deliberately avoids a fixed "the map is page 2" rule. The
    separating features were measured on the reference documents: administrative
    pages carry ~1100 connected components per megapixel in dense text rows,
    while the scanned map pages carry ~40 with much emptier, line-dominated
    layouts. Weights are exposed so they can be recalibrated on a labelled set.
    """

    def __init__(
        self,
        threshold: float = 0.45,
        weights: dict[str, float] | None = None,
        config: NormalizationConfig | None = None,
    ) -> None:
        self.threshold = threshold
        self.weights = {
            "text_density": 0.30,
            "ink": 0.22,
            "structure": 0.22,
            "blank": 0.16,
            "text_rows": 0.10,
        }
        if weights:
            unknown = set(weights) - set(self.weights)
            if unknown:
                raise ValueError(f"Bobot tidak dikenal: {sorted(unknown)}")
            self.weights.update(weights)
        self._config = config or DEFAULT_CONFIG

    # ------------------------------------------------------------- features
    def measure(self, image: np.ndarray) -> PageFeatures:
        gray = to_grayscale(image)
        working, _ = resize_long_side(gray, self._config.target_long_side)
        height, width = working.shape[:2]
        page_area = float(width * height) or 1.0
        megapixels = page_area / 1e6

        binary = binarize(working)
        ink_ratio = float(np.count_nonzero(binary)) / page_area

        count, _labels, _stats, _centroids = cv2.connectedComponentsWithStats(
            binary, connectivity=8
        )
        components = max(count - 1, 0)
        components_per_mp = components / megapixels if megapixels else 0.0

        projection = (binary > 0).sum(axis=1).astype(np.float64)
        peak = float(projection.max()) if projection.size else 0.0
        if peak > 0:
            normalized = projection / peak
            active = normalized > 0.12
            text_rows = int(np.count_nonzero(np.diff(active.astype(np.int8)) == 1))
        else:
            text_rows = 0

        blank_ratio = self._blank_ratio(binary)

        diagonal, axis = self._segment_lengths(binary)
        total = diagonal + axis
        diagonal_share = diagonal / total if total > 0 else 0.0
        line_strength = min(total / (2.0 * math.hypot(width, height)), 1.0)

        return PageFeatures(
            components_per_megapixel=components_per_mp,
            text_rows=text_rows,
            ink_ratio=ink_ratio,
            blank_ratio=blank_ratio,
            diagonal_share=diagonal_share,
            line_strength=line_strength,
            width=width,
            height=height,
        )

    @staticmethod
    def _blank_ratio(binary: np.ndarray) -> float:
        kernel = np.ones((25, 25), np.uint8)
        dilated = cv2.dilate(binary, kernel)
        return float(1.0 - (np.count_nonzero(dilated) / dilated.size))

    @staticmethod
    def _segment_lengths(binary: np.ndarray) -> tuple[float, float]:
        diagonal = 0.0
        axis = 0.0
        for segment in hough_segments(binary):
            angle = abs(segment_angle_degrees(segment))
            x1, y1, x2, y2 = (float(value) for value in segment)
            length = math.hypot(x2 - x1, y2 - y1)
            if min(angle, abs(angle - 90.0)) < 6.0:
                axis += length
            elif 20.0 < angle < 70.0:
                diagonal += length
        return diagonal, axis

    # -------------------------------------------------------------- scoring
    def score(self, features: PageFeatures) -> float:
        text_density = _decreasing(
            math.log10(max(features.components_per_megapixel, 1.0)), 1.75, 2.75
        )
        ink = _decreasing(features.ink_ratio, 0.010, 0.026)
        blank = _increasing(features.blank_ratio, 0.75, 0.94)
        structure = _clamp(0.55 * features.diagonal_share + 0.45 * features.line_strength)
        text_rows = _decreasing(features.text_rows, 3.0, 12.0)

        weights = self.weights
        total = (
            weights["text_density"] * text_density
            + weights["ink"] * ink
            + weights["structure"] * structure
            + weights["blank"] * blank
            + weights["text_rows"] * text_rows
        )
        return round(_clamp(total), 4)

    def assess_page(self, image: np.ndarray, page_index: int = 0) -> PageAssessment:
        features = self.measure(image)
        score = self.score(features)
        return PageAssessment(
            page_index=page_index,
            is_map=score >= self.threshold,
            score=score,
            features=features,
            reasons=self._explain(features, score),
        )

    def assess_pages(self, images: list[np.ndarray]) -> MapDetectionResult:
        pages = tuple(
            self.assess_page(image, index) for index, image in enumerate(images)
        )
        map_candidates = [(page.score, page.page_index) for page in pages if page.is_map]
        map_page = max(map_candidates)[1] if map_candidates else None
        return MapDetectionResult(pages=pages, map_page=map_page)

    def _explain(self, features: PageFeatures, score: float) -> tuple[str, ...]:
        notes: list[str] = []
        if features.components_per_megapixel > 400:
            notes.append("kepadatan teks tinggi")
        else:
            notes.append("kepadatan teks rendah")
        if features.blank_ratio > 0.90:
            notes.append("area kosong luas")
        if features.diagonal_share > 0.15:
            notes.append("banyak garis diagonal")
        if features.text_rows >= 9:
            notes.append("banyak baris teks")
        verdict = "kandidat peta" if score >= self.threshold else "bukan peta"
        notes.append(f"skor {score:.2f} ({verdict})")
        return tuple(notes)
