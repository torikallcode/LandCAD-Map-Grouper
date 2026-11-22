from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "LandCAD Map Grouper"
APP_VERSION = "0.9.0"
APP_ORGANIZATION = "LandCAD"


def _runtime_root() -> Path:
    """Directory that should hold writable app data.

    When frozen by PyInstaller ``__file__`` lives inside ``_internal``, which is
    read-only in a normal install (and would also lose data on upgrade). The
    executable's own directory is the right place for ``data/``.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = _runtime_root()
DATA_DIR = RUNTIME_ROOT / "data"
LOG_DIR = DATA_DIR / "logs"
CACHE_DIR = DATA_DIR / "cache"
OUTPUT_DIR = DATA_DIR / "output"

SUPPORTED_EXTENSIONS = (".pdf",)

DEFAULT_PREVIEW_DPI = 144
MAX_PREVIEW_DIMENSION = 2400
RENDER_DEBOUNCE_MS = 120

MIN_ZOOM = 0.25
MAX_ZOOM = 4.0

RECURSIVE_SCAN_DEFAULT = False
LOG_LEVEL = os.environ.get("LANDCAD_LOG_LEVEL", "INFO").upper()

MAP_DETECTION_DPI = 120
MAP_PAGE_THRESHOLD = 0.45
NORMALIZED_DIR = CACHE_DIR / "normalized"
FEATURE_CACHE_DIR = CACHE_DIR / "features"
ORB_N_FEATURES = 2000
PARCEL_MASKING_DEFAULT = True


@dataclass(frozen=True)
class AppPaths:
    data: Path = DATA_DIR
    logs: Path = LOG_DIR
    cache: Path = CACHE_DIR
    output: Path = OUTPUT_DIR
    normalized: Path = NORMALIZED_DIR
    features: Path = FEATURE_CACHE_DIR

    def ensure(self) -> "AppPaths":
        for directory in (
            self.data,
            self.logs,
            self.cache,
            self.output,
            self.normalized,
            self.features,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return self


PATHS = AppPaths()
