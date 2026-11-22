import cv2
import numpy as np
import pytest

from core.parcel_detector import build_parcel_mask, detect_main_parcels
from services.comparison_service import (
    ComparableMap,
    compare_maps,
    comparison_summary,
    prepare_comparable,
)


@pytest.fixture
def pair(sample_pdfs):
    from core.pdf_reader import inspect_pdf

    return [inspect_pdf(path) for path in sample_pdfs]


@pytest.fixture
def maps(pair, tmp_path):
    return [prepare_comparable(info, cache_dir=tmp_path) for info in pair]


# ------------------------------------------------------------- preparation
def test_prepare_detects_parcel_and_builds_mask(maps):
    for comparable in maps:
        assert comparable.parcel is not None
        assert comparable.parcel.has_parcel
        assert comparable.parcel_mask is not None
        assert comparable.parcel_mask.covered_fraction > 0
        assert comparable.has_parcel


def test_prepare_extracts_features_from_masked_image(maps):
    for comparable in maps:
        assert comparable.masked_image is not None
        assert comparable.masked_features is not None
        assert comparable.masked_features.count > 0
        assert not comparable.masked_features.is_empty


def test_masked_image_differs_from_original(maps):
    comparable = maps[0]
    assert not np.array_equal(comparable.image, comparable.masked_image)


def test_parcel_label_is_reported(maps):
    assert "terdeteksi" in maps[0].parcel_label


# --------------------------------------------------------------- comparison
def test_compare_reports_parcel_masking_by_default(maps):
    result = compare_maps(maps[0], maps[1])

    assert result.is_aligned
    assert result.parcel_masking
    assert result.images_parcel_free
    assert result.ink_coverage_unmasked > 0.0
    assert result.parcel_note


def test_masking_can_be_disabled(maps):
    result = compare_maps(maps[0], maps[1], ignore_parcel=False)

    assert result.is_aligned
    assert not result.parcel_masking
    assert result.coverage_gain == 0.0
    assert "dimatikan" in result.parcel_note.lower()


def test_coverage_values_are_bounded(maps):
    result = compare_maps(maps[0], maps[1])
    assert 0.0 <= result.ink_coverage <= 1.0
    assert 0.0 <= result.ink_coverage_unmasked <= 1.0


def test_masking_produces_both_overlays(maps):
    result = compare_maps(maps[0], maps[1])
    assert result.overlay_png.startswith(b"\x89PNG")
    assert result.overlay_unmasked_png.startswith(b"\x89PNG")
    assert result.overlay_png != result.overlay_unmasked_png


def test_parcel_mask_image_is_produced(maps):
    result = compare_maps(maps[0], maps[1])
    assert result.parcel_mask_png.startswith(b"\x89PNG")


def test_summary_mentions_parcel(maps):
    summary = comparison_summary(compare_maps(maps[0], maps[1]))
    assert "Bidang utama A:" in summary
    assert "Bidang utama B:" in summary
    assert "Catatan bidang:" in summary


def test_comparison_without_parcel_reports_note(tmp_path):
    """Two maps without a detected parcel must still compare, with a note."""
    from core.feature_extractor import extract_features

    rng = np.random.default_rng(3)
    base = np.full((900, 700), 255, np.uint8)
    for _ in range(25):
        x1 = int(rng.integers(60, 640))
        y1 = int(rng.integers(60, 840))
        cv2.line(base, (x1, y1), (x1 + 120, y1 + 40), 220, 1)

    def comparable(name):
        from core.pdf_reader import DocumentStatus, PdfInfo

        info = PdfInfo(
            path=__import__("pathlib").Path(name),
            filename=name,
            size_bytes=1,
            modified_at=0.0,
            page_count=1,
            status=DocumentStatus.OK,
        )
        return ComparableMap(
            info=info,
            analysis=type(
                "A", (), {"map_page_number": 1, "map_label": "Halaman 1"}
            )(),
            image=base,
            features=extract_features(base),
            parcel=detect_main_parcels(base),
        )

    result = compare_maps(comparable("a.pdf"), comparable("b.pdf"))
    assert "tidak terdeteksi" in result.parcel_note.lower() or not result.parcel_masking


# ------------------------------------------------- decisive behaviour check
def test_masking_improves_scores_when_parcels_are_far_apart():
    """The whole point of Phase 4: same base map, parcels in different places.

    Removing both parcel outlines must push ink coverage up, because the
    unmatched parcel ink is no longer counted against the pair.
    """
    from core.feature_extractor import extract_features
    from core.pdf_reader import DocumentStatus, PdfInfo

    rng = np.random.default_rng(11)
    height, width = 900, 800
    base = np.full((height, width), 255, np.uint8)
    for _ in range(35):
        x1 = int(rng.integers(80, width - 80))
        y1 = int(rng.integers(80, height - 80))
        length = int(rng.integers(80, 220))
        angle = float(rng.choice([0, 90, 30, -30]))
        cv2.line(
            base,
            (x1, y1),
            (int(x1 + length * np.cos(np.radians(angle))), int(y1 + length * np.sin(np.radians(angle)))),
            215,
            1,
        )

    shape = np.array([[300, 400], [430, 420], [450, 540], [310, 510]], np.int32)
    doc1 = base.copy()
    cv2.polylines(doc1, [shape], True, 95, 3)
    doc2 = base.copy()
    cv2.polylines(doc2, [shape + np.array([150, 120])], True, 95, 3)

    def make(image, name):
        info = PdfInfo(
            path=__import__("pathlib").Path(name),
            filename=name,
            size_bytes=1,
            modified_at=0.0,
            page_count=1,
            status=DocumentStatus.OK,
        )
        detection = detect_main_parcels(image)
        mask = build_parcel_mask(image, detection)
        masked = mask.apply(image) if not mask.is_empty else None
        return ComparableMap(
            info=info,
            analysis=type("A", (), {"map_page_number": 1, "map_label": "Halaman 1"})(),
            image=image,
            features=extract_features(image),
            parcel=detection,
            parcel_mask=mask,
            masked_image=masked,
            masked_features=extract_features(masked) if masked is not None else None,
        )

    left, right = make(doc1, "a.pdf"), make(doc2, "b.pdf")
    assert left.has_parcel and right.has_parcel

    masked_result = compare_maps(left, right, ignore_parcel=True)
    unmasked_result = compare_maps(left, right, ignore_parcel=False)

    assert masked_result.is_aligned and unmasked_result.is_aligned
    assert masked_result.ink_coverage > unmasked_result.ink_coverage
    assert masked_result.coverage_gain > 0
