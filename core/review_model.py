from __future__ import annotations

from dataclasses import dataclass, field, replace

from core.clustering_engine import ClusteringResult, SimilarityEdge
from utils.logging_utils import get_logger

logger = get_logger("review_model")

REVIEW_VERSION = "review-v1"
UNGROUPED_ID = 0


def _group_name(group_id: int) -> str:
    return f"KELOMPOK_{group_id:03d}"


@dataclass(frozen=True)
class ReviewedGroup:
    """A group as the user currently sees it (after manual corrections)."""

    group_id: int
    member_keys: tuple[str, ...] = ()
    confidence: str = "Rendah"
    min_pair_score: float = 0.0
    verified: bool = False
    note: str = ""

    @property
    def name(self) -> str:
        return _group_name(self.group_id)

    @property
    def size(self) -> int:
        return len(self.member_keys)

    @property
    def is_empty(self) -> bool:
        return not self.member_keys

    @property
    def status_label(self) -> str:
        return "Terverifikasi" if self.verified else "Perlu verifikasi"

    @property
    def needs_review(self) -> bool:
        return not self.verified


@dataclass(frozen=True)
class ReviewState:
    """Editable grouping state produced from a clustering result.

    Manual corrections never touch the PDFs; they only change which group a
    document belongs to before export.
    """

    groups: tuple[ReviewedGroup, ...] = ()
    ungrouped: tuple[str, ...] = ()
    edges: tuple[SimilarityEdge, ...] = ()
    names: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------ factories
    @classmethod
    def from_clustering(
        cls,
        clustering: ClusteringResult,
        names: dict[str, str] | None = None,
    ) -> "ReviewState":
        groups = tuple(
            ReviewedGroup(
                group_id=group.group_id,
                member_keys=tuple(group.member_keys),
                confidence=group.confidence,
                min_pair_score=group.min_pair_score,
                verified=False,
            )
            for group in clustering.groups
        )
        return cls(
            groups=groups,
            ungrouped=tuple(clustering.ungrouped),
            edges=tuple(clustering.edges),
            names=dict(names or {}),
        )

    # ------------------------------------------------------------- lookups
    def name_of(self, key: str) -> str:
        return self.names.get(key, key)

    def group_of(self, key: str) -> ReviewedGroup | None:
        for group in self.groups:
            if key in group.member_keys:
                return group
        return None

    def group_by_id(self, group_id: int) -> ReviewedGroup | None:
        return next((g for g in self.groups if g.group_id == group_id), None)

    def all_keys(self) -> tuple[str, ...]:
        keys = [key for group in self.groups for key in group.member_keys]
        keys.extend(self.ungrouped)
        return tuple(keys)

    @property
    def grouped_count(self) -> int:
        return sum(group.size for group in self.groups)

    @property
    def total_groups(self) -> int:
        return len(self.groups)

    @property
    def verified_count(self) -> int:
        return sum(1 for group in self.groups if group.verified)

    @property
    def needs_review_count(self) -> int:
        return sum(1 for group in self.groups if not group.verified)

    def score_between(self, left: str, right: str) -> float:
        for edge in self.edges:
            if {edge.a_key, edge.b_key} == {left, right}:
                return edge.score
        return 0.0

    def matches_between(self, left: str, right: str) -> int:
        for edge in self.edges:
            if {edge.a_key, edge.b_key} == {left, right}:
                return edge.match_count
        return 0

    def member_scores(self, group: ReviewedGroup) -> dict[str, float]:
        """Best score of each member against the rest of its own group."""
        scores: dict[str, float] = {}
        for key in group.member_keys:
            values = [
                self.score_between(key, other)
                for other in group.member_keys
                if other != key
            ]
            scores[key] = max(values) if values else 0.0
        return scores

    def match_count_of(self, key: str) -> int:
        """Best match count of this document against any other document."""
        values = [
            self.matches_between(key, other)
            for other in self.all_keys()
            if other != key
        ]
        return max(values) if values else 0

    # ------------------------------------------------------------ mutations
    def move_document(self, key: str, target_group_id: int | None) -> "ReviewState":
        """Move ``key`` into another group, or to the ungrouped bucket if None."""
        if key not in self.all_keys():
            raise KeyError(f"Dokumen tidak dikenal: {key}")

        groups = []
        moved = target_group_id not in (None, UNGROUPED_ID)
        target_found = False

        for group in self.groups:
            members = tuple(member for member in group.member_keys if member != key)
            if group.group_id == target_group_id:
                target_found = True
                if key not in members:
                    members = members + (key,)
            groups.append(
                replace(
                    group,
                    member_keys=members,
                    verified=False,
                    confidence=self._confidence_for(members),
                    min_pair_score=self._min_score_for(members),
                )
            )

        if moved and not target_found:
            raise KeyError(f"Kelompok tidak dikenal: {target_group_id}")

        ungrouped = tuple(member for member in self.ungrouped if member != key)
        if not moved:
            ungrouped = ungrouped + (key,)

        # Drop groups that lost all their members.
        groups = [group for group in groups if group.member_keys]
        return self._rebuild(groups, ungrouped)

    def merge_groups(self, source_ids: tuple[int, ...], target_id: int) -> "ReviewState":
        """Merge ``source_ids`` into ``target_id``; the target keeps its id."""
        target = self.group_by_id(target_id)
        if target is None:
            raise KeyError(f"Kelompok tujuan tidak dikenal: {target_id}")

        source_ids = tuple(sid for sid in source_ids if sid != target_id)
        sources = [self.group_by_id(sid) for sid in source_ids]
        missing = [sid for sid, group in zip(source_ids, sources) if group is None]
        if missing:
            raise KeyError(f"Kelompok tidak dikenal: {missing}")

        merged_keys = list(target.member_keys)
        for group in sources:
            assert group is not None
            for key in group.member_keys:
                if key not in merged_keys:
                    merged_keys.append(key)

        groups = [
            replace(
                group,
                member_keys=tuple(merged_keys),
                verified=False,
                confidence=self._confidence_for(tuple(merged_keys)),
                min_pair_score=self._min_score_for(tuple(merged_keys)),
            )
            if group.group_id == target_id
            else group
            for group in self.groups
            if group.group_id not in source_ids
        ]
        return self._rebuild(groups, self.ungrouped)

    def split_document(self, key: str) -> "ReviewState":
        """Detach ``key`` from its group into the ungrouped bucket."""
        return self.move_document(key, None)

    def create_group(self, keys: tuple[str, ...]) -> "ReviewState":
        """Form a new group from explicitly chosen documents."""
        keys = tuple(dict.fromkeys(keys))
        if not keys:
            raise ValueError("Daftar dokumen kosong.")

        next_id = max((group.group_id for group in self.groups), default=0) + 1
        groups: list[ReviewedGroup] = []
        chosen = set(keys)

        for group in self.groups:
            remaining = tuple(member for member in group.member_keys if member not in chosen)
            if remaining:
                groups.append(
                    replace(
                        group,
                        member_keys=remaining,
                        verified=False,
                        confidence=self._confidence_for(remaining),
                        min_pair_score=self._min_score_for(remaining),
                    )
                )

        groups.append(
            ReviewedGroup(
                group_id=next_id,
                member_keys=keys,
                confidence=self._confidence_for(keys),
                min_pair_score=self._min_score_for(keys),
                verified=False,
            )
        )
        ungrouped = tuple(member for member in self.ungrouped if member not in chosen)
        return self._rebuild(groups, ungrouped)

    def set_verified(self, group_id: int, verified: bool) -> "ReviewState":
        group = self.group_by_id(group_id)
        if group is None:
            raise KeyError(f"Kelompok tidak dikenal: {group_id}")
        groups = [
            replace(item, verified=verified) if item.group_id == group_id else item
            for item in self.groups
        ]
        return self._rebuild(groups, self.ungrouped)

    def verify_all(self) -> "ReviewState":
        return self._rebuild(
            [replace(group, verified=True) for group in self.groups], self.ungrouped
        )

    def set_note(self, group_id: int, note: str) -> "ReviewState":
        if self.group_by_id(group_id) is None:
            raise KeyError(f"Kelompok tidak dikenal: {group_id}")
        groups = [
            replace(item, note=note) if item.group_id == group_id else item
            for item in self.groups
        ]
        return self._rebuild(groups, self.ungrouped)

    def reset(self, clustering: ClusteringResult) -> "ReviewState":
        return ReviewState.from_clustering(clustering, self.names)

    # ------------------------------------------------------------ internals
    def _rebuild(
        self, groups: list[ReviewedGroup], ungrouped: tuple[str, ...]
    ) -> "ReviewState":
        ordered = sorted(groups, key=lambda item: item.group_id)
        return ReviewState(
            groups=tuple(ordered),
            ungrouped=ungrouped,
            edges=self.edges,
            names=self.names,
        )

    def _confidence_for(self, members: tuple[str, ...]) -> str:
        if len(members) < 2:
            return "Rendah"
        scores = [
            self.score_between(a, b)
            for index, a in enumerate(members)
            for b in members[index + 1:]
        ]
        linked = [score for score in scores if score >= 65.0]
        if not linked:
            return "Rendah"
        minimum = min(linked)
        if minimum >= 85.0:
            return "Tinggi"
        return "Sedang"

    def _min_score_for(self, members: tuple[str, ...]) -> float:
        if len(members) < 2:
            return 0.0
        scores = [
            self.score_between(a, b)
            for index, a in enumerate(members)
            for b in members[index + 1:]
        ]
        linked = [score for score in scores if score >= 65.0]
        return round(min(linked), 1) if linked else 0.0


__all__ = [
    "REVIEW_VERSION",
    "ReviewedGroup",
    "ReviewState",
    "UNGROUPED_ID",
]
