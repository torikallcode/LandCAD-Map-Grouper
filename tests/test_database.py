import sqlite3
from pathlib import Path

import pytest

from database import db


@pytest.fixture
def database(tmp_path) -> Path:
    return db.initialize(tmp_path / "test.db")


# ------------------------------------------------------------------- schema
def test_initialize_creates_all_tables(database):
    with db.connect(database) as connection:
        names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"documents", "features", "similarity", "groups", "group_members"} <= names


def test_schema_version_recorded(database):
    assert db.schema_version(database) == db.SCHEMA_VERSION


def test_initialize_is_idempotent(tmp_path):
    path = tmp_path / "twice.db"
    db.initialize(path)
    db.initialize(path)
    assert db.schema_version(path) == db.SCHEMA_VERSION


def test_foreign_keys_enforced(database):
    with db.connect(database) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_connect_rolls_back_on_error(database):
    with pytest.raises(sqlite3.IntegrityError):
        with db.connect(database) as connection:
            connection.execute(
                "INSERT INTO group_members (group_id, document_id) VALUES (999, 999)"
            )
    with db.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM group_members").fetchone()[0] == 0


# ---------------------------------------------------------------- documents
def test_upsert_document_inserts(database):
    with db.connect(database) as connection:
        document_id = db.upsert_document(
            connection,
            filename="SU_057.pdf",
            original_path="C:/data/SU_057.pdf",
            file_hash="abc",
            page_count=2,
            map_page=1,
        )
    assert document_id > 0

    with db.connect(database) as connection:
        rows = db.list_documents(connection)
    assert len(rows) == 1
    assert rows[0]["filename"] == "SU_057.pdf"
    assert rows[0]["map_page"] == 1


def test_upsert_document_updates_existing(database):
    with db.connect(database) as connection:
        first = db.upsert_document(
            connection, filename="a.pdf", original_path="C:/a.pdf", page_count=2
        )
        second = db.upsert_document(
            connection,
            filename="a.pdf",
            original_path="C:/a.pdf",
            page_count=3,
            map_page=1,
            processing_status="dianalisis",
        )
    assert first == second

    with db.connect(database) as connection:
        rows = db.list_documents(connection)
    assert len(rows) == 1
    assert rows[0]["page_count"] == 3
    assert rows[0]["processing_status"] == "dianalisis"


def test_original_path_is_unique(database):
    with db.connect(database) as connection:
        db.upsert_document(connection, filename="a.pdf", original_path="C:/same.pdf")
        db.upsert_document(connection, filename="b.pdf", original_path="C:/same.pdf")
        count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert count == 1


def test_list_documents_sorted_by_filename(database):
    with db.connect(database) as connection:
        for name in ("SU_10.pdf", "SU_02.pdf", "SU_01.pdf"):
            db.upsert_document(
                connection, filename=name, original_path=f"C:/{name}"
            )
    with db.connect(database) as connection:
        names = [row["filename"] for row in db.list_documents(connection)]
    assert names == ["SU_01.pdf", "SU_02.pdf", "SU_10.pdf"]


# ------------------------------------------------------------------ features
def test_save_feature_reference_replaces_previous(database):
    with db.connect(database) as connection:
        document_id = db.upsert_document(
            connection, filename="a.pdf", original_path="C:/a.pdf"
        )
        db.save_feature_reference(
            connection,
            document_id=document_id,
            feature_data_path="cache/a.npz",
            feature_count=2000,
            preprocessing_version="normalize-v2",
        )
        db.save_feature_reference(
            connection,
            document_id=document_id,
            feature_data_path="cache/b.npz",
            feature_count=1500,
        )
        rows = connection.execute("SELECT * FROM features").fetchall()
    assert len(rows) == 1
    assert rows[0]["feature_data_path"] == "cache/b.npz"
    assert rows[0]["feature_count"] == 1500


# ---------------------------------------------------------------- similarity
def test_save_similarity_upserts(database):
    with db.connect(database) as connection:
        a = db.upsert_document(connection, filename="a.pdf", original_path="C:/a.pdf")
        b = db.upsert_document(connection, filename="b.pdf", original_path="C:/b.pdf")
        db.save_similarity(
            connection, document_a=a, document_b=b, similarity_score=70.0
        )
        db.save_similarity(
            connection,
            document_a=b,
            document_b=a,
            similarity_score=84.4,
            alignment_quality=0.88,
            inlier_count=341,
        )
        rows = connection.execute("SELECT * FROM similarity").fetchall()
    assert len(rows) == 1
    assert rows[0]["similarity_score"] == pytest.approx(84.4)
    assert rows[0]["inlier_count"] == 341


def test_similarity_stores_ordered_pair(database):
    with db.connect(database) as connection:
        a = db.upsert_document(connection, filename="a.pdf", original_path="C:/a.pdf")
        b = db.upsert_document(connection, filename="b.pdf", original_path="C:/b.pdf")
        db.save_similarity(
            connection, document_a=b, document_b=a, similarity_score=50.0
        )
        row = connection.execute("SELECT * FROM similarity").fetchone()
    assert row["document_a"] == min(a, b)
    assert row["document_b"] == max(a, b)


# -------------------------------------------------------------------- groups
def test_save_groups_replaces_previous(database):
    with db.connect(database) as connection:
        a = db.upsert_document(connection, filename="a.pdf", original_path="C:/a.pdf")
        b = db.upsert_document(connection, filename="b.pdf", original_path="C:/b.pdf")
        db.save_groups(connection, [("KELOMPOK_001", "perlu_verifikasi", [a, b])])

        db.save_groups(connection, [("KELOMPOK_001", "terverifikasi", [a])])
        groups = db.list_groups(connection)

    assert len(groups) == 1
    assert groups[0]["verification_status"] == "terverifikasi"
    assert groups[0]["member_ids"] == [a]


def test_list_groups_reports_members(database):
    with db.connect(database) as connection:
        a = db.upsert_document(connection, filename="a.pdf", original_path="C:/a.pdf")
        b = db.upsert_document(connection, filename="b.pdf", original_path="C:/b.pdf")
        db.save_groups(
            connection,
            [
                ("KELOMPOK_001", "perlu_verifikasi", [a, b]),
                ("KELOMPOK_002", "terverifikasi", [a]),
            ],
        )
        groups = db.list_groups(connection)

    names = [group["group_name"] for group in groups]
    assert names == ["KELOMPOK_001", "KELOMPOK_002"]
    assert len(groups[0]["member_ids"]) == 2


def test_duplicate_membership_ignored(database):
    with db.connect(database) as connection:
        a = db.upsert_document(connection, filename="a.pdf", original_path="C:/a.pdf")
        db.save_groups(connection, [("G", "x", [a, a, a])])
        count = connection.execute("SELECT COUNT(*) FROM group_members").fetchone()[0]
    assert count == 1


def test_group_member_requires_existing_document(database):
    with db.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            db.save_groups(connection, [("G", "x", [9999])])


# ------------------------------------------------------------------ utility
def test_utc_now_is_isoformat():
    value = db.utc_now()
    assert "T" in value
    assert value.endswith("+00:00")


def test_default_database_path_under_data():
    assert db.default_database_path().name == "landcad.db"
