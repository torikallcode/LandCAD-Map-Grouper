from pathlib import Path

import numpy as np
import pytest

from core.feature_extractor import extract_features
from core.image_alignment import TransformModel
from core.pdf_reader import inspect_pdf
from services.comparison_service import (
    ComparableMap,
    ComparisonResult,
    compare_maps,
    comparison_summary,
    ink_coverage,
    load_or_extract,
    prepare_comparable,
)


@pytest.fixture
def pair(sample_pdfs):
    return [inspect_pdf(path) for path in sample_pdfs]


@pytest.fixture
def zero_cache(tmp_path):
    return tmp_path / "features"


# --------------------------------------------------------------- preparation
def test_prepare_comparable_extracts_map_and_features(pair, zero_cache):
    comparable = prepare_comparable(pair[0], cache_dir=zero_cache)

    assert isinstance(comparable, ComparableMap)
    assert comparable.map_page_number == 2
    assert comparable.features.count > 0
    assert comparable.image.ndim == 2
    assert comparable.filename == pair[0].filename


def test_prepare_uses_cache_on_second_call(pair, zero_cache):
    first = prepare_comparable(pair[0], cache_dir=zero_cache)
    second = prepare_comparable(pair[0], cache_dir=zero_cache)

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.features.count == first.features.count


def test_prepare_without_cache_dir_never_hits_cache(pair):
    first = prepare_comparable(pair[0], cache_dir=None)
    second = prepare_comparable(pair[0], cache_dir=None)
    assert first.cache_hit is False
    assert second.cache_hit is False


def test_corrupt_cache_file_is_replaced(pair, zero_cache):
    prepare_comparable(pair[0], cache_dir=zero_cache)
    cached = list(Path(zero_cache).glob("*.npz"))
    assert cached
    # Two caches exist now (original + parcel-masked image); corrupt them all so
    # the next prepare() must genuinely recompute.
    for path in cached:
        path.write_bytes(b"bukan npz")

    comparable = prepare_comparable(pair[0], cache_dir=zero_cache)
    assert comparable.cache_hit is False
    assert comparable.features.count > 0


def test_prepare_rejects_document_without_map(tmp_path):
    broken = tmp_path / "rusak.pdf"
    broken.write_bytes(b"bukan pdf")
    info = inspect_pdf(broken)

    with pytest.raises(ValueError):
        prepare_comparable(info)


def test_load_or_extract_reports_cache_miss_then_hit(tmp_path):
    from tests.test_normalization import make_map_page

    image = make_map_page()
    first, hit_first = load_or_extract(image, tmp_path)
    second, hit_second = load_or_extract(image, tmp_path)

    assert hit_first is False
    assert hit_second is True
    assert first.count == second.count
    assert extract_features(image).count == first.count


# ---------------------------------------------------------------- comparison
def test_compare_reference_documents_align(pair, zero_cache):
    maps = [prepare_comparable(info, cache_dir=zero_cache) for info in pair]
    result = compare_maps(maps[0], maps[1])

    assert isinstance(result, ComparisonResult)
    assert not result.has_error
    assert result.is_aligned
    assert result.alignment.inlier_count > 100
    assert result.alignment.quality > 0.6
    assert result.alignment.model in (TransformModel.AFFINE, TransformModel.HOMOGRAPHY)


def test_compare_reference_documents_have_high_ink_coverage(pair, zero_cache):
    """The two reference maps share a base map, so plenty of ink must coincide."""
    maps = [prepare_comparable(info, cache_dir=zero_cache) for info in pair]
    result = compare_maps(maps[0], maps[1])

    assert result.warped is not None
    assert result.ink_coverage > 0.35


def test_warped_shape_matches_reference(pair, zero_cache):
    maps = [prepare_comparable(info, cache_dir=zero_cache) for info in pair]
    result = compare_maps(maps[0], maps[1])

    assert result.warped.shape == maps[0].image.shape


def test_comparison_is_symmetric_in_quality(pair, zero_cache):
    maps = [prepare_comparable(info, cache_dir=zero_cache) for info in pair]
    forward = compare_maps(maps[0], maps[1])
    backward = compare_maps(maps[1], maps[0])

    assert abs(forward.alignment.quality - backward.alignment.quality) < 0.15


