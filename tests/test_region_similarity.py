"""Region diagnostics: the page furniture must not count as map evidence."""

import numpy as np
import pytest

from core.diagnose_pair import (
    DIAGNOSTIC_VERSION,
    diagnose_pair,
    isolate_template,
    region_split,
    score_by_region,
)
from core.feature_extractor import extract_features
from core.similarity_engine import score_pair


def _page_with_frame(body_seed: int = 1, size=(400, 300)) -> np.ndarray:
    """A page with an identical frame and a body that varies by seed."""
    height, width = size
    rng = np.random.default_rng(body_seed)
    page = np.full((height, width), 255, np.uint8)
    # page furniture: outer frame + legend line at the bottom
    page[6:10, 6:width - 6] = 90
    page[height - 11:height - 7, 6:width - 6] = 90
    page[6:height - 6, 6:10] = 90
    page[6:height - 6, width - 10:width - 6] = 90
    page[height - 40:height - 36, 20:120] = 60
    # body: random strokes
    for _ in range(18):
        x1, y1 = int(rng.integers(30, width - 60)), int(rng.integers(40, height - 80))
        x2, y2 = x1 + int(rng.integers(20, 90)), y1 + int(rng.integers(20, 70))
        page[y1:y2, x1:x1 + 2] = 0
        page[y1:y1 + 2, x1:x2] = 0
    return page


# ------------------------------------------------------------------- split
def test_region_split_covers_whole_page():
    page = _page_with_frame()
    split = region_split(page, margin_fraction=0.10)
    total = split.border.astype(int) + split.interior.astype(int)
    assert np.all(total == 1), "setiap piksel harus masuk tepat satu region"


def test_region_split_proportions_match_margin():
    page = np.zeros((400, 300), np.uint8)
    split = region_split(page, margin_fraction=0.10)
    # border area = 1 - (0.8 * 0.8) = 36% for a 10% margin on both axes
    assert split.border_fraction == pytest.approx(0.36, abs=0.02)


def test_region_split_rejects_zero_margin():
    page = np.zeros((100, 100), np.uint8)
    split = region_split(page, margin_fraction=0.0)
    # With a zero margin the border band collapses to a single row/column, so the
    # interior covers almost the whole page.
    assert split.interior_fraction > 0.95


# ---------------------------------------------------------------- template
def test_isolate_template_keeps_frame_and_blanks_body():
    page = _page_with_frame()
    furniture = isolate_template(page, margin_fraction=0.10)

    height, width = page.shape
    # frame keeps ink
    assert (furniture[8, 10:width - 10] < 255).any()
    # body centre is blanked
    assert furniture[height // 2, width // 2] == 255


# ------------------------------------------------------- the key measurement
def test_different_maps_sharing_a_frame_have_no_interior_evidence():
    """The exact failure mode: same furniture, different map body.

    Uses the real reference map pages rather than tiny synthetic ones, because
    ORB needs enough structure to align the frame in the first place.
    """
    from pathlib import Path

    import cv2

    from core.image_normalizer import normalize_page
    from core.map_page_detector import MapPageDetector
    from services.processing_service import render_pages

    project = Path(__file__).resolve().parents[1]
    sample = project / "SU_057.pdf"
    if not sample.exists():
        pytest.skip("SU_057.pdf tidak tersedia")

    rendered = render_pages(sample, dpi=120)
    detection = MapPageDetector().assess_pages(rendered)
    page = normalize_page(rendered[detection.map_page]).image

    # Same furniture (border band of the real page), completely different body.
    furniture = isolate_template(page, margin_fraction=0.12)
    in_body = furniture == 255
    rng = np.random.default_rng(4)
    body = np.full(page.shape, 255, np.uint8)
    height, width = body.shape
    for _ in range(40):
        x1 = int(rng.integers(100, width - 100))
        y1 = int(rng.integers(100, height - 100))
        length = int(rng.integers(100, 350))
        angle = float(rng.choice([0, 90, 30, -30]))
        x2 = int(x1 + length * np.cos(np.radians(angle)))
        y2 = int(y1 + length * np.sin(np.radians(angle)))
        body = cv2.line(body, (x1, y1), (x2, y2), 210, 1)

    candidate = page.copy()
    candidate[in_body] = body[in_body]

    result = score_pair(page, candidate, extract_features(page),
                        extract_features(candidate))
    indicators = result.score.indicators

    # Furniture matches far better than the map body, and the pair is rejected.
    assert indicators.border_coverage > 0.8
    assert indicators.interior_coverage < 0.3
    assert result.score.score == 0.0


def test_identical_page_scores_high_on_both_regions():
    page = _page_with_frame(body_seed=7)
    result = score_pair(page, page, extract_features(page), extract_features(page))

    indicators = result.score.indicators
    assert indicators.interior_coverage > 0.9
    assert result.score.score > 90


def test_interior_coverage_is_insensitive_to_rotation():
    """Whole-page coverage drops on rotation; interior coverage must not."""
    from core.image_alignment import align_features

    page = _page_with_frame(body_seed=3)
    import cv2

    matrix = cv2.getRotationMatrix2D((page.shape[1] / 2, page.shape[0] / 2), 6, 1.0)
    rotated = cv2.warpAffine(page, matrix, (page.shape[1], page.shape[0]),
                             borderValue=255)

    alignment = align_features(extract_features(rotated), extract_features(page))
    assert alignment.is_aligned

    regions = score_by_region(page, rotated, alignment)
    assert regions.interior > 0.6
    assert regions.interior >= regions.overall - 5.0


def test_score_by_region_requires_alignment():
    page = _page_with_frame()
    with pytest.raises(Exception):
        score_by_region(page, page, None)


def test_diagnose_pair_reports_unalgined_pairs():
    blank = np.full((200, 160), 255, np.uint8)
    payload = diagnose_pair(blank, blank, extract_features(blank), extract_features(blank))
    assert payload["aligned"] == 0.0


def test_version_constant():
    assert DIAGNOSTIC_VERSION == "diagnose-v1"
