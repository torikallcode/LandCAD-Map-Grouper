from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from config import PATHS
from core.clustering_engine import (
    DEFAULT_CLUSTER_CONFIG,
    ClusterConfig,
    ClusteringResult,
    MapDocument,
    cluster_documents,
    clustering_summary,
)
from core.feature_extractor import OrbFeatures
from core.pdf_reader import PdfInfo
from core.similarity_engine import similarity_summary
from services.comparison_service import (
    CACHE_VERSION,
    ComparableMap,
    prepare_comparable,
)
from utils.logging_utils import get_logger

logger = get_logger("grouping_service")

ProgressCallback = Callable[[int, int, str], None]
CancelCheck = Callable[[], bool]


@dataclass(frozen=True)
class GroupedDocument:
    """A document prepared for grouping (parcel-free map + features)."""

    info: PdfInfo
    image: np.ndarray = field(repr=False)
    features: OrbFeatures = field(repr=False)
    comparable: ComparableMap | None = None

    @property
    def key(self) -> str:
        return self.info.key

    @property
    def filename(self) -> str:
        return self.info.filename

    def to_map_document(self) -> MapDocument:
        return MapDocument(
            key=self.key,
            filename=self.filename,
            image=self.image,
            features=self.features,
        )


@dataclass(frozen=True)
class GroupingResult:
    clustering: ClusteringResult
    documents: tuple[GroupedDocument, ...] = ()
    cancelled: bool = False
    errors: tuple[str, ...] = ()

    @property
    def group_count(self) -> int:
        return self.clustering.total_groups

    @property
    def ungrouped_count(self) -> int:
        return self.clustering.ungrouped_count

    def name_of(self, key: str) -> str:
        for document in self.documents:
            if document.key == key:
                return document.filename
        return key

    def filenames(self, keys: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(self.name_of(key) for key in keys)

    def group_members(self, group_id: int) -> tuple[GroupedDocument, ...]:
        group = next(
            (item for item in self.clustering.groups if item.group_id == group_id),
            None,
        )
        if group is None:
            return ()
        members = set(group.member_keys)
        return tuple(document for document in self.documents if document.key in members)

    def summary(self) -> str:
        names = {document.key: document.filename for document in self.documents}
        lines = [clustering_summary(self.clustering, names)]
        if self.errors:
            lines.append(f"Gagal disiapkan: {len(self.errors)} dokumen")
            for error in self.errors[:5]:
                lines.append(f"  - {error}")
        return "\n".join(lines)


def prepare_grouped_document(
    info: PdfInfo,
    cache_dir: Path | None = None,
) -> GroupedDocument:
    """Prepare one document using the parcel-free map when available.

    Grouping deliberately uses the parcel-free image: the whole point is that two
    documents sharing a base map group together even when their main parcels sit
    in different places.
    """
    comparable = prepare_comparable(info, cache_dir=cache_dir)

    if comparable.masked_image is not None and comparable.masked_features is not None:
        image = comparable.masked_image
        features = comparable.masked_features
    else:
        image = comparable.image
        features = comparable.features

    return GroupedDocument(
        info=info, image=image, features=features, comparable=comparable
    )


def prepare_documents(
    documents: list[PdfInfo],
    cache_dir: Path | None = None,
    progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> tuple[list[GroupedDocument], list[str], bool]:
    prepared: list[GroupedDocument] = []
    errors: list[str] = []
    total = len(documents)

    for index, info in enumerate(documents, start=1):
        if should_cancel is not None and should_cancel():
            return prepared, errors, True
        if progress is not None:
            progress(index, total, f"Menyiapkan {info.filename}")
        try:
            prepared.append(prepare_grouped_document(info, cache_dir=cache_dir))
        except Exception as exc:  # noqa: BLE001 - one bad document must not stop the batch
            logger.warning("Gagal menyiapkan %s: %s", info.filename, exc)
            errors.append(f"{info.filename}: {exc}")

    return prepared, errors, False


def group_documents(
    documents: list[PdfInfo],
    cache_dir: Path | None = PATHS.features,
    config: ClusterConfig = DEFAULT_CLUSTER_CONFIG,
    progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
    prepared: list[GroupedDocument] | None = None,
) -> GroupingResult:
    """Prepare documents and group them by base-map similarity."""
    errors: list[str] = []

    if prepared is None:
        prepared, errors, cancelled = prepare_documents(
            documents, cache_dir=cache_dir, progress=progress, should_cancel=should_cancel
        )
        if cancelled:
            return GroupingResult(
                clustering=ClusteringResult(), documents=tuple(prepared),
                cancelled=True, errors=tuple(errors),
            )
    else:
        prepared = list(prepared)

    map_documents = [document.to_map_document() for document in prepared]
    clustering = cluster_documents(
        map_documents,
        config=config,
        progress=progress,
        should_cancel=should_cancel,
    )

    return GroupingResult(
        clustering=clustering,
        documents=tuple(prepared),
        cancelled=False,
        errors=tuple(errors),
    )


__all__ = [
    "CACHE_VERSION",
    "GroupedDocument",
    "GroupingResult",
    "group_documents",
    "prepare_documents",
    "prepare_grouped_document",
    "similarity_summary",
]
