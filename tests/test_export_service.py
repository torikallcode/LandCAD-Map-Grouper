import pytest

from core.clustering_engine import UNGROUPED_LABEL, ClusteringResult, Group, SimilarityEdge
from core.pdf_reader import DocumentStatus, PdfInfo
from core.review_model import ReviewState
from services.export_service import (
    REPORT_COLUMNS,
    ReportRow,
    build_report_rows,
    copy_documents_to_folders,
    export_report,
    extract_su_number,
    safe_folder_name,
    write_csv,
    write_xlsx,
)


@pytest.fixture
def fake_documents(tmp_path):
    """Three tiny stand-in PDFs so copying can be tested safely."""
    documents = {}
    for name in ("SU_057.pdf", "SU_058.pdf", "lainnya.pdf"):
        path = tmp_path / "source" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4\n%" + name.encode())
        info = PdfInfo(
            path=path,
            filename=name,
            size_bytes=path.stat().st_size,
            modified_at=0.0,
            page_count=2,
            status=DocumentStatus.OK,
        )
        documents[info.key] = info
    return documents


def _state(documents) -> ReviewState:
    keys = list(documents)
    clustering = ClusteringResult(
        groups=(
            Group(group_id=1, member_keys=(keys[0], keys[1]), confidence="Sedang",
                  min_pair_score=70.0),
        ),
        ungrouped=(keys[2],),
        edges=(
            SimilarityEdge(a_key=keys[0], b_key=keys[1], score=70.0, match_count=123),
        ),
    )
    names = {key: documents[key].filename for key in keys}
    return ReviewState.from_clustering(clustering, names)


# ------------------------------------------------------------------ SU number
@pytest.mark.parametrize(
    "filename,expected",
    [
        ("SU_057.pdf", "057"),
        ("SU_058.PDF", "058"),
        ("su_12.pdf", "12"),
        ("SU-099.pdf", "099"),
        ("dokumen.pdf", ""),
        ("SU_abc.pdf", ""),
        ("SU.pdf", ""),
    ],
)
def test_extract_su_number(filename, expected):
    assert extract_su_number(filename) == expected


# --------------------------------------------------------------------- rows
def test_report_rows_cover_every_document(fake_documents):
    rows = build_report_rows(_state(fake_documents), fake_documents)

    assert len(rows) == 3
    assert {row.filename for row in rows} == {"SU_057.pdf", "SU_058.pdf", "lainnya.pdf"}


def test_report_rows_have_prd_columns(fake_documents):
    row = build_report_rows(_state(fake_documents), fake_documents)[0]
    assert len(row.as_list()) == len(REPORT_COLUMNS)


def test_grouped_rows_use_group_id(fake_documents):
    rows = build_report_rows(_state(fake_documents), fake_documents)
    grouped = [row for row in rows if row.group_id == "KELOMPOK_001"]
    assert len(grouped) == 2
    assert all(row.status == "Perlu verifikasi" for row in grouped)


def test_ungrouped_rows_use_label(fake_documents):
    rows = build_report_rows(_state(fake_documents), fake_documents)
    ungrouped = [row for row in rows if row.group_id == UNGROUPED_LABEL]
    assert len(ungrouped) == 1
    assert ungrouped[0].similarity == 0.0


def test_verified_status_is_reported(fake_documents):
    state = _state(fake_documents).set_verified(1, True)
    rows = build_report_rows(state, fake_documents)
    verified = [row for row in rows if row.status == "Terverifikasi"]
    assert len(verified) == 2


def test_match_count_and_score_filled(fake_documents):
    rows = build_report_rows(_state(fake_documents), fake_documents)
    grouped = [row for row in rows if row.group_id == "KELOMPOK_001"]
    assert all(row.match_count == 123 for row in grouped)
    assert all(row.similarity == 70.0 for row in grouped)


def test_su_number_extracted_from_filename(fake_documents):
    rows = build_report_rows(_state(fake_documents), fake_documents)
    numbers = {row.filename: row.su_number for row in rows}
    assert numbers["SU_057.pdf"] == "057"
    assert numbers["lainnya.pdf"] == ""


# --------------------------------------------------------------------- files
def test_write_csv_creates_header_and_rows(fake_documents, tmp_path):
    rows = build_report_rows(_state(fake_documents), fake_documents)
    path = write_csv(rows, tmp_path / "lap.csv")

    assert path.exists()
    content = path.read_text(encoding="utf-8-sig").splitlines()
    assert content[0].split(";") == list(REPORT_COLUMNS)
    assert len(content) == len(rows) + 1


