from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from utils.image_utils import to_grayscale
from utils.logging_utils import get_logger

logger = get_logger("feature_extractor")

FEATURE_VERSION = "orb-v1"


@dataclass(frozen=True)
class FeatureConfig:
    """ORB parameters tuned for sparse land-survey line drawings.

    ``fast_threshold=10`` and ``edge_threshold=15`` were needed to keep enough
    corners on nearly-blank map pages: with OpenCV's defaults (20/31) the map
    pages produced too few keypoints to align reliably.
    """

    n_features: int = 2000
    scale_factor: float = 1.2
    n_levels: int = 8
    edge_threshold: int = 15
    fast_threshold: int = 10
    clahe_clip_limit: float = 2.0
    clahe_tile_grid: int = 8
    blur_kernel: int = 3


DEFAULT_FEATURE_CONFIG = FeatureConfig()


@dataclass(frozen=True)
class OrbFeatures:
    """ORB keypoints and descriptors for one page.

    Named ``OrbFeatures`` rather than ``PageFeatures`` to stay distinct from the
    map-detection ``PageFeatures`` dataclass in ``core.map_page_detector``.
    """

    keypoints: tuple[cv2.KeyPoint, ...] = field(default=(), repr=False)
    descriptors: np.ndarray | None = field(default=None, repr=False)
    image_shape: tuple[int, int] = (0, 0)
    feature_version: str = FEATURE_VERSION

    @property
    def count(self) -> int:
        return 0 if self.descriptors is None else int(self.descriptors.shape[0])

    @property
    def is_empty(self) -> bool:
        return self.count == 0

    def points(self) -> np.ndarray:
        """Keypoint locations as an (N, 2) float32 array."""
        if not self.keypoints:
            return np.empty((0, 2), dtype=np.float32)
        return np.asarray([kp.pt for kp in self.keypoints], dtype=np.float32)

    def to_cache(self) -> dict[str, np.ndarray]:
        """Serialisable payload for ``np.savez_compressed``."""
        points = self.points()
        sizes = np.asarray([kp.size for kp in self.keypoints], dtype=np.float32)
        angles = np.asarray([kp.angle for kp in self.keypoints], dtype=np.float32)
        responses = np.asarray(
            [kp.response for kp in self.keypoints], dtype=np.float32
        )
        return {
            "points": points,
            "sizes": sizes,
            "angles": angles,
            "responses": responses,
            "descriptors": (
                self.descriptors
                if self.descriptors is not None
                else np.empty((0, 32), dtype=np.uint8)
            ),
            "image_shape": np.asarray(self.image_shape, dtype=np.int32),
            "version": np.asarray(self.feature_version),
        }


def preprocess_for_features(
    image: np.ndarray, config: FeatureConfig = DEFAULT_FEATURE_CONFIG
) -> np.ndarray:
    """Grayscale -> CLAHE -> light blur.

    Contrast normalisation before ORB matters here: the map pages are faint
    scans, and raw contrast produced noticeably fewer stable corners.
    """
    gray = to_grayscale(image)
    clahe = cv2.createCLAHE(
        clipLimit=config.clahe_clip_limit,
        tileGridSize=(config.clahe_tile_grid, config.clahe_tile_grid),
    )
    gray = clahe.apply(gray)
    if config.blur_kernel > 1:
        kernel = config.blur_kernel | 1
        gray = cv2.GaussianBlur(gray, (kernel, kernel), 0)
    return gray


def create_detector(config: FeatureConfig = DEFAULT_FEATURE_CONFIG) -> cv2.ORB:
    return cv2.ORB_create(
        nfeatures=config.n_features,
        scaleFactor=config.scale_factor,
        nlevels=config.n_levels,
        edgeThreshold=config.edge_threshold,
        fastThreshold=config.fast_threshold,
    )


def extract_features(
    image: np.ndarray, config: FeatureConfig = DEFAULT_FEATURE_CONFIG
) -> OrbFeatures:
    gray = preprocess_for_features(image, config)
    keypoints, descriptors = create_detector(config).detectAndCompute(gray, None)
    height, width = gray.shape[:2]

    if descriptors is None or len(keypoints) == 0:
        logger.debug("Tidak ada fitur terdeteksi pada halaman %dx%d", width, height)
        return OrbFeatures(image_shape=(height, width))

    order = np.argsort([-kp.response for kp in keypoints], kind="stable")
    ordered_keypoints = tuple(keypoints[index] for index in order)
    ordered_descriptors = np.ascontiguousarray(descriptors[order])

    return OrbFeatures(
        keypoints=ordered_keypoints,
        descriptors=ordered_descriptors,
        image_shape=(height, width),
    )


def features_to_cache(features: OrbFeatures, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **features.to_cache())
    return path


def features_from_cache(path: Path) -> OrbFeatures:
    with np.load(Path(path), allow_pickle=False) as payload:
        points = payload["points"]
        sizes = payload["sizes"]
        angles = payload["angles"]
        responses = payload["responses"]
        descriptors = payload["descriptors"]
        shape = tuple(int(value) for value in payload["image_shape"])
        version = str(payload["version"])

    keypoints = tuple(
        cv2.KeyPoint(
            float(point[0]),
            float(point[1]),
            float(size),
            float(angle),
            float(response),
        )
        for point, size, angle, response in zip(points, sizes, angles, responses)
    )
    return OrbFeatures(
        keypoints=keypoints,
        descriptors=descriptors if descriptors.size else None,
        image_shape=(shape[0], shape[1]) if len(shape) == 2 else (0, 0),
        feature_version=version,
    )
