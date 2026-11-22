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
    """Thresholds and linkage strategy for grouping.

    Two thresholds, because PRD 9's bands are *display* categories, not a joining
    rule:

    * ``link_threshold`` (default 82) — the score a pair needs before it may join
      a group. Set from measurement: genuine pairs scored 79.9-85.2, while maps
      that only shared the page frame scored 0. Using PRD's 65 here was the main
      reason different maps got merged.
    * ``candidate_threshold`` (default 65) — the floor of the "needs review"
      band. Pairs in 65-81 are reported as candidates for manual inspection and
      are **not** merged automatically.

    ``linkage="average"`` prevents single-linkage chaining: one borderline pair
    cannot merge two clusters (see the 118-document regression test).
    """

    link_threshold: float = 82.0
    candidate_threshold: float = 65.0
    high_confidence_threshold: float = 85.0
    linkage: str = "average"
    prefilter_enabled: bool = True
    prefilter_ratio: float = 0.75
    prefilter_min_matches: int = 12

    def __post_init__(self) -> None:
        if self.linkage not in ("average", "single"):
            raise ValueError(
                f"linkage tidak dikenal: {self.linkage!r} "
                "(gunakan 'average' atau 'single')"
            )
        if self.candidate_threshold >= self.link_threshold:
            raise ValueError(
                "candidate_threshold harus lebih kecil daripada link_threshold"
            )


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
    match_count: int = 0
    inlier_count: int = 0

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
class CandidatePair:
    """A pair in the "needs review" band, offered for manual inspection only."""

    a_key: str
    b_key: str
    score: float

    @property
    def label(self) -> str:
        return f"{self.a_key} ↔ {self.b_key}"


@dataclass(frozen=True)
class ClusteringResult:
    groups: tuple[Group, ...] = ()
    ungrouped: tuple[str, ...] = ()
    edges: tuple[SimilarityEdge, ...] = ()
    skipped_pairs: int = 0
    candidates: tuple[CandidatePair, ...] = ()
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
    def candidate_count(self) -> int:
        return len(self.candidates)

    @property
    def review_count(self) -> int:
        return sum(1 for group in self.groups if group.needs_review)

    def group_of(self, key: str) -> Group | None:
        for group in self.groups:
            if key in group.member_keys:
                return group
        return None

    def candidates_of(self, key: str) -> tuple[CandidatePair, ...]:
        return tuple(
            pair
            for pair in self.candidates
            if key in (pair.a_key, pair.b_key)
        )


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
            alignment = result.alignment
            edges.append(
                SimilarityEdge(
                    a_key=left.key,
                    b_key=right.key,
                    score=score,
                    skipped=False,
                    reason=result.score.blocked_reason,
                    match_count=alignment.match_count if alignment is not None else 0,
                    inlier_count=alignment.inlier_count if alignment is not None else 0,
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


def _average_linkage_clusters(matrix: np.ndarray, threshold: float) -> list[list[int]]:
    """Agglomerative average linkage (UPGMA) using Lance-Williams updates.

    Two clusters merge only when the *mean* similarity over all cross pairs is at
    or above ``threshold``. Single linkage (connected components) merged entire
    batches through one borderline pair; average linkage refuses that chain while
    still allowing a genuine group whose members score unevenly.
    """
    count = matrix.shape[0]
    if count == 0:
        return []

    similarity = np.array(matrix, dtype=np.float64, copy=True)
    np.fill_diagonal(similarity, -np.inf)
    sizes = np.ones(count, dtype=np.float64)
    active = np.ones(count, dtype=bool)
    members: list[list[int]] = [[index] for index in range(count)]

    for _ in range(count - 1):
        candidates = np.where(active[:, None] & active[None, :], similarity, -np.inf)
        position = int(np.argmax(candidates))
        left, right = divmod(position, count)
        best = candidates[left, right]
        if not np.isfinite(best) or best < threshold:
            break

        if left > right:
            left, right = right, left
        total = sizes[left] + sizes[right]
        similarity[left, :] = (
            sizes[left] * similarity[left, :] + sizes[right] * similarity[right, :]
        ) / total
        similarity[:, left] = similarity[left, :]
        similarity[left, left] = -np.inf
        sizes[left] = total
        members[left] = members[left] + members[right]
        active[right] = False
        similarity[right, :] = -np.inf
        similarity[:, right] = -np.inf

    return [members[index] for index in range(count) if active[index]]


def build_groups(
    documents: Sequence[MapDocument],
    matrix: np.ndarray,
    config: ClusterConfig = DEFAULT_CLUSTER_CONFIG,
) -> tuple[tuple[Group, ...], tuple[str, ...]]:
    """Group documents by base-map similarity using the configured linkage.

    Average linkage is the default; see :class:`ClusterConfig` for the measured
    reason (single linkage produced one giant group for a 118-document batch).
    Documents that end up alone stay ungrouped rather than being forced together.
    """
    count = len(documents)

    if config.linkage == "single":
        union = _UnionFind(count)
        for i in range(count):
            for j in range(i + 1, count):
                if matrix[i, j] >= config.link_threshold:
                    union.union(i, j)
        raw_clusters: list[list[int]] = []
        buckets: dict[int, list[int]] = {}
        for index in range(count):
            buckets.setdefault(union.find(index), []).append(index)
        raw_clusters = list(buckets.values())
    else:
        raw_clusters = _average_linkage_clusters(matrix, config.link_threshold)

    groups: list[Group] = []
    ungrouped: list[str] = []
    group_id = 1

    for members in sorted(raw_clusters, key=lambda items: (-len(items), items[0])):
        if len(members) < 2:
            ungrouped.extend(documents[index].key for index in members)
            continue

        # Only linked pairs describe the group's strength. A pair inside the same
        # group that never reached the threshold is a linkage artifact, not
        # evidence of a weak group.
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

    # Pairs in the "needs review" band are surfaced, never merged automatically.
    candidates = tuple(
        CandidatePair(a_key=edge.a_key, b_key=edge.b_key, score=edge.score)
        for edge in edges
        if not edge.skipped
        and config.candidate_threshold <= edge.score < config.link_threshold
    )

    logger.info(
        "Pengelompokan selesai: %d kelompok, %d belum, %d dilewati, "
        "%d pasangan kandidat (perlu pemeriksaan)",
        len(groups),
        len(ungrouped) + len(unusable),
        skipped,
        len(candidates),
    )
    return ClusteringResult(
        groups=groups,
        ungrouped=tuple(list(ungrouped) + [doc.key for doc in unusable]),
        edges=edges,
        skipped_pairs=skipped,
        candidates=candidates,
    )


def clustering_summary(result: ClusteringResult, names: dict[str, str] | None = None) -> str:
    names = names or {}
    lines = [
        f"Jumlah kelompok: {result.total_groups}",
        f"Dokumen berkelompok: {result.grouped_count}",
        f"Belum terkelompok: {result.ungrouped_count}",
        f"Pasangan dilewati prefilter: {result.skipped_pairs}",
        f"Pasangan kandidat (perlu pemeriksaan): {result.candidate_count}",
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
