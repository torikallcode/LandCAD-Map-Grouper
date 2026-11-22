from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from utils.image_utils import to_grayscale
from utils.logging_utils import get_logger

logger = get_logger("parcel_detector")

PARCEL_VERSION = "parcel-v1"


@dataclass(frozen=True)
class ParcelConfig:
    """Thresholds for main-parcel detection.

    The reference maps draw the main parcel with lines that are *not* thicker
    than the base map (median stroke 2.8px vs 2.0-4.0px) but are consistently
    much darker: 100% of sampled edge pixels were <=128 grey, while only 22-34%
    of the base-map ink was that dark. Detection therefore keys on a closed
    polygon whose edges are uniformly dark, not on stroke thickness alone.
    """

    ink_threshold: int = 200
    close_kernel: int = 5
    approx_epsilon: float = 0.02
    min_vertices: int = 4
    max_vertices: int = 8
    min_area_fraction: float = 0.005
    max_area_fraction: float = 0.15
    max_border_fraction: float = 0.85
    dark_threshold: int = 150
    min_dark_fraction: float = 0.80
    min_confidence: float = 0.50
    sample_window: int = 2
    mask_thickness: int = 9


DEFAULT_PARCEL_CONFIG = ParcelConfig()


@dataclass(frozen=True)
class ParcelCandidate:
    """One closed polygon that may be the main parcel."""

    polygon: np.ndarray = field(repr=False)
    area: float = 0.0
    area_fraction: float = 0.0
    vertex_count: int = 0
    bounding_box: tuple[int, int, int, int] = (0, 0, 0, 0)
    dark_fraction: float = 0.0
    median_intensity: float = 0.0
    median_stroke_width: float = 0.0
    confidence: float = 0.0

    @property
    def is_reliable(self) -> bool:
        return self.confidence >= DEFAULT_PARCEL_CONFIG.min_confidence

    @property
    def points(self) -> np.ndarray:
        return self.polygon.reshape(-1, 2)

    @property
    def needs_review(self) -> bool:
        """PRD requires flagging uncertain detections for manual review."""
        return not self.is_reliable


@dataclass(frozen=True)
class ParcelDetection:
    candidates: tuple[ParcelCandidate, ...] = ()
    config: ParcelConfig = DEFAULT_PARCEL_CONFIG

    @property
    def has_parcel(self) -> bool:
        return bool(self.candidates)

    @property
    def count(self) -> int:
        return len(self.candidates)

    @property
    def primary(self) -> ParcelCandidate | None:
        return max(self.candidates, key=lambda c: c.confidence) if self.candidates else None

    @property
    def needs_review(self) -> bool:
        primary = self.primary
        return primary is not None and primary.needs_review

    @property
    def label(self) -> str:
        if not self.has_parcel:
            return "Tidak terdeteksi"
        primary = self.primary
        status = "perlu pemeriksaan" if self.needs_review else "terdeteksi"
        return f"{status} ({primary.confidence:.2f})"


@dataclass(frozen=True)
class ParcelMask:
    """Binary mask covering the main parcel outlines (255 = masked out)."""

    mask: np.ndarray = field(repr=False)
    covered_fraction: float = 0.0
    detection: ParcelDetection = ParcelDetection()

    @property
    def is_empty(self) -> bool:
        return not self.detection.has_parcel or self.covered_fraction <= 0.0

    def apply(self, image: np.ndarray, fill: int = 255) -> np.ndarray:
        """Paint the masked region with the background value."""
        result = image.copy()
        if result.ndim == 3:
            result[self.mask > 0] = (fill, fill, fill)
        else:
            result[self.mask > 0] = fill
        return result


def _ink_mask(gray: np.ndarray, config: ParcelConfig) -> np.ndarray:
    _, binary = cv2.threshold(gray, config.ink_threshold, 255, cv2.THRESH_BINARY_INV)
    return (binary > 0).astype(np.uint8)


def _edge_points(polygon: np.ndarray) -> list[tuple[int, int]]:
    points = polygon.reshape(-1, 2).astype(float)
    samples: list[tuple[int, int]] = []
    for index in range(len(points)):
        start = points[index]
        end = points[(index + 1) % len(points)]
        length = float(np.linalg.norm(end - start))
        steps = max(int(length), 2)
        for t in np.linspace(0.0, 1.0, steps):
            x, y = (start + (end - start) * t).astype(int)
            samples.append((int(x), int(y)))
    return samples


def _sample(gray: np.ndarray, points: list[tuple[int, int]], window: int) -> np.ndarray:
    """Darkest value in a small window around each point (the stroke core)."""
    height, width = gray.shape[:2]
    values: list[float] = []
    for x, y in points:
        y0, y1 = max(y - window, 0), min(y + window + 1, height)
        x0, x1 = max(x - window, 0), min(x + window + 1, width)
        if y1 > y0 and x1 > x0:
            values.append(float(gray[y0:y1, x0:x1].min()))
    return np.asarray(values, dtype=np.float64)


def _stroke_widths(dist: np.ndarray, points: list[tuple[int, int]], window: int) -> np.ndarray:
    height, width = dist.shape[:2]
    values: list[float] = []
    for x, y in points:
        y0, y1 = max(y - window, 0), min(y + window + 1, height)
        x0, x1 = max(x - window, 0), min(x + window + 1, width)
        if y1 > y0 and x1 > x0:
            values.append(float(dist[y0:y1, x0:x1].max()))
    return np.asarray([value for value in values if value > 0], dtype=np.float64)


