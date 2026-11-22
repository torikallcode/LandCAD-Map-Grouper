from __future__ import annotations

import cv2
import numpy as np

from core.parcel_detector import (
    DEFAULT_PARCEL_CONFIG,
    PARCEL_VERSION,
    ParcelCandidate,
    ParcelConfig,
    ParcelDetection,
    ParcelMask,
    build_parcel_mask,
    detect_main_parcels,
    parcel_summary,
)


def make_page_with_parcel(
    width=1000,
    height=1400,
    parcel_offset=(0, 0),
    parcel_value=100,
    parcel_thickness=3,
    base_value=220,
    seed=5,
):
    """Synthetic map: faint base-map lines plus one dark closed parcel."""
    rng = np.random.default_rng(seed)
    page = np.full((height, width), 255, np.uint8)

    for _ in range(30):
        x1 = int(rng.integers(120, width - 120))
        y1 = int(rng.integers(120, height - 120))
        length = int(rng.integers(60, 200))
        angle = float(rng.choice([0, 90, 35, -35, 145]))
        x2 = int(x1 + length * np.cos(np.radians(angle)))
        y2 = int(y1 + length * np.sin(np.radians(angle)))
        cv2.line(page, (x1, y1), (x2, y2), base_value, 1)

    ox, oy = parcel_offset
    corners = np.array(
        [
            [330 + ox, 560 + oy],
            [520 + ox, 600 + oy],
            [560 + ox, 780 + oy],
            [360 + ox, 740 + oy],
        ],
        dtype=np.int32,
    )
    cv2.polylines(page, [corners], True, parcel_value, parcel_thickness)
    return page, corners


# ------------------------------------------------------------------ detection
def test_detects_dark_closed_parcel():
    page, _ = make_page_with_parcel()
    detection = detect_main_parcels(page)

    assert detection.has_parcel
    assert detection.count >= 1
    primary = detection.primary
    assert primary is not None
    assert 4 <= primary.vertex_count <= 6
    assert primary.dark_fraction >= 0.8
    assert primary.confidence >= 0.5


def test_ignores_pale_closed_shapes():
    """A closed polygon drawn as faintly as the base map must not qualify."""
    page, _ = make_page_with_parcel(parcel_value=225)
    detection = detect_main_parcels(page)
    assert not detection.has_parcel


def test_ignores_open_shapes():
    page, _ = make_page_with_parcel()
    # draw a dark but open polyline; no closed contour should form
    open_page = np.full_like(page, 255)
    cv2.line(open_page, (200, 200), (500, 200), 90, 3)
    cv2.line(open_page, (500, 200), (500, 500), 90, 3)
    assert not detect_main_parcels(open_page).has_parcel


def test_ignores_full_page_frame():
    page = np.full((800, 600), 255, np.uint8)
    cv2.rectangle(page, (5, 5), (595, 795), 90, 3)
    detection = detect_main_parcels(page)
    assert not detection.has_parcel


def test_ignores_tiny_polygon():
    page = np.full((1000, 800), 255, np.uint8)
    cv2.polylines(
        page, [np.array([[10, 10], [40, 12], [42, 40], [12, 38]], np.int32)], True, 90, 2
    )
    assert not detect_main_parcels(page).has_parcel


def test_blank_page_has_no_parcel():
    assert not detect_main_parcels(np.full((600, 400), 255, np.uint8)).has_parcel


def test_empty_image_is_handled():
    detection = detect_main_parcels(np.zeros((0, 0), np.uint8))
    assert not detection.has_parcel
    assert detection.primary is None


def test_detection_is_position_independent():
    base, _ = make_page_with_parcel()
    shifted, _ = make_page_with_parcel(parcel_offset=(120, 90))
    assert detect_main_parcels(base).has_parcel
    assert detect_main_parcels(shifted).has_parcel


