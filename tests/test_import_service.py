from pathlib import Path

import pytest

from core.pdf_reader import DocumentStatus
from services.import_service import (
    find_pdf_files,
    import_folder,
    inspect_files,
    summarize,
)


def test_find_pdf_files_sorted_naturally(tmp_path, temp_pdf):
    temp_pdf("SU_10.pdf", pages=1)
    temp_pdf("SU_2.pdf", pages=1)
    temp_pdf("SU_1.pdf", pages=1)
    (tmp_path / "catatan.txt").write_text("bukan pdf", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    temp_pdf("sub/SU_3.pdf", pages=1)

    found = [path.name for path in find_pdf_files(tmp_path)]
    assert found == ["SU_1.pdf", "SU_2.pdf", "SU_10.pdf"]

    recursive = [path.name for path in find_pdf_files(tmp_path, recursive=True)]
    assert "SU_3.pdf" in recursive


def test_find_pdf_files_rejects_missing_folder(tmp_path):
    with pytest.raises(NotADirectoryError):
        find_pdf_files(tmp_path / "tidak-ada")


def test_import_folder_mixes_valid_and_invalid(tmp_path, temp_pdf):
    temp_pdf("baik_1.pdf", pages=2)
    temp_pdf("baik_2.pdf", pages=1)
    (tmp_path / "rusak.pdf").write_bytes(b"%PDF-1.4 rusak")

    result = import_folder(tmp_path)

    assert result.total == 3
    assert result.valid_count == 2
    assert result.invalid_count == 1
    assert result.total_pages == 3
    assert not result.cancelled


def test_import_folder_reports_progress(tmp_path, temp_pdf):
    temp_pdf("a.pdf", pages=1)
    temp_pdf("b.pdf", pages=1)
    events: list[tuple[int, int, str]] = []

    import_folder(tmp_path, progress=lambda c, t, n: events.append((c, t, n)))

    assert [event[0] for event in events] == [1, 2]
    assert all(event[1] == 2 for event in events)


def test_import_folder_supports_cancellation(tmp_path, temp_pdf):
    for index in range(4):
        temp_pdf(f"file_{index}.pdf", pages=1)

    result = import_folder(tmp_path, should_cancel=lambda: True)

    assert result.cancelled
    assert result.total == 0


def test_import_continues_after_single_failure(tmp_path, temp_pdf):
    temp_pdf("valid.pdf", pages=1)
    (tmp_path / "rusak.pdf").write_bytes(b"x")

    result = import_folder(tmp_path)

    assert result.total == 2
    assert result.valid_count == 1
    failed = [doc for doc in result.documents if not doc.is_valid]
    assert len(failed) == 1
    assert failed[0].filename == "rusak.pdf"
    assert failed[0].status in (
        DocumentStatus.CORRUPT,
        DocumentStatus.UNREADABLE,
    )


def test_inspect_files_deduplicates_paths(temp_pdf):
    path = temp_pdf("satu.pdf", pages=1)
    result = inspect_files([path, path, path])
    assert result.total == 1


def test_summarize_counts():
    from core.pdf_reader import PdfInfo

    valid = PdfInfo(Path("a.pdf"), "a.pdf", 10, 0.0, 2, DocumentStatus.OK)
    invalid = PdfInfo(Path("b.pdf"), "b.pdf", 0, 0.0, 0, DocumentStatus.CORRUPT)
    assert summarize([valid, invalid]) == {"total": 2, "valid": 1, "invalid": 1}
