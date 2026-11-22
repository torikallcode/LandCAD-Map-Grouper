"""Persistence of grouping results (PRD 11 tables) and schema migration."""

import sqlite3
from pathlib import Path

import pytest

from core.clustering_engine import ClusteringResult, Group, SimilarityEdge
from core.pdf_reader import DocumentStatus, PdfInfo
from core.review_model import ReviewState
from database import db
from services.persistence_service import (
    clear_review,
    load_review,
    save_review,
)


@pytest.fixture
def database(tmp_path) -> Path:
    return db.initialize(tmp_path / "test.db")


def _info(tmp_path: Path, name: str, pages: int = 2) -> PdfInfo:
    path = tmp_path / "docs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4\n%" + name.encode())
    return PdfInfo(
        path=path,
        filename=name,
        size_bytes=path.stat().st_size,
        modified_at=0.0,
        page_count=pages,
        status=DocumentStatus.OK,
    )


def _state(documents: list[PdfInfo], verified: bool = False) -> ReviewState:
    keys = [info.key for info in documents]
    clustering = ClusteringResult(
        groups=(
            Group(
                group_id=1,
                member_keys=tuple(keys[:2]),
                confidence="Sedang",
                min_pair_score=84.4,
            ),
        ),
        ungrouped=tuple(keys[2:]),
        edges=(
            SimilarityEdge(
                a_key=keys[0], b_key=keys[1], score=84.4,
                match_count=463, inlier_count=341,
            ),
        ),
    )
    state = ReviewState.from_clustering(
        clustering, {info.key: info.filename for info in documents}
    )
    return state.set_verified(1, True) if verified else state


def _doc_map(documents: list[PdfInfo]) -> dict[str, PdfInfo]:
    return {info.key: info for info in documents}


# ------------------------------------------------------------------ migration
def test_schema_version_is_two(database):
    assert db.SCHEMA_VERSION == 2
    assert db.schema_version(database) == 2


def test_similarity_has_match_count_column(database):
    with db.connect(database) as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(similarity)")}
    assert "match_count" in columns


def test_v1_database_is_migrated_and_data_preserved(tmp_path):
    """An old database must upgrade in place without losing documents."""
    path = tmp_path / "old.db"
    old_schema = db.SCHEMA.replace(
        "    match_count       INTEGER NOT NULL DEFAULT 0,\n", ""
    )
    connection = sqlite3.connect(str(path))
    connection.executescript(old_schema)
    connection.execute("PRAGMA user_version = 1")
    connection.execute(
        "INSERT INTO documents (filename, original_path, created_at) VALUES (?, ?, ?)",
        ("lama.pdf", "C:/lama.pdf", "2024-01-01T00:00:00+00:00"),
    )
    connection.commit()
    connection.close()

    db.initialize(path)

    assert db.schema_version(path) == 2
    with db.connect(path) as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(similarity)")}
        names = [row["filename"] for row in db.list_documents(conn)]
    assert "match_count" in columns
    assert names == ["lama.pdf"]


def test_initialize_is_idempotent_on_v2(tmp_path):
    path = db.initialize(tmp_path / "twice.db")
    db.initialize(path)
    assert db.schema_version(path) == 2


# ------------------------------------------------------------------- helpers
def test_normalize_path_key_matches_pdf_info_key(tmp_path):
    info = _info(tmp_path, "SU_057.pdf")
    assert db.normalize_path_key(info.path) == info.key


def test_normalize_path_key_is_case_insensitive(tmp_path):
    info = _info(tmp_path, "SU_057.pdf")
    upper = str(info.path).upper()
    assert db.normalize_path_key(upper) == db.normalize_path_key(info.path)


# ---------------------------------------------------------------- save/load
def test_save_review_writes_groups_and_members(database, tmp_path):
    documents = [_info(tmp_path, n) for n in ("SU_057.pdf", "SU_058.pdf", "SU_060.pdf")]
    written = save_review(database, _state(documents), _doc_map(documents))

    assert written == 1
    with db.connect(database) as connection:
        assert len(db.list_groups(connection)) == 1
        members = connection.execute("SELECT COUNT(*) FROM group_members").fetchone()[0]
    assert members == 2


def test_save_review_persists_similarity_edges(database, tmp_path):
    documents = [_info(tmp_path, n) for n in ("SU_057.pdf", "SU_058.pdf", "SU_060.pdf")]
    save_review(database, _state(documents), _doc_map(documents))

    with db.connect(database) as connection:
        row = connection.execute("SELECT * FROM similarity").fetchone()
    assert row["similarity_score"] == pytest.approx(84.4)
    assert row["inlier_count"] == 341
    assert row["match_count"] == 463


def test_save_review_keeps_verification_status(database, tmp_path):
    documents = [_info(tmp_path, n) for n in ("SU_057.pdf", "SU_058.pdf", "SU_060.pdf")]
    save_review(database, _state(documents, verified=True), _doc_map(documents))

    with db.connect(database) as connection:
        assert db.list_groups(connection)[0]["verification_status"] == "terverifikasi"


