import numpy as np
import pytest

from core.map_page_detector import (
    DETECTOR_VERSION,
    MapPageDetector,
    PageFeatures,
)

from tests.test_normalization import make_map_page, make_text_page


def _detector(**kwargs):
    return MapPageDetector(**kwargs)


# ------------------------------------------------------------------- features
def test_features_measured_for_text_page():
    features = _detector().measure(make_text_page())
    assert features.components_per_megapixel > 100
    assert features.text_rows >= 5
    assert features.ink_ratio > 0.0
    assert features.width > 0 and features.height > 0


def test_features_measured_for_map_page():
    features = _detector().measure(make_map_page())
    assert features.components_per_megapixel < 100
    assert features.blank_ratio > 0.8


def test_features_dict_is_json_friendly():
    payload = _detector().measure(make_map_page()).as_dict()
    assert all(isinstance(key, str) for key in payload)
    assert all(isinstance(value, float) for value in payload.values())


def test_text_page_scores_lower_than_map_page():
    detector = _detector()
    text_score = detector.score(detector.measure(make_text_page()))
    map_score = detector.score(detector.measure(make_map_page()))
    assert map_score > text_score
    assert map_score >= detector.threshold > text_score


def test_score_is_bounded():
    detector = _detector()
    for page in (make_text_page(), make_map_page()):
        score = detector.score(detector.measure(page))
        assert 0.0 <= score <= 1.0


# ------------------------------------------------------------------ assessment
def test_assess_page_labels_map():
    assessment = _detector().assess_page(make_map_page(), page_index=1)
    assert assessment.is_map
    assert assessment.page_number == 2
    assert assessment.label == "Peta"
    assert assessment.reasons


def test_assess_page_labels_administration():
    assessment = _detector().assess_page(make_text_page(), page_index=0)
    assert not assessment.is_map
    assert assessment.label == "Bukan peta"


def test_detection_picks_map_page_not_fixed_index():
    detector = _detector()
    result = detector.assess_pages(
        [make_text_page(), make_text_page(seed=3), make_map_page()]
    )
    assert result.map_page == 2
    assert result.map_page_number == 3
    assert result.has_map
    assert result.map_count == 1


def test_detection_handles_map_on_first_page():
    result = _detector().assess_pages([make_map_page(), make_text_page()])
    assert result.map_page == 0
    assert result.map_page_number == 1


def test_detection_handles_no_map_page():
    result = _detector().assess_pages([make_text_page(), make_text_page(seed=5)])
    assert not result.has_map
    assert result.map_page is None
    assert result.map_page_number is None


def test_detection_handles_empty_document():
    result = _detector().assess_pages([])
    assert result.pages == ()
    assert not result.has_map
    assert result.best_page is None


def test_best_page_returns_highest_score():
    detector = _detector()
    result = detector.assess_pages([make_text_page(), make_map_page(), make_text_page(seed=2)])
    assert result.best_page is not None
    assert result.best_page.page_index == 1


def test_assessment_lookup_by_index():
    result = _detector().assess_pages([make_text_page(), make_map_page()])
    assert result.assessment(1).is_map
    assert result.assessment(99) is None


def test_detector_version_exposed():
    result = _detector().assess_pages([make_map_page()])
    assert result.detector_version == DETECTOR_VERSION


# ------------------------------------------------------------------ thresholds
def test_threshold_zero_flags_everything():
    detector = _detector(threshold=0.0)
    result = detector.assess_pages([make_text_page()])
    assert result.pages[0].is_map


def test_threshold_one_flags_nothing():
    detector = _detector(threshold=1.0)
    result = detector.assess_pages([make_map_page()])
    assert not result.pages[0].is_map


def test_custom_weights_change_score():
    default = _detector().score(_detector().measure(make_map_page()))
    text_heavy = _detector(weights={"text_density": 0.9}).score(
        _detector().measure(make_map_page())
    )
    assert text_heavy > default


def test_unknown_weight_rejected():
    with pytest.raises(ValueError):
        MapPageDetector(weights={"tidak_ada": 0.5})


# ------------------------------------------------------------------ robustness
@pytest.mark.parametrize("turns", [0, 1, 2, 3])
def test_map_detection_survives_quarter_turns(turns):
    page = np.ascontiguousarray(np.rot90(make_map_page(), turns))
    assert _detector().assess_page(page).is_map


def test_map_detection_survives_low_resolution():
    page = make_map_page()
    small = page[::2, ::2]
    assert _detector().assess_page(small).is_map


def test_features_dataclass_defaults():
    features = PageFeatures()
    assert features.megapixels == 0.0
    assert features.text_rows == 0
