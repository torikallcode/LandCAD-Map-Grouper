"""SQLite schema and helpers (PRD section 11).

The database stores metadata and grouping results only. Feature descriptors stay
in cache files because the PRD explicitly allows files when that is more
efficient than storing full descriptor blobs in the database.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from config import PATHS
from utils.logging_utils import get_logger

logger = get_logger("db")

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    filename          TEXT NOT NULL,
    original_path     TEXT NOT NULL UNIQUE,
    file_hash         TEXT NOT NULL DEFAULT '',
    page_count        INTEGER NOT NULL DEFAULT 0,
    map_page          INTEGER,
    processing_status TEXT NOT NULL DEFAULT 'baru',
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS features (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id          INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    feature_data_path    TEXT NOT NULL,
    feature_count        INTEGER NOT NULL DEFAULT 0,
    preprocessing_version TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS similarity (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    document_a        INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    document_b        INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    similarity_score  REAL NOT NULL DEFAULT 0,
    alignment_quality REAL NOT NULL DEFAULT 0,
    inlier_count      INTEGER NOT NULL DEFAULT 0,
    match_count       INTEGER NOT NULL DEFAULT 0,
    review_status     TEXT NOT NULL DEFAULT 'perlu_verifikasi',
    UNIQUE (document_a, document_b)
);

CREATE TABLE IF NOT EXISTS groups (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    group_name          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    verification_status TEXT NOT NULL DEFAULT 'perlu_verifikasi'
);

CREATE TABLE IF NOT EXISTS group_members (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id    INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    UNIQUE (group_id, document_id)
);

CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(file_hash);
CREATE INDEX IF NOT EXISTS idx_group_members_group ON group_members(group_id);
"""

# v1 -> v2: PRD 10A reports "Jumlah pasangan cocok", which needs the feature
# match count alongside the inlier count.
MIGRATION_V2 = """
ALTER TABLE similarity ADD COLUMN match_count INTEGER NOT NULL DEFAULT 0;
"""


@dataclass(frozen=True)
class SavedGroup:
    name: str
    verified: bool
    paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class SavedEdge:
    path_a: str
    path_b: str
    score: float
    inlier_count: int = 0
    match_count: int = 0


@dataclass(frozen=True)
class SavedReview:
    """Grouping results read back from the database."""

    groups: tuple[SavedGroup, ...] = ()
    edges: tuple[SavedEdge, ...] = ()
    documents: dict[str, dict] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.groups

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(path for group in self.groups for path in group.paths)


def default_database_path() -> Path:
    return Path(PATHS.data) / "landcad.db"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_path_key(path: Path | str) -> str:
    """Path key matching ``PdfInfo.key`` so DB rows map back to documents.

    Windows is case-insensitive and both sides must agree, otherwise a saved
    group would not reattach to the scanned document.
    """
    return os.path.normcase(str(Path(path).resolve()))


