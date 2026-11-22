from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from config import PATHS
from core.image_normalizer import PREPROCESSING_VERSION
from core.map_page_detector import DETECTOR_VERSION
from core.parcel_detector import PARCEL_VERSION
from core.pdf_reader import PdfInfo
from utils.image_utils import decode_png_gray, encode_png
from utils.logging_utils import get_logger

logger = get_logger("cache_service")

CACHE_SCHEMA_VERSION = "cache-v1"


def file_fingerprint(path: Path, chunk_size: int = 1 << 20) -> str:
    """SHA-256 of the file contents (streamed so big PDFs stay cheap)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def analysis_cache_key(
    file_hash: str,
    dpi: int,
    detector_version: str = DETECTOR_VERSION,
    preprocessing_version: str = PREPROCESSING_VERSION,
    parcel_version: str = PARCEL_VERSION,
) -> str:
    """Cache key that changes whenever the pipeline changes.

    Any change to detector, normalisation or parcel logic invalidates previously
    cached analysis, because a stale map must never be compared silently.
    """
    payload = "|".join(
        [
            CACHE_SCHEMA_VERSION,
            file_hash,
            str(dpi),
            detector_version,
            preprocessing_version,
            parcel_version,
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class CachedAnalysis:
    """Everything expensive about one PDF, stored on disk."""

    key: str
    info: PdfInfo
    map_page: int | None
    page_count: int
    detection_scores: tuple[tuple[int, float, bool], ...] = ()
    normalized: np.ndarray | None = None
    masked: np.ndarray | None = None
    parcel_polygons: tuple[np.ndarray, ...] = ()
    parcel_confidences: tuple[float, ...] = ()
    created_at: str = ""
    cache_hit: bool = False

    @property
    def has_map(self) -> bool:
        return self.map_page is not None


class AnalysisCache:
    """Filesystem cache for rendered / detected / normalised map pages.

    Measured on the reference documents: rendering, page detection and
    normalisation cost ~1.0 s per document while ORB costs 27 ms. Re-running the
    whole pipeline for every grouping was therefore the real bottleneck, not the
    feature extraction. Caching the normalised and parcel-free images brings a
    repeat analysis down to a few milliseconds.
    """

    def __init__(self, root: Path | str = PATHS.cache / "analysis") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ path
    def _dir(self, key: str) -> Path:
        return self.root / key[:2] / key

    def has(self, key: str) -> bool:
        return (self._dir(key) / "meta.json").exists()

    # ----------------------------------------------------------------- write
    def store(self, analysis: CachedAnalysis) -> Path:
        directory = self._dir(analysis.key)
        directory.mkdir(parents=True, exist_ok=True)

        metadata = {
            "key": analysis.key,
            "filename": analysis.info.filename,
            "original_path": str(analysis.info.path),
            "size_bytes": analysis.info.size_bytes,
            "page_count": analysis.page_count,
            "map_page": analysis.map_page,
            "detection_scores": [
                {"page_index": index, "score": score, "is_map": is_map}
                for index, score, is_map in analysis.detection_scores
            ],
            "parcel_polygons": [
                polygon.reshape(-1).tolist() for polygon in analysis.parcel_polygons
            ],
            "parcel_confidences": list(analysis.parcel_confidences),
            "created_at": analysis.created_at
            or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "schema": CACHE_SCHEMA_VERSION,
        }
        (directory / "meta.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        if analysis.normalized is not None:
            (directory / "normalized.png").write_bytes(encode_png(analysis.normalized))
        if analysis.masked is not None:
            (directory / "masked.png").write_bytes(encode_png(analysis.masked))

        logger.debug("Cache disimpan: %s", directory)
        return directory

    # ------------------------------------------------------------------ read
    def load(self, key: str, info: PdfInfo) -> CachedAnalysis | None:
        directory = self._dir(key)
        metadata_path = directory / "meta.json"
        if not metadata_path.exists():
            return None

        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Cache rusak (%s), dihapus: %s", directory.name, exc)
            self.invalidate(key)
            return None

        if metadata.get("schema") != CACHE_SCHEMA_VERSION:
            self.invalidate(key)
            return None

        try:
            normalized = self._read_image(directory / "normalized.png")
            masked = self._read_image(directory / "masked.png")
        except (OSError, ValueError) as exc:
            logger.warning("Gambar cache rusak (%s), dihapus: %s", directory.name, exc)
            self.invalidate(key)
            return None

        polygons = tuple(
            np.asarray(flat, dtype=np.int32).reshape(-1, 1, 2)
            for flat in metadata.get("parcel_polygons", [])
        )
        scores = tuple(
            (int(item["page_index"]), float(item["score"]), bool(item["is_map"]))
            for item in metadata.get("detection_scores", [])
        )

        return CachedAnalysis(
            key=key,
            info=info,
            map_page=(
                int(metadata["map_page"]) if metadata.get("map_page") is not None else None
            ),
            page_count=int(metadata.get("page_count", info.page_count)),
            detection_scores=scores,
            normalized=normalized,
            masked=masked,
            parcel_polygons=polygons,
            parcel_confidences=tuple(
                float(value) for value in metadata.get("parcel_confidences", [])
            ),
            created_at=str(metadata.get("created_at", "")),
            cache_hit=True,
        )

    # ------------------------------------------------------------- lifecycle
    def invalidate(self, key: str) -> None:
        directory = self._dir(key)
        if not directory.exists():
            return
        for path in directory.iterdir():
            try:
                path.unlink()
            except OSError:
                pass
        try:
            directory.rmdir()
        except OSError:
            pass

    def clear(self) -> int:
        """Remove every cached entry.

        The list is materialised first: deleting directories while ``rglob`` is
        still walking them raises FileNotFoundError on Windows.
        """
        keys = [meta.parent.name for meta in list(self.root.rglob("meta.json"))]
        for key in keys:
            self.invalidate(key)
        return len(keys)

    def stats(self) -> dict[str, float]:
        entries = 0
        size = 0
        for path in list(self.root.rglob("*")):
            if not path.is_file():
                continue
            try:
                size += path.stat().st_size
            except OSError:
                continue
            if path.name == "meta.json":
                entries += 1
        return {"entries": float(entries), "size_bytes": float(size)}

    @staticmethod
    def _read_image(path: Path) -> np.ndarray | None:
        if not path.exists():
            return None
        return decode_png_gray(path.read_bytes())


__all__ = [
    "CACHE_SCHEMA_VERSION",
    "AnalysisCache",
    "CachedAnalysis",
    "analysis_cache_key",
    "file_fingerprint",
]