def test_save_review_ignores_empty_state(database, tmp_path):
    documents = [_info(tmp_path, "a.pdf")]
    assert save_review(database, ReviewState(), _doc_map(documents)) == 0


def test_save_review_replaces_previous_result(database, tmp_path):
    documents = [_info(tmp_path, n) for n in ("SU_057.pdf", "SU_058.pdf", "SU_060.pdf")]
    save_review(database, _state(documents), _doc_map(documents))
    save_review(database, _state(documents), _doc_map(documents))

    with db.connect(database) as connection:
        assert len(db.list_groups(connection)) == 1
        assert connection.execute("SELECT COUNT(*) FROM similarity").fetchone()[0] == 1


def test_save_review_survives_broken_database_path(tmp_path):
    documents = [_info(tmp_path, "a.pdf"), _info(tmp_path, "b.pdf")]
    state = _state(documents)
    # A directory cannot be opened as a SQLite file.
    broken = tmp_path / "not-a-db"
    broken.mkdir()
    assert save_review(broken, state, _doc_map(documents)) == 0


# ---------------------------------------------------------------------- load
def test_load_review_round_trip(database, tmp_path):
    documents = [_info(tmp_path, n) for n in ("SU_057.pdf", "SU_058.pdf", "SU_060.pdf")]
    state = _state(documents, verified=True)
    save_review(database, state, _doc_map(documents))

    restored = load_review(database)

    assert restored is not None
    assert restored.state.total_groups == 1
    assert restored.state.group_by_id(1).verified is True
    assert len(restored.state.ungrouped) == 1
    assert restored.state.edges[0].score == pytest.approx(84.4)
    assert restored.state.edges[0].match_count == 463


def test_load_review_returns_none_when_nothing_saved(database):
    assert load_review(database) is None


def test_load_review_restores_documents_without_opening_pdfs(database, tmp_path):
    documents = [_info(tmp_path, n, pages=3) for n in ("a.pdf", "b.pdf")]
    save_review(database, _state(documents), _doc_map(documents))

    restored = load_review(database)
    assert restored is not None
    for info in restored.documents.values():
        assert info.page_count == 3
        assert info.is_valid


def test_load_review_skips_missing_files(database, tmp_path):
    documents = [_info(tmp_path, n) for n in ("a.pdf", "b.pdf", "c.pdf")]
    save_review(database, _state(documents), _doc_map(documents))

    documents[0].path.unlink()
    restored = load_review(database)

    assert restored is not None
    assert documents[0].key in restored.missing_paths
    # The group drops to one usable member and is therefore not offered.
    assert restored.state.total_groups == 0
    assert documents[1].key in restored.state.ungrouped


def test_load_review_filters_by_folder(database, tmp_path):
    inside = _info(tmp_path, "SU_057.pdf")
    other_dir = tmp_path / "lain"
    other_dir.mkdir()
    outside_path = other_dir / "SU_999.pdf"
    outside_path.write_bytes(b"%PDF-1.4\n")
    outside = PdfInfo(
        path=outside_path, filename="SU_999.pdf", size_bytes=10, modified_at=0.0,
        page_count=2, status=DocumentStatus.OK,
    )
    documents = [inside, outside, _info(tmp_path, "SU_058.pdf")]
    save_review(database, _state(documents), _doc_map(documents))

    restored = load_review(database, only_within=tmp_path / "docs")
    assert restored is not None
    assert outside.key not in restored.documents


def test_load_review_tolerates_broken_database(tmp_path):
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"bukan database sqlite")
    assert load_review(broken) is None


def test_clear_review_removes_groups(database, tmp_path):
    documents = [_info(tmp_path, n) for n in ("a.pdf", "b.pdf")]
    save_review(database, _state(documents), _doc_map(documents))

    clear_review(database)

    assert load_review(database) is None
    with db.connect(database) as connection:
        assert db.list_documents(connection)  # documents survive


# ------------------------------------------------------------------ integrity
def test_saved_group_membership_has_no_duplicates(database, tmp_path):
    documents = [_info(tmp_path, n) for n in ("a.pdf", "b.pdf", "c.pdf")]
    save_review(database, _state(documents), _doc_map(documents))

    with db.connect(database) as connection:
        rows = connection.execute(
            "SELECT group_id, COUNT(*) c FROM group_members GROUP BY group_id"
        ).fetchall()
    assert all(row["c"] == 2 for row in rows)


def test_save_review_rejects_grouped_and_ungrouped_overlap(database, tmp_path):
    documents = [_info(tmp_path, n) for n in ("a.pdf", "b.pdf")]
    key_to_id = {}
    with db.connect(database) as connection:
        for info in documents:
            document_id = db.upsert_document(
                connection,
                filename=info.filename,
                original_path=str(info.path.resolve()),
            )
            key_to_id[info.key] = document_id

        with pytest.raises(ValueError):
            db.save_review_state(
                connection,
                groups=[("G1", False, [key_to_id[documents[0].key]])],
                ungrouped=[key_to_id[documents[0].key]],
                edges=[],
            )
