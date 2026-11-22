from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np

from core.similarity_engine import (
    DEFAULT_SIMILARITY_CONFIG,
    SimilarityConfig,
    SimilarityResult,
    prefilter_pair,
    score_pair,
)
from utils.logging_utils import get_logger

logger = get_logger("clustering_engine")

CLUSTERING_VERSION = "cluster-v1"

UNGROUPED_LABEL = "BELUM_TERKELOMPOK"


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class ClusterConfig:
    """Thresholds for grouping.

    PRD section 9 bands: 85-100 high, 65-84 needs review, below 65 low. Grouping
    only links pairs at or above ``link_threshold``; anything below is treated as
    "no relationship", so a weak score can never pull documents together.
    """

    link_threshold: float = 65.0
    high_confidence_threshold: float = 85.0
    prefilter_enabled: bool = True
    prefilter_ratio: float = 0.75
    prefilter_min_matches: int = 12


DEFAULT_CLUSTER_CONFIG = ClusterConfig()


@dataclass(frozen=True)
class MapDocument:
    """One document's map contribution to grouping."""

    key: str
    filename: str
    image: np.ndarray = field(repr=False)
    features: object = field(repr=False)

    @property
    def is_usable(self) -> bool:
        return not self.features.is_empty


@dataclass(frozen=True)
class SimilarityEdge:
    """Measured relationship between two documents."""

    a_key: str
    b_key: str
    score: float
    skipped: bool = False
    reason: str = ""

    @property
    def is_link(self) -> bool:
        return not self.skipped and self.score >= DEFAULT_CLUSTER_CONFIG.link_threshold


@dataclass(frozen=True)
class Group:
    """A cluster of documents that share a base map."""

    group_id: int
    member_keys: tuple[str, ...]
    confidence: str = "Rendah"
    min_pair_score: float = 0.0
    needs_review: bool = True

    @property
    def name(self) -> str:
        return f"KELOMPOK_{self.group_id:03d}"

    @property
    def size(self) -> int:
        return len(self.member_keys)


@dataclass(frozen=True)
class ClusteringResult:
    groups: tuple[Group, ...] = ()
    ungrouped: tuple[str, ...] = ()
    edges: tuple[SimilarityEdge, ...] = ()
    skipped_pairs: int = 0
    version: str = CLUSTERING_VERSION

    @property
    def total_groups(self) -> int:
        return len(self.groups)

    @property
    def grouped_count(self) -> int:
        return sum(group.size for group in self.groups)

    @property
    def ungrouped_count(self) -> int:
        return len(self.ungrouped)

    @property
    def review_count(self) -> int:
        return sum(1 for group in self.groups if group.needs_review)

    def group_of(self, key: str) -> Group | None:
        for group in self.groups:
            if key in group.member_keys:
                return group
        return None


PairScorer = Callable[[MapDocument, MapDocument], SimilarityResult]


def _default_scorer(config: SimilarityConfig = DEFAULT_SIMILARITY_CONFIG) -> PairScorer:
    def score(reference: MapDocument, candidate: MapDocument) -> SimilarityResult:
        return score_pair(
            reference.image,
            candidate.image,
            reference.features,
            candidate.features,
            config,
        )

    return score