def test_write_xlsx_creates_workbook(fake_documents, tmp_path):
    rows = build_report_rows(_state(fake_documents), fake_documents)
    path = write_xlsx(rows, tmp_path / "lap.xlsx")

    assert path.exists()
    assert path.stat().st_size > 0

    from openpyxl import load_workbook

    sheet = load_workbook(path).active
    header = [cell.value for cell in sheet[1]]
    assert header == list(REPORT_COLUMNS)
    assert sheet.max_row == len(rows) + 1


def test_export_report_xlsx(fake_documents, tmp_path):
    result = export_report(_state(fake_documents), fake_documents, tmp_path, fmt="xlsx")
    assert not result.has_error
    assert result.report_path is not None
    assert result.report_path.suffix == ".xlsx"
    assert result.row_count == 3


def test_export_report_csv(fake_documents, tmp_path):
    result = export_report(_state(fake_documents), fake_documents, tmp_path, fmt="csv")
    assert not result.has_error
    assert result.report_path.suffix == ".csv"


def test_export_report_unknown_format(fake_documents, tmp_path):
    result = export_report(_state(fake_documents), fake_documents, tmp_path, fmt="pdf")
    assert result.has_error
    assert "Format tidak dikenal" in result.error


def test_export_report_without_rows(fake_documents, tmp_path):
    state = ReviewState()
    result = export_report(state, fake_documents, tmp_path)
    assert result.has_error


def test_export_timestamped_name(fake_documents, tmp_path):
    result = export_report(
        _state(fake_documents), fake_documents, tmp_path, timestamped=True
    )
    assert "_" in result.report_path.stem
    assert result.report_path.stem.startswith("laporan_pengelompokan_")


# -------------------------------------------------------------------- copying
def test_copy_creates_group_folders(fake_documents, tmp_path):
    result = copy_documents_to_folders(_state(fake_documents), fake_documents, tmp_path)

    assert not result.has_error
    assert result.copied_count == 3
    assert (tmp_path / "KELOMPOK_001" / "SU_057.pdf").exists()
    assert (tmp_path / "KELOMPOK_001" / "SU_058.pdf").exists()
    assert (tmp_path / f"{UNGROUPED_LABEL}" / "lainnya.pdf").exists()


def test_copy_never_moves_originals(fake_documents, tmp_path):
    state = _state(fake_documents)
    originals = {key: documents_info.path.read_bytes() for key, documents_info in fake_documents.items()}

    copy_documents_to_folders(state, fake_documents, tmp_path / "out")

    for key, payload in originals.items():
        assert fake_documents[key].path.exists()
        assert fake_documents[key].path.read_bytes() == payload


def test_copy_skips_existing_without_overwrite(fake_documents, tmp_path):
    state = _state(fake_documents)
    copy_documents_to_folders(state, fake_documents, tmp_path)

    second = copy_documents_to_folders(state, fake_documents, tmp_path)
    assert second.copied_count == 0
    assert len(second.skipped) == 3


def test_copy_overwrites_when_requested(fake_documents, tmp_path):
    state = _state(fake_documents)
    copy_documents_to_folders(state, fake_documents, tmp_path)

    second = copy_documents_to_folders(state, fake_documents, tmp_path, overwrite=True)
    assert second.copied_count == 3


def test_copy_reports_progress(fake_documents, tmp_path):
    events = []
    copy_documents_to_folders(
        _state(fake_documents),
        fake_documents,
        tmp_path,
        progress=lambda c, t, name: events.append((c, t)),
    )
    assert events[-1] == (3, 3)


def test_copy_uses_sanitised_folder_names(fake_documents, tmp_path):
    keys = list(fake_documents)
    clustering = ClusteringResult(
        groups=(Group(group_id=1, member_keys=(keys[0], keys[1])),),
        ungrouped=(keys[2],),
    )
    state = ReviewState.from_clustering(
        clustering, {key: fake_documents[key].filename for key in keys}
    )
    state = state.set_note(1, "x")

    result = copy_documents_to_folders(state, fake_documents, tmp_path)
    assert result.copied_count == 3


def test_safe_folder_name_sanitises():
    assert safe_folder_name("KELOMPOK_001") == "KELOMPOK_001"
    assert "/" not in safe_folder_name("a/b")
    assert safe_folder_name("") == UNGROUPED_LABEL


def test_export_summary_text(fake_documents, tmp_path):
    result = export_report(_state(fake_documents), fake_documents, tmp_path)
    summary = result.summary()
    assert "Baris laporan: 3" in summary
    assert "Laporan:" in summary


def test_row_as_list_order_matches_columns():
    row = ReportRow(filename="SU_001.pdf", su_number="001", map_page="2",
                    group_id="KELOMPOK_001", match_count=10, similarity=88.5,
                    confidence="Tinggi", status="Terverifikasi")
    values = row.as_list()
    assert values[0] == "SU_001.pdf"
    assert values[3] == "KELOMPOK_001"
    assert values[5] == 88.5
    assert values[7] == "Terverifikasi"