def test_comparison_between_map_and_admin_page_is_weak(pair, zero_cache):
    """A map page versus an administrative page must not align well."""
    from core.image_normalizer import normalize_page
    from core.map_page_detector import MapPageDetector
    from services.processing_service import analyse_document, render_pages

    map_map = prepare_comparable(pair[0], cache_dir=zero_cache)

    info = pair[0]
    detection = analyse_document(info, MapPageDetector())
    assert detection is not None
    pages = render_pages(info.path, dpi=120)
    admin_image = normalize_page(pages[0]).image
    admin = ComparableMap(
        info=info,
        analysis=detection,
        image=admin_image,
        features=extract_features(admin_image),
    )

    result = compare_maps(map_map, admin)

    assert result.alignment.inlier_count < 100
    assert result.alignment.quality < map_map.features.count


def test_compare_with_empty_features_reports_error(pair, zero_cache):
    maps = [prepare_comparable(info, cache_dir=zero_cache) for info in pair]
    blank_features = extract_features(np.full((200, 200), 255, np.uint8))
    blank = ComparableMap(
        info=maps[0].info,
        analysis=maps[0].analysis,
        image=np.full(maps[0].image.shape, 255, np.uint8),
        features=blank_features,
    )

    result = compare_maps(maps[0], blank)
    assert result.has_error
    assert not result.is_aligned
    assert result.warped is None


def test_compare_can_skip_image_rendering(pair, zero_cache):
    maps = [prepare_comparable(info, cache_dir=zero_cache) for info in pair]
    result = compare_maps(maps[0], maps[1], render_images=False)

    assert result.overlay_png == b""
    assert result.side_by_side_png == b""
    assert result.is_aligned


def test_result_provides_decodable_pngs(pair, zero_cache):
    import cv2

    maps = [prepare_comparable(info, cache_dir=zero_cache) for info in pair]
    result = compare_maps(maps[0], maps[1])

    assert result.overlay_png.startswith(b"\x89PNG")
    assert result.side_by_side_png.startswith(b"\x89PNG")

    overlay = cv2.imdecode(np.frombuffer(result.overlay_png, np.uint8), cv2.IMREAD_COLOR)
    side = cv2.imdecode(
        np.frombuffer(result.side_by_side_png, np.uint8), cv2.IMREAD_GRAYSCALE
    )
    assert overlay.ndim == 3
    assert side.shape[1] > maps[0].image.shape[1]


def test_result_labels(pair, zero_cache):
    maps = [prepare_comparable(info, cache_dir=zero_cache) for info in pair]
    result = compare_maps(maps[0], maps[1])

    assert "↔" in result.label
    assert result.category in ("Tinggi", "Sedang", "Rendah")
    assert result.quality_label in ("Tinggi", "Sedang", "Rendah", "Gagal")


# -------------------------------------------------------------- ink coverage
def test_ink_coverage_identical_images_is_one():
    from tests.test_normalization import make_map_page

    page = make_map_page()
    assert ink_coverage(page, page) == pytest.approx(1.0, abs=0.01)


def test_ink_coverage_blank_image_is_zero():
    from tests.test_normalization import make_map_page

    page = make_map_page()
    blank = np.full(page.shape, 255, np.uint8)
    assert ink_coverage(page, blank) == 0.0


def test_ink_coverage_handles_size_mismatch():
    from tests.test_normalization import make_map_page

    page = make_map_page()
    smaller = page[::2, ::2]
    coverage = ink_coverage(page, smaller)
    assert 0.0 <= coverage <= 1.0


# -------------------------------------------------------------------- summary
def test_summary_lists_key_metrics(pair, zero_cache):
    maps = [prepare_comparable(info, cache_dir=zero_cache) for info in pair]
    summary = comparison_summary(compare_maps(maps[0], maps[1]))

    assert "Model transformasi:" in summary
    assert "Kecocokan fitur:" in summary
    assert "Inlier:" in summary
    assert "Kualitas alignment:" in summary
    assert "Tumpang tindih tinta" in summary


def test_summary_for_failed_comparison(pair, zero_cache):
    maps = [prepare_comparable(info, cache_dir=zero_cache) for info in pair]
    blank_features = extract_features(np.full((200, 200), 255, np.uint8))
    blank = ComparableMap(
        info=maps[0].info,
        analysis=maps[0].analysis,
        image=np.full((200, 200), 255, np.uint8),
        features=blank_features,
    )
    summary = comparison_summary(compare_maps(maps[0], blank))
    assert "Fitur ORB" in summary or "gagal" in summary.lower()
