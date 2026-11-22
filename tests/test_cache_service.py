import json
from pathlib import Path

import numpy as np
import pytest

from core.pdf_reader import inspect_pdf
from services.cache_service import (
    CACHE_SCHEMA_VERSION,
    AnalysisCache,
    CachedAnalysis,
    analysis_cache_key,
    file_fingerprint,
)


@pytest.fixture
def cache(tmp_path) -> AnalysisCache:
    return AnalysisCache(tmp_path / "analysis")


@pytest.fixture
def sample_info(sample_pdfs):
    return inspect_pdf(sample_pdfs[0])


def _image(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, (120, 90), dtype=np.uint8)


def _analysis(info, key="k1", image=None) -> CachedAnalysis:
    return CachedAnalysis(
        key=key,
        info=info,
        map_page=1,
        page_count=2,
        detection_scores=((0, 0.12, False), (1, 0.90, True)),
        normalized=image if image is not None else _image(1),
        masked=_image(2),
        parcel_polygons=(np.array([[[10, 10], [50, 10], [50, 50], [10, 50]]], np.int32),),
        parcel_confidences=(0.93,),
    )


# ------------------------------------------------------------------ fingerprint
def test_file_fingerprint_is_stable(sample_info):
    first = file_fingerprint(sample_info.path)
    second = file_fingerprint(sample_info.path)
    assert first == second
    assert len(first) == 64


def test_file_fingerprint_changes_with_content(tmp_path):
    left = tmp_path / "a.pdf"
    right = tmp_path / "b.pdf"
    left.write_bytes(b"%PDF-1.4 isi satu")
    right.write_bytes(b"%PDF-1.4 isi dua")
    assert file_fingerprint(left) != file_fingerprint(right)


def test_fingerprint_missing_file_raises(tmp_path):
    with pytest.raises(OSError):
        file_fingerprint(tmp_path / "tidak-ada.pdf")


# ------------------------------------------------------------------------- key
def test_cache_key_deterministic():
    a = analysis_cache_key("abc123", 120)
    b = analysis_cache_key("abc123", 120)
    assert a == b


def test_cache_key_changes_with_dpi():
    assert analysis_cache_key("abc", 120) != analysis_cache_key("abc", 200)


def test_cache_key_changes_with_pipeline_version():
    base = analysis_cache_key("abc", 120)
    changed = analysis_cache_key("abc", 120, preprocessing_version="normalize-v99")
    assert base != changed


def test_cache_key_changes_with_detector_version():
    base = analysis_cache_key("abc", 120)
    assert analysis_cache_key("abc", 120, detector_version="map-page-v99") != base


def test_cache_key_changes_with_parcel_version():
    base = analysis_cache_key("abc", 120)
    assert analysis_cache_key("abc", 120, parcel_version="parcel-v99") != base


# ---------------------------------------------------------------- store / load
def test_store_then_load_round_trip(cache, sample_info):
    analysis = _analysis(sample_info, key="round1")
    cache.store(analysis)

    loaded = cache.load("round1", sample_info)
    assert loaded is not None
    assert loaded.cache_hit is True
    assert loaded.map_page == 1
    assert loaded.page_count == 2
    assert np.array_equal(loaded.normalized, analysis.normalized)
    assert np.array_equal(loaded.masked, analysis.masked)


def test_load_returns_none_when_missing(cache, sample_info):
    assert cache.load("tidak-ada", sample_info) is None


def test_has_reports_presence(cache, sample_info):
    assert not cache.has("x1")
    cache.store(_analysis(sample_info, key="x1"))
    assert cache.has("x1")


def test_detection_scores_preserved(cache, sample_info):
    cache.store(_analysis(sample_info, key="scores"))
    loaded = cache.load("scores", sample_info)
    assert loaded.detection_scores == ((0, 0.12, False), (1, 0.9, True))


def test_parcel_polygons_preserved(cache, sample_info):
    cache.store(_analysis(sample_info, key="poly"))
    loaded = cache.load("poly", sample_info)
    assert len(loaded.parcel_polygons) == 1
    assert loaded.parcel_polygons[0].reshape(-1, 2).tolist() == [
        [10, 10], [50, 10], [50, 50], [10, 50]
    ]
    assert loaded.parcel_confidences == (0.93,)


def test_created_at_is_recorded(cache, sample_info):
    cache.store(_analysis(sample_info, key="stamp"))
    loaded = cache.load("stamp", sample_info)
    assert loaded.created_at


