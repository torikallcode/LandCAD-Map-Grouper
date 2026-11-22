from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import numpy as np


def decode_png(png_bytes: bytes, flags: int = cv2.IMREAD_UNCHANGED) -> np.ndarray:
    """Decode PNG bytes into a BGR or grayscale ndarray."""
    buffer = np.frombuffer(png_bytes, dtype=np.uint8)
    image = cv2.imdecode(buffer, flags)
    if image is None:
        raise ValueError("Gambar tidak dapat didekode.")
    return image


def decode_png_gray(png_bytes: bytes) -> np.ndarray:
    return decode_png(png_bytes, cv2.IMREAD_GRAYSCALE)


def encode_png(image: np.ndarray) -> bytes:
    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("Gambar tidak dapat dienkode ke PNG.")
    return buffer.tobytes()


def to_grayscale(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    if image.ndim == 3 and image.shape[2] == 1:
        return image[:, :, 0]
    if image.ndim == 3 and image.shape[2] == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if image.ndim == 3 and image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
    raise ValueError(f"Format gambar tidak didukung: shape={image.shape}")


def binarize(gray: np.ndarray) -> np.ndarray:
    """Otsu threshold with ink as foreground (255 = ink)."""
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    _, binary = cv2.threshold(
        blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )
    return binary


def resize_long_side(image: np.ndarray, target_long_side: int) -> tuple[np.ndarray, float]:
    """Resize so the longest side equals ``target_long_side``. Returns (image, scale)."""
    height, width = image.shape[:2]
    longest = max(height, width)
    if target_long_side <= 0 or longest == 0:
        return image, 1.0
    scale = target_long_side / float(longest)
    if abs(scale - 1.0) < 1e-3:
        return image, 1.0
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
    resized = cv2.resize(
        image, (max(int(round(width * scale)), 1), max(int(round(height * scale)), 1)),
        interpolation=interpolation,
    )
    return resized, scale


def rotate(image: np.ndarray, angle: float, border_value: int = 255) -> np.ndarray:
    """Rotate around the centre, keeping the original canvas size."""
    if abs(angle) < 1e-6:
        return image
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), angle, 1.0)
    return cv2.warpAffine(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=border_value,
    )


def hough_segments(binary: np.ndarray) -> np.ndarray:
    """Return Hough line segments as an (N, 4) array regardless of OpenCV version."""
    height, width = binary.shape[:2]
    lines = cv2.HoughLinesP(
        binary,
        1,
        np.pi / 360,
        threshold=50,
        minLineLength=max(height, width) // 20,
        maxLineGap=6,
    )
    if lines is None:
        return np.empty((0, 4), dtype=np.int32)
    return np.asarray(lines, dtype=np.int32).reshape(-1, 4)


def segment_angle_degrees(segment: np.ndarray) -> float:
    """Angle of a segment mapped to (-90, 90] degrees."""
    x1, y1, x2, y2 = (float(value) for value in segment)
    angle = np.degrees(np.arctan2(y2 - y1, x2 - x1)) % 180.0
    if angle > 90.0:
        angle -= 180.0
    return float(angle)


def path_cache_key(path: Path) -> str:
    digest = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()
    return digest[:10]


def text_mask(binary: np.ndarray) -> np.ndarray:
    """Isolate glyph-sized components, dropping long map lines and page frames.

    Connected components are filtered by area and aspect so that only small,
    roughly character-sized blobs survive. This is what makes orientation
    analysis work on sparse map pages: the raw page projection is dominated by
    frame borders, while the surviving glyphs still reveal text direction.
    """
    height, width = binary.shape[:2]
    if height == 0 or width == 0:
        return binary

    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        binary, connectivity=8
    )
    keep = np.zeros(count, dtype=bool)
    min_area = 8
    max_area = max(60, int(height * width * 0.0015))
    min_height, max_height = 3, max(height // 25, 8)
    max_width = max(width // 12, 20)

    for index in range(1, count):
        _x, _y, component_width, component_height, area = stats[index]
        if (
            min_area <= area <= max_area
            and min_height <= component_height <= max_height
            and component_width <= max_width
        ):
            keep[index] = True

    return (keep[labels] * 255).astype(np.uint8)


def projection_variance_ratio(mask: np.ndarray, inset: float = 0.06) -> float:
    """Row vs column projection variance of a mask (after trimming the border)."""
    height, width = mask.shape[:2]
    if height == 0 or width == 0:
        return 1.0
    iy = int(height * inset)
    ix = int(width * inset)
    core = mask[iy:height - iy, ix:width - ix]
    if core.size == 0:
        core = mask

    rows = (core > 0).sum(axis=1).astype(np.float64)
    columns = (core > 0).sum(axis=0).astype(np.float64)
    row_variance = float(rows.var() / (rows.mean() ** 2 + 1e-6))
    column_variance = float(columns.var() / (columns.mean() ** 2 + 1e-6))
    return row_variance / (column_variance + 1e-6)
