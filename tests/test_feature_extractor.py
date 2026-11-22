import numpy as np

from core.feature_extractor import (
    DEFAULT_FEATURE_CONFIG,
    FEATURE_VERSION,
    FeatureConfig,
    OrbFeatures,
    extract_features,
    features_from_cache,
    features_to_cache,
    preprocess_for_features,
)

from tests.test_normalization import make_map_page, make_text_page


# ------------------------------------------------------------- preprocessing
def test_preprocess_returns_grayscale_same_shape():
    page = make_map_page()
    gray = preprocess_for_features(page)
    assert gray.ndim == 2
    assert gray.shape == page.shape


def test_preprocess_accepts_bgr():
    page = make_map_page()
    bgr = np.dstack([page, page, page])
    assert preprocess_for_features(bgr).shape == page.shape


def test_preprocess_does_not_mutate_input():
    page = make_map_page()
    snapshot = page.copy()
    preprocess_for_features(page)
    assert np.array_equal(page, snapshot)


def test_blur_kernel_can_be_disabled():
    page = make_map_page()
    sharp = preprocess_for_features(page, FeatureConfig(blur_kernel=1))
    blurred = preprocess_for_features(page, FeatureConfig(blur_kernel=5))
    assert not np.array_equal(sharp, blurred)


# ---------------------------------------------------------------- extraction
def test_extract_features_returns_keypoints_and_descriptors():
    features = extract_features(make_map_page())
    assert isinstance(features, OrbFeatures)
    assert features.count > 0
    assert features.descriptors.shape[1] == 32
    assert features.descriptors.dtype == np.uint8
    assert len(features.keypoints) == features.count


def test_extract_features_respects_feature_cap():
    """``nfeatures`` is a per-level target, so allow a small overshoot."""
    features = extract_features(make_map_page(), FeatureConfig(n_features=50))
    assert features.count <= 60


def test_smaller_cap_yields_fewer_features():
    page = make_map_page()
    small = extract_features(page, FeatureConfig(n_features=50))
    large = extract_features(page, FeatureConfig(n_features=2000))
    assert small.count < large.count


def test_extract_features_dense_text_page_has_many_points():
    features = extract_features(make_text_page())
    assert features.count >= 100


def test_blank_image_yields_no_features():
    blank = np.full((400, 300), 255, np.uint8)
    features = extract_features(blank)
    assert features.is_empty
    assert features.descriptors is None
    assert features.count == 0


def test_points_array_matches_keypoints():
    features = extract_features(make_map_page())
    points = features.points()
    assert points.shape == (features.count, 2)
    assert points.dtype == np.float32


def test_feature_version_recorded():
    features = extract_features(make_map_page())
    assert features.feature_version == FEATURE_VERSION


def test_image_shape_recorded():
    page = make_map_page(width=640, height=480)
    features = extract_features(page)
    assert features.image_shape == (480, 640)


def test_features_are_deterministic():
    page = make_map_page()
    first = extract_features(page)
    second = extract_features(page)
    assert first.count == second.count
    assert np.array_equal(first.descriptors, second.descriptors)


# --------------------------------------------------------------------- cache
def test_cache_round_trip_preserves_data(tmp_path):
    features = extract_features(make_map_page())
    path = features_to_cache(features, tmp_path / "cache.npz")
    assert path.exists()

    restored = features_from_cache(path)
    assert restored.count == features.count
    assert np.array_equal(restored.descriptors, features.descriptors)
    assert restored.image_shape == features.image_shape
    assert restored.feature_version == FEATURE_VERSION


def test_cache_creates_parent_directories(tmp_path):
    features = extract_features(make_map_page())
    path = features_to_cache(features, tmp_path / "a" / "b" / "f.npz")
    assert path.exists()


def test_cache_round_trip_for_empty_features(tmp_path):
    blank = np.full((200, 200), 255, np.uint8)
    features = extract_features(blank)
    path = features_to_cache(features, tmp_path / "empty.npz")
    restored = features_from_cache(path)
    assert restored.is_empty


def test_default_config_is_used():
    assert DEFAULT_FEATURE_CONFIG.n_features == 2000
    assert DEFAULT_FEATURE_CONFIG.edge_threshold == 15
    assert DEFAULT_FEATURE_CONFIG.fast_threshold == 10