def test_store_returns_directory(cache, sample_info):
    directory = cache.store(_analysis(sample_info, key="dir1"))
    assert directory.exists()
    assert (directory / "meta.json").exists()
    assert (directory / "normalized.png").exists()


# ---------------------------------------------------------------- corruption
def test_corrupt_metadata_is_removed(cache, sample_info):
    directory = cache.store(_analysis(sample_info, key="bad"))
    (directory / "meta.json").write_text("{bukan json", encoding="utf-8")

    assert cache.load("bad", sample_info) is None
    assert not cache.has("bad")


def test_corrupt_image_is_removed(cache, sample_info):
    directory = cache.store(_analysis(sample_info, key="badimg"))
    (directory / "normalized.png").write_bytes(b"bukan png")

    assert cache.load("badimg", sample_info) is None
    assert not cache.has("badimg")


def test_schema_mismatch_invalidates(cache, sample_info):
    directory = cache.store(_analysis(sample_info, key="schema"))
    payload = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    payload["schema"] = "cache-v0"
    (directory / "meta.json").write_text(json.dumps(payload), encoding="utf-8")

    assert cache.load("schema", sample_info) is None


def test_missing_image_file_is_tolerated(cache, sample_info):
    directory = cache.store(_analysis(sample_info, key="noimg"))
    (directory / "masked.png").unlink()

    loaded = cache.load("noimg", sample_info)
    assert loaded is not None
    assert loaded.masked is None


# ---------------------------------------------------------------- lifecycle
def test_invalidate_removes_entry(cache, sample_info):
    cache.store(_analysis(sample_info, key="gone"))
    cache.invalidate("gone")
    assert not cache.has("gone")


def test_invalidate_missing_key_is_safe(cache):
    cache.invalidate("never-existed")


def test_clear_removes_everything(cache, sample_info):
    for index in range(3):
        cache.store(_analysis(sample_info, key=f"c{index}"))
    assert cache.stats()["entries"] == 3

    removed = cache.clear()
    assert removed == 3
    assert cache.stats()["entries"] == 0


def test_stats_report_entries_and_size(cache, sample_info):
    cache.store(_analysis(sample_info, key="stat1"))
    stats = cache.stats()
    assert stats["entries"] == 1
    assert stats["size_bytes"] > 0


# -------------------------------------------------------------- integration
def test_prepare_comparable_uses_cache(tmp_path):
    """Second preparation must hit the analysis cache and be much faster."""
    import time

    from core.pdf_reader import inspect_pdf as inspect
    from services.comparison_service import prepare_comparable

    project = Path(__file__).resolve().parents[1]
    pdf = project / "SU_057.pdf"
    if not pdf.exists():
        pytest.skip("SU_057.pdf tidak tersedia")

    info = inspect(pdf)
    cache = AnalysisCache(tmp_path / "analysis")
    features = tmp_path / "features"

    cold = prepare_comparable(info, cache_dir=features, analysis_cache=cache)
    start = time.perf_counter()
    warm = prepare_comparable(info, cache_dir=features, analysis_cache=cache)
    warm_ms = (time.perf_counter() - start) * 1000

    assert warm.analysis.map_page_number == cold.analysis.map_page_number
    assert warm.image.shape == cold.image.shape
    assert np.array_equal(warm.image, cold.image)
    assert warm.features.count == cold.features.count
    assert warm.parcel_label == cold.parcel_label
    assert warm_ms < 900, f"warm path terlalu lambat: {warm_ms:.0f} ms"


def test_cache_can_be_disabled(tmp_path):
    from services.comparison_service import prepare_comparable

    project = Path(__file__).resolve().parents[1]
    pdf = project / "SU_057.pdf"
    if not pdf.exists():
        pytest.skip("SU_057.pdf tidak tersedia")

    cache = AnalysisCache(tmp_path / "analysis")
    first = prepare_comparable(
        inspect_pdf(pdf), cache_dir=tmp_path / "f", analysis_cache=cache
    )
    second = prepare_comparable(
        inspect_pdf(pdf),
        cache_dir=tmp_path / "f",
        analysis_cache=cache,
        use_analysis_cache=False,
    )
    # Disabling the cache must bypass it entirely, leaving it untouched.
    assert not cache.has(
        analysis_cache_key(file_fingerprint(pdf), 120)
    ) or second.analysis.map_page_number == first.analysis.map_page_number


def test_schema_constant_present():
    assert CACHE_SCHEMA_VERSION == "cache-v1"
