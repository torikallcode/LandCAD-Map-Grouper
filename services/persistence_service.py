from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.clustering_engine import SimilarityEdge
from core.pdf_reader import DocumentStatus, PdfInfo
from core.review_model import ReviewState, ReviewedGroup
from database import db
from utils.logging_utils import get_logger

logger = get_logger("persistence_service")


@dataclass(frozen=True)
class RestoredReview:
    """Grouping result loaded from the database."""

    state: ReviewState
    documents: dict[str, PdfInfo] = field(default_factory=dict)
    missing_paths: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return self.state.total_groups == 0

    def paths(self) -> tuple[str, ...]:
        return tuple(self.documents)


def save_review(
    db_path: Path | str,
    state: ReviewState,
    documents: dict[str, PdfInfo],
) -> int:
    """Write groups, membership and similarity edges to SQLite.

    Returns the number of groups written. Failure is logged, never raised: losing
    persistence must not destroy a review the user just finished.
    """
    if state.total_groups == 0:
        return 0

    try:
        with db.connect(db_path) as connection:
            key_to_id = _ensure_documents(connection, state, documents)
            groups = [
                (
                    group.name,
                    group.verified,
                    [
                        key_to_id[key]
                        for key in group.member_keys
                        if key in key_to_id
                    ],
                )
                for group in state.groups
            ]
            ungrouped = [key_to_id[key] for key in state.ungrouped if key in key_to_id]
            edges = [
                (
                    key_to_id[edge.a_key],
                    key_to_id[edge.b_key],
                    edge.score,
                    edge.inlier_count,
                    edge.match_count,
                )
                for edge in state.edges
                if not edge.skipped
                and edge.a_key in key_to_id
                and edge.b_key in key_to_id
            ]

            db.save_review_state(
                connection, groups=groups, ungrouped=ungrouped, edges=edges
            )
        logger.info("Hasil pengelompokan disimpan: %d kelompok", len(groups))
        return len(groups)
    except Exception as exc:  # noqa: BLE001 - persistence must never crash the UI
        logger.warning("Gagal menyimpan hasil pengelompokan: %s", exc)
        return 0


def _ensure_documents(
    connection, state: ReviewState, documents: dict[str, PdfInfo]
) -> dict[str, int]:
    """Make sure every document of the review has a row, then map key -> id."""
    for key in state.all_keys():
        info = documents.get(key)
        filename = info.filename if info is not None else state.name_of(key)
        path = info.path if info is not None else Path(key)
        page_count = info.page_count if info is not None else 0
        status = "dianalisis" if info is not None and info.is_valid else "gagal"
        db.upsert_document(
            connection,
            filename=filename,
            original_path=str(Path(path).resolve()),
            page_count=page_count,
            processing_status=status,
        )

    # Upsert normalises the path through the same rule as ``PdfInfo.key``.
    return db.document_ids_by_key(connection)


def load_review(
    db_path: Path | str,
    only_within: Path | None = None,
) -> RestoredReview | None:
    """Read a saved grouping result.

    ``only_within`` restricts the result to documents inside that folder, so a
    review from another batch is not offered to the user by mistake.
    """
    try:
        with db.connect(db_path) as connection:
            saved = db.load_review_state(connection)
    except Exception as exc:  # noqa: BLE001 - a broken DB must not block startup
        logger.warning("Gagal membaca hasil tersimpan: %s", exc)
        return None

    if saved.is_empty:
        return None

    documents: dict[str, PdfInfo] = {}
    missing: list[str] = []
    for key, meta in saved.documents.items():
        path = Path(meta["original_path"])
        if only_within is not None and not _is_within(path, only_within):
            continue
        if not path.exists():
            missing.append(key)
            continue
        documents[key] = _pdf_info_from_row(path, meta)

    groups: list[ReviewedGroup] = []
    for index, group in enumerate(saved.groups, start=1):
        keys = tuple(key for key in group.paths if key in documents)
        if len(keys) < 2:
            continue
        groups.append(
            ReviewedGroup(
                group_id=index,
                member_keys=keys,
                confidence=_confidence_from_keys(keys, saved),
                min_pair_score=_min_score(keys, saved),
                verified=group.verified,
            )
        )

    grouped = {key for group in groups for key in group.member_keys}
    ungrouped = tuple(key for key in documents if key not in grouped)

    edges = tuple(
        SimilarityEdge(
            a_key=edge.path_a,
            b_key=edge.path_b,
            score=edge.score,
            match_count=edge.match_count,
            inlier_count=edge.inlier_count,
        )
        for edge in saved.edges
        if edge.path_a in documents and edge.path_b in documents
    )

    if not groups and not ungrouped:
        return None

    state = ReviewState(
        groups=tuple(groups),
        ungrouped=ungrouped,
        edges=edges,
        names={key: info.filename for key, info in documents.items()},
    )
    logger.info(
        "Hasil tersimpan dimuat: %d kelompok, %d belum terkelompok",
        len(groups),
        len(ungrouped),
    )
    return RestoredReview(
        state=state, documents=documents, missing_paths=tuple(missing)
    )


def _pdf_info_from_row(path: Path, meta: dict) -> PdfInfo:
    """Rebuild ``PdfInfo`` without reopening the PDF (fast startup path)."""
    try:
        stat = path.stat()
    except OSError:
        stat = None

    page_count = int(meta.get("page_count") or 0)
    status = DocumentStatus.OK if page_count > 0 else DocumentStatus.EMPTY
    return PdfInfo(
        path=path,
        filename=str(meta.get("filename") or path.name),
        size_bytes=stat.st_size if stat is not None else 0,
        modified_at=stat.st_mtime if stat is not None else 0.0,
        page_count=page_count,
        status=status,
    )


def _is_within(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except (ValueError, OSError):
        return False


def _min_score(keys: tuple[str, ...], saved: db.SavedReview) -> float:
    scores = [
        edge.score
        for edge in saved.edges
        if {edge.path_a, edge.path_b} <= set(keys)
    ]
    return round(min(scores), 1) if scores else 0.0


def _confidence_from_keys(keys: tuple[str, ...], saved: db.SavedReview) -> str:
    scores = [
        edge.score
        for edge in saved.edges
        if {edge.path_a, edge.path_b} <= set(keys) and edge.score >= 65.0
    ]
    if not scores:
        return "Rendah"
    return "Tinggi" if min(scores) >= 85.0 else "Sedang"


def clear_review(db_path: Path | str) -> None:
    """Remove saved groups and edges (documents are kept)."""
    try:
        with db.connect(db_path) as connection:
            connection.execute("DELETE FROM group_members")
            connection.execute("DELETE FROM groups")
            connection.execute("DELETE FROM similarity")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gagal menghapus hasil tersimpan: %s", exc)


__all__ = [
    "RestoredReview",
    "clear_review",
    "load_review",
    "save_review",
]