def test_multiple_dark_polygons_reported():
    page, _ = make_page_with_parcel()
    cv2.polylines(
        page,
        [np.array([[700, 300], [860, 320], [870, 470], [710, 450]], np.int32)],
        True,
        95,
        3,
    )
    detection = detect_main_parcels(page)
    assert detection.count >= 2


# --------------------------------------------------------------------- config
def test_custom_dark_threshold_changes_result():
    page, _ = make_page_with_parcel(parcel_value=180)
    strict = detect_main_parcels(page, ParcelConfig(dark_threshold=100))
    loose = detect_main_parcels(page, ParcelConfig(dark_threshold=200))
    assert not strict.has_parcel
    assert loose.has_parcel


def test_area_limits_reject_oversized_shape():
    page = np.full((800, 800), 255, np.uint8)
    cv2.rectangle(page, (10, 10), (790, 600), 90, 3)
    detection = detect_main_parcels(page, ParcelConfig(max_area_fraction=0.10))
    assert not detection.has_parcel


def test_default_config_values():
    assert DEFAULT_PARCEL_CONFIG.dark_threshold == 150
    assert DEFAULT_PARCEL_CONFIG.min_dark_fraction == 0.80
    assert DEFAULT_PARCEL_CONFIG.min_area_fraction == 0.005


# ----------------------------------------------------------------------- mask
def test_mask_covers_parcel_outline_only():
    page, corners = make_page_with_parcel()
    detection = detect_main_parcels(page)
    mask = build_parcel_mask(page, detection)

    assert mask.detection.has_parcel
    assert 0.0 < mask.covered_fraction < 0.05

    # outline is masked
    x, y = corners[0]
    assert mask.mask[max(y - 3, 0):y + 4, max(x - 3, 0):x + 4].max() > 0

    # interior centre stays clear so labels survive
    centre_x = int(np.mean(corners[:, 0]))
    centre_y = int(np.mean(corners[:, 1]))
    assert mask.mask[centre_y, centre_x] == 0


def test_mask_apply_paints_outline_with_background():
    page, corners = make_page_with_parcel()
    mask = build_parcel_mask(page)
    cleaned = mask.apply(page)

    x, y = corners[0]
    assert cleaned[y, x] == 255
    # far-away base-map pixel is untouched
    assert np.array_equal(page[1300, 900:905], cleaned[1300, 900:905])


def test_empty_mask_when_no_parcel():
    blank = np.full((400, 300), 255, np.uint8)
    mask = build_parcel_mask(blank)
    assert mask.is_empty
    assert mask.covered_fraction == 0.0
    assert mask.mask.max() == 0


def test_mask_apply_preserves_shape():
    page, _ = make_page_with_parcel()
    mask = build_parcel_mask(page)
    assert mask.apply(page).shape == page.shape


# ------------------------------------------------------------------ reporting
def test_summary_reports_detection():
    page, _ = make_page_with_parcel()
    summary = parcel_summary(detect_main_parcels(page))
    assert "Bidang utama" in summary
    assert "Kandidat" in summary


def test_summary_for_missing_parcel():
    summary = parcel_summary(detect_main_parcels(np.full((300, 300), 255, np.uint8)))
    assert "tidak terdeteksi" in summary.lower()


def test_low_confidence_flagged_for_review():
    candidate = ParcelCandidate(
        polygon=np.zeros((4, 1, 2), np.int32),
        confidence=0.2,
    )
    detection = ParcelDetection(candidates=(candidate,))
    assert detection.needs_review
    assert "perlu pemeriksaan" in detection.label.lower()


def test_detection_label_and_version():
    page, _ = make_page_with_parcel()
    detection = detect_main_parcels(page)
    assert detection.label
    assert PARCEL_VERSION == "parcel-v1"


def test_parcel_mask_dataclass_defaults():
    mask = ParcelMask(mask=np.zeros((10, 10), np.uint8))
    assert mask.is_empty
    assert mask.covered_fraction == 0.0