def build_similarity_matrix(
    documents: Sequence[MapDocument],
    scorer: PairScorer | None = None,
    config: ClusterConfig = DEFAULT_CLUSTER_CONFIG,
    progress: Callable[[int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[np.ndarray, tuple[SimilarityEdge, ...], int]:
    """Score every unordered pair once.

    Returns the symmetric score matrix (0-100), the measured edges, and the
    number of pairs skipped by the cheap prefilter.
    """
    scorer = scorer or _default_scorer()
    count = len(documents)
    matrix = np.zeros((count, count), dtype=np.float64)
    np.fill_diagonal(matrix, 100.0)

    total_pairs = count * (count - 1) // 2
    edges: list[SimilarityEdge] = []
    skipped = 0
    processed = 0

    for i in range(count):
        for j in range(i + 1, count):
            if should_cancel is not None and should_cancel():
                logger.info("Perbandingan dibatalkan pada %d/%d pasangan", processed, total_pairs)
                return matrix, tuple(edges), skipped

            processed += 1
            if progress is not None:
                progress(processed, total_pairs, f"{documents[i].filename} ↔ {documents[j].filename}")

            left, right = documents[i], documents[j]

            if config.prefilter_enabled and not prefilter_pair(
                left.features,
                right.features,
                config.prefilter_ratio,
                config.prefilter_min_matches,
            ):
                skipped += 1
                edges.append(
                    SimilarityEdge(
                        a_key=left.key,
                        b_key=right.key,
                        score=0.0,
                        skipped=True,
                        reason="prefilter: kecocokan fitur terlalu sedikit",
                    )
                )
                continue

            result = scorer(left, right)
            score = result.score.score if result.score.aligned else 0.0
            matrix[i, j] = score
            matrix[j, i] = score
            edges.append(
                SimilarityEdge(
                    a_key=left.key,
                    b_key=right.key,
                    score=score,
                    skipped=False,
                    reason=result.score.blocked_reason,
                )
            )

    return matrix, tuple(edges), skipped


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        root_left, root_right = self.find(left), self.find(right)
        if root_left == root_right:
            return
        if self.rank[root_left] < self.rank[root_right]:
            root_left, root_right = root_right, root_left
        self.parent[root_right] = root_left
        if self.rank[root_left] == self.rank[root_right]:
            self.rank[root_left] += 1


def build_groups(
    documents: Sequence[MapDocument],
    matrix: np.ndarray,
    config: ClusterConfig = DEFAULT_CLUSTER_CONFIG,
) -> tuple[tuple[Group, ...], tuple[str, ...]]:
    """Connected components over pairs scoring at or above the link threshold.

    Graph-based grouping is used instead of agglomerative clustering because the
    requirement is "same base map or not", not a distance hierarchy. Documents
    with no qualifying edge stay ungrouped rather than being forced into a group.
    """
    count = len(documents)
    union = _UnionFind(count)

    for i in range(count):
        for j in range(i + 1, count):
            if matrix[i, j] >= config.link_threshold:
                union.union(i, j)

    buckets: dict[int, list[int]] = {}
    for index in range(count):
        buckets.setdefault(union.find(index), []).append(index)

    groups: list[Group] = []
    ungrouped: list[str] = []
    group_id = 1

    for members in sorted(buckets.values(), key=lambda items: (-len(items), items[0])):
        if len(members) < 2:
            ungrouped.extend(documents[index].key for index in members)
            continue

        # Only linked pairs describe the group's strength. An unlinked pair inside
        # the same group (a-b linked, b-c linked, but a-c weak) is a transitive
        # artifact, not evidence of a weak group.
        scores = [
            matrix[a, b]
            for a in members
            for b in members
            if a < b and matrix[a, b] >= config.link_threshold
        ]
        minimum = float(min(scores)) if scores else 0.0
        confidence = (
            "Tinggi"
            if minimum >= config.high_confidence_threshold
            else "Sedang" if minimum >= config.link_threshold else "Rendah"
        )
        groups.append(
            Group(
                group_id=group_id,
                member_keys=tuple(documents[index].key for index in members),
                confidence=confidence,
                min_pair_score=round(minimum, 1),
                needs_review=minimum < config.high_confidence_threshold,
            )
        )
        group_id += 1

    return tuple(groups), tuple(ungrouped)


def cluster_documents(
    documents: Sequence[MapDocument],
    scorer: PairScorer | None = None,
    config: ClusterConfig = DEFAULT_CLUSTER_CONFIG,
    progress: Callable[[int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> ClusteringResult:
    """Group documents by base-map similarity."""
    usable = [doc for doc in documents if doc.is_usable]
    unusable = [doc for doc in documents if not doc.is_usable]

    if not usable:
        return ClusteringResult(ungrouped=tuple(doc.key for doc in unusable))

    matrix, edges, skipped = build_similarity_matrix(
        usable, scorer=scorer, config=config, progress=progress, should_cancel=should_cancel
    )
    groups, ungrouped = build_groups(usable, matrix, config)

    logger.info(
        "Pengelompokan selesai: %d kelompok, %d belum, %d pasangan dilewati",
        len(groups),
        len(ungrouped) + len(unusable),
        skipped,
    )
    return ClusteringResult(
        groups=groups,
        ungrouped=tuple(list(ungrouped) + [doc.key for doc in unusable]),
        edges=edges,
        skipped_pairs=skipped,
    )


def clustering_summary(result: ClusteringResult, names: dict[str, str] | None = None) -> str:
    names = names or {}
    lines = [
        f"Jumlah kelompok: {result.total_groups}",
        f"Dokumen berkelompok: {result.grouped_count}",
        f"Belum terkelompok: {result.ungrouped_count}",
        f"Pasangan dilewati prefilter: {result.skipped_pairs}",
    ]
    for group in result.groups:
        members = ", ".join(names.get(key, key) for key in group.member_keys)
        lines.append(
            f"{group.name}: {group.size} dokumen "
            f"(keyakinan {group.confidence}, skor min {group.min_pair_score:.1f}) — {members}"
        )
    if result.ungrouped:
        lines.append(
            "Belum terkelompok: "
            + ", ".join(names.get(key, key) for key in result.ungrouped)
        )
    return "\n".join(lines)


__all__ = [
    "CLUSTERING_VERSION",
    "ClusterConfig",
    "ClusteringResult",
    "DEFAULT_CLUSTER_CONFIG",
    "Group",
    "MapDocument",
    "SimilarityEdge",
    "UNGROUPED_LABEL",
    "build_groups",
    "build_similarity_matrix",
    "cluster_documents",
    "clustering_summary",
]