def _confidence(dark_fraction: float, area_fraction: float, vertex_count: int) -> float:
    """Blend darkness consistency with shape plausibility."""
    darkness = min(max(dark_fraction, 0.0), 1.0)
    # a parcel is a meaningful chunk of the page but not the whole page
    size = 1.0
    if area_fraction < 0.01:
        size = area_fraction / 0.01
    elif area_fraction > 0.10:
        size = max(0.0, 1.0 - (area_fraction - 0.10) / 0.10)
    shape = 1.0 if 4 <= vertex_count <= 6 else 0.7
    return round(0.70 * darkness * shape + 0.30 * size, 4)


def detect_main_parcels(
    image: np.ndarray, config: ParcelConfig = DEFAULT_PARCEL_CONFIG
) -> ParcelDetection:
    """Find closed, uniformly dark polygons that look like the main parcel."""
    gray = to_grayscale(image)
    height, width = gray.shape[:2]
    page_area = float(height * width)
    if page_area <= 0:
        return ParcelDetection(config=config)

    ink = _ink_mask(gray, config)
    stroke = cv2.distanceTransform(ink, cv2.DIST_L2, 5) * 2.0
    closed = cv2.morphologyEx(
        ink, cv2.MORPH_CLOSE, np.ones((config.close_kernel, config.close_kernel), np.uint8)
    )

    contours, hierarchy = cv2.findContours(
        closed, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
    )
    if hierarchy is None:
        return ParcelDetection(config=config)
    hierarchy = hierarchy[0]

    candidates: list[ParcelCandidate] = []
    for index, contour in enumerate(contours):
        if hierarchy[index][3] != -1:
            continue

        area = float(cv2.contourArea(contour))
        area_fraction = area / page_area
        if not (config.min_area_fraction <= area_fraction <= config.max_area_fraction):
            continue

        x, y, box_width, box_height = cv2.boundingRect(contour)
        if box_width > width * config.max_border_fraction:
            continue
        if box_height > height * config.max_border_fraction:
            continue

        perimeter = float(cv2.arcLength(contour, True))
        approx = cv2.approxPolyDP(contour, config.approx_epsilon * perimeter, True)
        vertex_count = len(approx)
        if not (config.min_vertices <= vertex_count <= config.max_vertices):
            continue

        points = _edge_points(approx)
        intensities = _sample(gray, points, config.sample_window)
        if intensities.size == 0:
            continue

        dark_fraction = float((intensities <= config.dark_threshold).mean())
        if dark_fraction < config.min_dark_fraction:
            continue

        widths = _stroke_widths(stroke, points, config.sample_window)

        candidates.append(
            ParcelCandidate(
                polygon=approx,
                area=area,
                area_fraction=area_fraction,
                vertex_count=vertex_count,
                bounding_box=(x, y, box_width, box_height),
                dark_fraction=round(dark_fraction, 4),
                median_intensity=round(float(np.median(intensities)), 1),
                median_stroke_width=(
                    round(float(np.median(widths)), 2) if widths.size else 0.0
                ),
                confidence=_confidence(dark_fraction, area_fraction, vertex_count),
            )
        )

    candidates.sort(key=lambda candidate: -candidate.confidence)
    logger.debug(
        "Deteksi bidang: %d kandidat dari %d kontur", len(candidates), len(contours)
    )
    return ParcelDetection(candidates=tuple(candidates), config=config)


def build_parcel_mask(
    image: np.ndarray,
    detection: ParcelDetection | None = None,
    config: ParcelConfig = DEFAULT_PARCEL_CONFIG,
) -> ParcelMask:
    """Mask the main parcel *outlines* only.

    The interior is deliberately left untouched: it holds parcel labels that are
    part of the document identity, and the surrounding base map must survive
    (PRD: masking has to preserve the base map around the main parcel).
    """
    gray = to_grayscale(image)
    detection = detection or detect_main_parcels(gray, config)
    mask = np.zeros(gray.shape, dtype=np.uint8)

    if not detection.has_parcel:
        return ParcelMask(mask=mask, covered_fraction=0.0, detection=detection)

    for candidate in detection.candidates:
        cv2.polylines(
            mask,
            [candidate.polygon],
            isClosed=True,
            color=255,
            thickness=config.mask_thickness,
        )

    covered = float(np.count_nonzero(mask)) / float(mask.size)
    return ParcelMask(
        mask=mask, covered_fraction=round(covered, 5), detection=detection
    )


def parcel_summary(detection: ParcelDetection) -> str:
    if not detection.has_parcel:
        return "Bidang utama: tidak terdeteksi."
    lines = [f"Bidang utama: {detection.label}", f"Kandidat: {detection.count}"]
    for index, candidate in enumerate(detection.candidates, start=1):
        lines.append(
            f"  #{index}: area {candidate.area_fraction * 100:.2f}% halaman, "
            f"{candidate.vertex_count} sudut, "
            f"gelap {candidate.dark_fraction * 100:.0f}%, "
            f"tebal {candidate.median_stroke_width:.1f}px, "
            f"keyakinan {candidate.confidence:.2f}"
        )
    if detection.needs_review:
        lines.append("Catatan: keyakinan rendah, hasil perlu diperiksa manual.")
    return "\n".join(lines)