@contextmanager
def connect(path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Open a connection with PRAGMAs suited to a desktop app."""
    target = Path(path) if path is not None else default_database_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(str(target))
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}


def _migrate(connection: sqlite3.Connection) -> None:
    """Bring an older database up to the current schema, preserving data."""
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])

    if version < 2:
        # A freshly created DB already has the column from SCHEMA, so only add it
        # when upgrading a v1 file.
        if "match_count" not in _columns(connection, "similarity"):
            connection.executescript(MIGRATION_V2)
            logger.info("Migrasi database v1 -> v2 (kolom match_count)")
        version = 2

    connection.execute(f"PRAGMA user_version = {version}")


def initialize(path: Path | str | None = None) -> Path:
    target = Path(path) if path is not None else default_database_path()
    with connect(target) as connection:
        connection.executescript(SCHEMA)
        _migrate(connection)
    return target


def schema_version(path: Path | str | None = None) -> int:
    with connect(path) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


# ------------------------------------------------------------------ documents
def upsert_document(
    connection: sqlite3.Connection,
    *,
    filename: str,
    original_path: str,
    file_hash: str = "",
    page_count: int = 0,
    map_page: int | None = None,
    processing_status: str = "baru",
) -> int:
    existing = connection.execute(
        "SELECT id FROM documents WHERE original_path = ?", (original_path,)
    ).fetchone()
    if existing is not None:
        connection.execute(
            """
            UPDATE documents
               SET filename = ?, file_hash = ?, page_count = ?, map_page = ?,
                   processing_status = ?
             WHERE id = ?
            """,
            (filename, file_hash, page_count, map_page, processing_status, existing["id"]),
        )
        return int(existing["id"])

    cursor = connection.execute(
        """
        INSERT INTO documents
            (filename, original_path, file_hash, page_count, map_page,
             processing_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (filename, original_path, file_hash, page_count, map_page,
         processing_status, utc_now()),
    )
    return int(cursor.lastrowid)


def list_documents(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(connection.execute("SELECT * FROM documents ORDER BY filename"))


# ------------------------------------------------------------------- features
def save_feature_reference(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    feature_data_path: str,
    feature_count: int,
    preprocessing_version: str = "",
) -> int:
    connection.execute(
        "DELETE FROM features WHERE document_id = ?", (document_id,)
    )
    cursor = connection.execute(
        """
        INSERT INTO features
            (document_id, feature_data_path, feature_count, preprocessing_version)
        VALUES (?, ?, ?, ?)
        """,
        (document_id, feature_data_path, feature_count, preprocessing_version),
    )
    return int(cursor.lastrowid)


# ----------------------------------------------------------------- similarity
def save_similarity(
    connection: sqlite3.Connection,
    *,
    document_a: int,
    document_b: int,
    similarity_score: float,
    alignment_quality: float = 0.0,
    inlier_count: int = 0,
    match_count: int = 0,
    review_status: str = "perlu_verifikasi",
) -> None:
    low, high = sorted((document_a, document_b))
    connection.execute(
        """
        INSERT INTO similarity
            (document_a, document_b, similarity_score, alignment_quality,
             inlier_count, match_count, review_status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(document_a, document_b) DO UPDATE SET
            similarity_score = excluded.similarity_score,
            alignment_quality = excluded.alignment_quality,
            inlier_count = excluded.inlier_count,
            match_count = excluded.match_count,
            review_status = excluded.review_status
        """,
        (
            low,
            high,
            similarity_score,
            alignment_quality,
            inlier_count,
            match_count,
            review_status,
        ),
    )


# --------------------------------------------------------------------- groups
def save_groups(
    connection: sqlite3.Connection,
    groups: list[tuple[str, str, list[int]]],
) -> None:
    """Replace all groups; ``groups`` is (name, verification_status, doc ids)."""
    connection.execute("DELETE FROM group_members")
    connection.execute("DELETE FROM groups")
    for name, verification, document_ids in groups:
        cursor = connection.execute(
            "INSERT INTO groups (group_name, created_at, verification_status) "
            "VALUES (?, ?, ?)",
            (name, utc_now(), verification),
        )
        group_id = int(cursor.lastrowid)
        for document_id in document_ids:
            connection.execute(
                "INSERT OR IGNORE INTO group_members (group_id, document_id) VALUES (?, ?)",
                (group_id, document_id),
            )


def list_groups(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        "SELECT * FROM groups ORDER BY group_name"
    ).fetchall()
    result = []
    for row in rows:
        members = connection.execute(
            "SELECT document_id FROM group_members WHERE group_id = ?",
            (row["id"],),
        ).fetchall()
        result.append(
            {
                "id": row["id"],
                "group_name": row["group_name"],
                "verification_status": row["verification_status"],
                "member_ids": [int(member["document_id"]) for member in members],
            }
        )
    return result


def document_ids_by_key(connection: sqlite3.Connection) -> dict[str, int]:
    """Map ``PdfInfo.key``-style path keys to database ids."""
    return {
        normalize_path_key(row["original_path"]): int(row["id"])
        for row in connection.execute("SELECT id, original_path FROM documents")
    }


def save_review_state(
    connection: sqlite3.Connection,
    *,
    groups: list[tuple[str, bool, list[int]]],
    ungrouped: list[int],
    edges: list[tuple[int, int, float, int, int]],
) -> None:
    """Persist a complete grouping result (PRD 11 tables).

    ``groups`` is ``(name, verified, document ids)``; ``edges`` is
    ``(document_a, document_b, score, inlier_count, match_count)``.

    Documents with no membership row are the ungrouped ones, so ``ungrouped`` is
    only used to keep the caller honest — it must not overlap the groups.
    """
    grouped_ids = {document_id for _, _, ids in groups for document_id in ids}
    overlap = grouped_ids & set(ungrouped)
    if overlap:
        raise ValueError(
            f"Dokumen tidak boleh berkelompok sekaligus belum terkelompok: {sorted(overlap)}"
        )

    connection.execute("DELETE FROM group_members")
    connection.execute("DELETE FROM groups")
    connection.execute("DELETE FROM similarity")

    for name, verified, document_ids in groups:
        cursor = connection.execute(
            "INSERT INTO groups (group_name, created_at, verification_status) "
            "VALUES (?, ?, ?)",
            (name, utc_now(), "terverifikasi" if verified else "perlu_verifikasi"),
        )
        group_id = int(cursor.lastrowid)
        for document_id in document_ids:
            connection.execute(
                "INSERT OR IGNORE INTO group_members (group_id, document_id) VALUES (?, ?)",
                (group_id, document_id),
            )

    for document_a, document_b, score, inlier_count, match_count in edges:
        save_similarity(
            connection,
            document_a=document_a,
            document_b=document_b,
            similarity_score=score,
            inlier_count=inlier_count,
            match_count=match_count,
        )


def load_review_state(connection: sqlite3.Connection) -> SavedReview:
    """Read back groups, membership and similarity edges."""
    documents = {
        normalize_path_key(row["original_path"]): {
            "id": int(row["id"]),
            "filename": row["filename"],
            "original_path": row["original_path"],
            "page_count": int(row["page_count"]),
            "map_page": row["map_page"],
        }
        for row in connection.execute("SELECT * FROM documents")
    }
    id_to_path = {meta["id"]: path for path, meta in documents.items()}

    groups: list[SavedGroup] = []
    for row in connection.execute("SELECT * FROM groups ORDER BY group_name"):
        member_rows = connection.execute(
            "SELECT document_id FROM group_members WHERE group_id = ? ORDER BY document_id",
            (row["id"],),
        ).fetchall()
        paths = tuple(
            id_to_path[int(member["document_id"])]
            for member in member_rows
            if int(member["document_id"]) in id_to_path
        )
        groups.append(
            SavedGroup(
                name=row["group_name"],
                verified=row["verification_status"] == "terverifikasi",
                paths=paths,
            )
        )

    edges: list[SavedEdge] = []
    for row in connection.execute("SELECT * FROM similarity"):
        path_a = id_to_path.get(int(row["document_a"]))
        path_b = id_to_path.get(int(row["document_b"]))
        if path_a is None or path_b is None:
            continue
        edges.append(
            SavedEdge(
                path_a=path_a,
                path_b=path_b,
                score=float(row["similarity_score"]),
                inlier_count=int(row["inlier_count"]),
                match_count=int(row["match_count"]),
            )
        )

    return SavedReview(groups=tuple(groups), edges=tuple(edges), documents=documents)


__all__ = [
    "SCHEMA",
    "SCHEMA_VERSION",
    "SavedEdge",
    "SavedGroup",
    "SavedReview",
    "connect",
    "default_database_path",
    "document_ids_by_key",
    "initialize",
    "list_documents",
    "list_groups",
    "load_review_state",
    "normalize_path_key",
    "save_feature_reference",
    "save_groups",
    "save_review_state",
    "save_similarity",
    "schema_version",
    "upsert_document",
    "utc_now",
]
