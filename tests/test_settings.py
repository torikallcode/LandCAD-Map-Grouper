"""Settings storage and the settings tab (PRD section 9)."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.settings_store import (  # noqa: E402
    DEFAULT_SETTINGS,
    AppSettings,
    load_settings,
    reset_settings,
    save_settings,
)


@pytest.fixture(scope="module")
def qapp():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def clean_settings(qapp, tmp_path):
    """Redirect QSettings to a throwaway INI file per test."""
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path)
    )
    yield
    # Wipe anything a test wrote so the next one starts from defaults.
    QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, "LandCAD",
              "LandCAD Map Grouper").clear()


# ------------------------------------------------------------------ defaults
def test_defaults_match_prd_bands():
    # 82 to join (measured), 85 = PRD "high similarity" band, 65 = PRD review floor.
    assert DEFAULT_SETTINGS.link_threshold == 82.0
    assert DEFAULT_SETTINGS.high_confidence_threshold == 85.0
    assert DEFAULT_SETTINGS.prefilter_enabled is True


def test_load_returns_defaults_when_nothing_saved():
    assert load_settings() == DEFAULT_SETTINGS


def test_save_then_load_round_trip():
    settings = AppSettings(
        link_threshold=70.0,
        high_confidence_threshold=88.0,
        map_page_threshold=0.5,
        prefilter_enabled=False,
    )
    save_settings(settings)
    assert load_settings() == settings


def test_save_persists_across_calls():
    save_settings(AppSettings(link_threshold=72.0))
    assert load_settings().link_threshold == 72.0


def test_reset_restores_defaults():
    save_settings(AppSettings(link_threshold=90.0))
    reset_settings()
    assert load_settings() == DEFAULT_SETTINGS


# ---------------------------------------------------------------- validation
def test_values_are_clamped():
    validated = AppSettings(
        link_threshold=10.0, high_confidence_threshold=200.0, map_page_threshold=5.0
    ).validated()
    assert validated.link_threshold == 60.0
    assert validated.high_confidence_threshold == 100.0
    assert validated.map_page_threshold == 0.80


def test_high_threshold_is_pushed_above_link_threshold():
    validated = AppSettings(
        link_threshold=80.0, high_confidence_threshold=70.0
    ).validated()
    assert validated.high_confidence_threshold > validated.link_threshold


def test_consistency_flag():
    assert AppSettings(link_threshold=65.0, high_confidence_threshold=85.0).is_consistent
    assert not AppSettings(
        link_threshold=90.0, high_confidence_threshold=80.0
    ).is_consistent


def test_saved_values_are_always_validated():
    stored = save_settings(AppSettings(link_threshold=1.0))
    assert stored.link_threshold == 60.0
    assert load_settings().link_threshold == 60.0


# -------------------------------------------------------------------- view
@pytest.fixture
def settings_view(qapp):
    from app.settings_view import SettingsView

    widget = SettingsView(AppSettings())
    yield widget
    widget.deleteLater()


def test_view_shows_current_settings(settings_view):
    assert settings_view.current_settings().link_threshold == 82.0
    assert settings_view.current_settings().high_confidence_threshold == 85.0


def test_view_save_emits_signal(settings_view):
    captured = []
    settings_view.settings_changed.connect(captured.append)

    settings_view._link_spin.setValue(70.0)
    settings_view._high_spin.setValue(90.0)
    settings_view._on_save()

    assert len(captured) == 1
    assert captured[0].link_threshold == 70.0
    assert load_settings().link_threshold == 70.0


def test_view_rejects_inconsistent_values(settings_view, monkeypatch):
    warnings = []
    monkeypatch.setattr(
        "app.settings_view.QMessageBox.warning",
        lambda *args, **kwargs: warnings.append(args),
    )
    emitted = []
    settings_view.settings_changed.connect(emitted.append)

    before = load_settings()
    settings_view._link_spin.setValue(90.0)
    settings_view._high_spin.setValue(80.0)
    settings_view._on_save()

    assert warnings
    assert not emitted
    # Nothing was written: the stored values are unchanged.
    assert load_settings() == before
    assert load_settings().link_threshold != 90.0


def test_view_reset_restores_defaults(settings_view):
    settings_view._link_spin.setValue(80.0)
    settings_view._on_reset()
    assert settings_view.current_settings() == DEFAULT_SETTINGS


def test_view_clear_cache_requires_confirmation(settings_view, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    asked = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: (
            asked.append(args) or QMessageBox.StandardButton.No
        ),
    )
    emitted = []
    settings_view.clear_cache_requested.connect(lambda: emitted.append(True))

    settings_view._on_clear_cache()

    assert asked
    assert not emitted


def test_view_clear_cache_emits_when_confirmed(settings_view, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    emitted = []
    settings_view.clear_cache_requested.connect(lambda: emitted.append(True))

    settings_view._on_clear_cache()
    assert emitted


def test_set_settings_updates_widgets(settings_view):
    settings_view.set_settings(
        AppSettings(
            link_threshold=75.0,
            high_confidence_threshold=92.0,
            map_page_threshold=0.55,
            prefilter_enabled=False,
        )
    )
    assert settings_view._link_spin.value() == 75.0
    assert settings_view._high_spin.value() == 92.0
    assert settings_view._map_spin.value() == 0.55
    assert settings_view._prefilter_check.isChecked() is False
