from __future__ import annotations

from dataclasses import asdict, dataclass, replace

from PySide6.QtCore import QSettings

from config import APP_NAME, MAP_PAGE_THRESHOLD, APP_ORGANIZATION
from utils.logging_utils import get_logger

logger = get_logger("settings_store")

SETTINGS_GROUP = "thresholds"


@dataclass(frozen=True)
class AppSettings:
    """User-tunable thresholds (PRD section 9).

    ``link_threshold`` defaults to 82, not PRD's 65: 65-84 is the PRD's "needs
    review" *display* band, and using its floor as a joining rule merged maps that
    only shared the page frame. Measured genuine pairs scored 79.9-85.2 while
    frame-only matches scored 0.
    """

    link_threshold: float = 82.0
    high_confidence_threshold: float = 85.0
    map_page_threshold: float = MAP_PAGE_THRESHOLD
    prefilter_enabled: bool = True

    def validated(self) -> "AppSettings":
        """Clamp values and keep them consistent with each other."""
        link = min(max(float(self.link_threshold), 60.0), 98.0)
        high = min(max(float(self.high_confidence_threshold), link + 1.0), 100.0)
        map_page = min(max(float(self.map_page_threshold), 0.30), 0.80)
        return replace(
            self,
            link_threshold=round(link, 1),
            high_confidence_threshold=round(high, 1),
            map_page_threshold=round(map_page, 2),
        )

    @property
    def is_consistent(self) -> bool:
        return self.high_confidence_threshold > self.link_threshold


DEFAULT_SETTINGS = AppSettings()


def _settings() -> QSettings:
    return QSettings(APP_ORGANIZATION, APP_NAME)


def load_settings() -> AppSettings:
    """Read settings, falling back to defaults for missing/invalid values."""
    store = _settings()
    store.beginGroup(SETTINGS_GROUP)
    try:
        settings = AppSettings(
            link_threshold=float(store.value("link_threshold", DEFAULT_SETTINGS.link_threshold)),
            high_confidence_threshold=float(
                store.value(
                    "high_confidence_threshold",
                    DEFAULT_SETTINGS.high_confidence_threshold,
                )
            ),
            map_page_threshold=float(
                store.value("map_page_threshold", DEFAULT_SETTINGS.map_page_threshold)
            ),
            prefilter_enabled=_as_bool(
                store.value("prefilter_enabled", DEFAULT_SETTINGS.prefilter_enabled)
            ),
        )
    except (TypeError, ValueError) as exc:
        logger.warning("Pengaturan tersimpan tidak valid (%s), memakai default", exc)
        return DEFAULT_SETTINGS
    finally:
        store.endGroup()

    return settings.validated()


def save_settings(settings: AppSettings) -> AppSettings:
    """Persist settings and return the validated version actually stored."""
    stored = settings.validated()
    store = _settings()
    store.beginGroup(SETTINGS_GROUP)
    try:
        for key, value in asdict(stored).items():
            store.setValue(key, value)
    finally:
        store.endGroup()
    store.sync()
    logger.info("Pengaturan disimpan: %s", stored)
    return stored


def reset_settings() -> AppSettings:
    return save_settings(DEFAULT_SETTINGS)


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "ya", "on")
    return bool(value)


__all__ = [
    "AppSettings",
    "DEFAULT_SETTINGS",
    "load_settings",
    "reset_settings",
    "save_settings",
]
