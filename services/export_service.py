from __future__ import annotations

import csv
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core.clustering_engine import UNGROUPED_LABEL
from core.pdf_reader import PdfInfo
from core.review_model import ReviewState
from utils.file_utils import natural_sort_key
from utils.logging_utils import get_logger

logger = get_logger("export_service")

EXPORT_VERSION = "export-v1"

REPORT_COLUMNS = (
    "Nama file",
    "Nomor SU",
    "Halaman peta",
    "ID kelompok",
    "Jumlah pasangan cocok",
    "Skor kemiripan",
    "Confidence",
    "Status",
)


@dataclass(frozen=True)
class ReportRow:
    """One row of the grouping report (PRD section 10A)."""

    filename: str
    su_number: str = ""
    map_page: str = "-"
    group_id: str = UNGROUPED_LABEL
    match_count: int = 0
    similarity: float = 0.0
    confidence: str = "Rendah"
    status: str = "Perlu verifikasi"
    source_path: Path | None = None

    def as_list(self) -> list[object]:
        return [
            self.filename,
            self.su_number,
            self.map_page,
            self.group_id,
            self.match_count,
            self.similarity,
            self.confidence,
            self.status,
        ]


@dataclass(frozen=True)
class ExportResult:
    rows: tuple[ReportRow, ...] = ()
    report_path: Path | None = None
    output_dir: Path | None = None
    copied: tuple[Path, ...] = field(default_factory=tuple)
    skipped: tuple[str, ...] = field(default_factory=tuple)
    error: str = ""

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def copied_count(self) -> int:
        return len(self.copied)

    @property
    def has_error(self) -> bool:
        return bool(self.error)

    def summary(self) -> str:
        if self.has_error:
            return f"Ekspor gagal: {self.error}"
        lines = [f"Baris laporan: {self.row_count}"]
        if self.report_path is not None:
            lines.append(f"Laporan: {self.report_path}")
        if self.output_dir is not None:
            lines.append(f"Folder hasil: {self.output_dir}")
            lines.append(f"File disalin: {self.copied_count}")
        if self.skipped:
            lines.append(f"Dilewati: {len(self.skipped)} ({', '.join(self.skipped[:3])})")
        return "\n".join(lines)


def extract_su_number(filename: str) -> str:
    """Return the SU number from a filename such as ``SU_057.pdf`` -> ``057``.

    Only the conventional ``SU_<digits>`` prefix is recognised; anything else
    returns an empty string because inventing a number would be misleading.
    """
    stem = Path(filename).stem
    parts = stem.replace("-", "_").split("_")
    if len(parts) >= 2 and parts[0].upper() == "SU" and parts[1].isdigit():
        return parts[1]
    return ""


def build_report_rows(
    state: ReviewState,
    documents: dict[str, PdfInfo],
) -> tuple[ReportRow, ...]:
    """Build one report row per document in the current review state."""
    rows: list[ReportRow] = []

    entries: list[tuple[str, str, bool, str, float]] = []
    for group in state.groups:
        for key in group.member_keys:
            entries.append((key, group.name, group.verified, group.confidence,
                            state.member_scores(group).get(key, 0.0)))
    for key in state.ungrouped:
        entries.append((key, UNGROUPED_LABEL, False, "Rendah", 0.0))

    entries.sort(key=lambda item: (item[1], natural_sort_key(item[0])))

    for key, group_id, verified, confidence, similarity in entries:
        info = documents.get(key)
        filename = info.filename if info is not None else state.name_of(key)
        map_page = "-"
        if info is not None:
            map_page = str(info.page_count) if info.is_valid else "-"

        rows.append(
            ReportRow(
                filename=filename,
                su_number=extract_su_number(filename),
                map_page=map_page,
                group_id=group_id,
                match_count=state.match_count_of(key),
                similarity=round(similarity, 1),
                confidence=confidence,
                status="Terverifikasi" if verified else "Perlu verifikasi",
                source_path=info.path if info is not None else None,
            )
        )

    return tuple(rows)


def write_csv(rows: tuple[ReportRow, ...], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(REPORT_COLUMNS)
        for row in rows:
            writer.writerow(row.as_list())
    logger.info("Laporan CSV ditulis: %s (%d baris)", path, len(rows))
    return path


def write_xlsx(rows: tuple[ReportRow, ...], path: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Hasil Pengelompokan"
    sheet.append(list(REPORT_COLUMNS))
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    for row in rows:
        sheet.append(row.as_list())

    for column, width in zip(
        "ABCDEFGH", (24, 12, 14, 18, 22, 16, 14, 18)
    ):
        sheet.column_dimensions[column].width = width

    workbook.save(path)
    logger.info("Laporan XLSX ditulis: %s (%d baris)", path, len(rows))
    return path


def export_report(
    state: ReviewState,
    documents: dict[str, PdfInfo],
    output_dir: Path,
    fmt: str = "xlsx",
    timestamped: bool = False,
) -> ExportResult:
    """Write the CSV/XLSX report without touching any PDF."""
    rows = build_report_rows(state, documents)
    if not rows:
        return ExportResult(rows=(), error="Tidak ada dokumen untuk diekspor.")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = datetime.now().strftime("%Y%m%d_%H%M%S") if timestamped else ""
    stem = f"laporan_pengelompokan{('_' + suffix) if suffix else ''}"

    try:
        if fmt == "csv":
            path = write_csv(rows, output_dir / f"{stem}.csv")
        elif fmt == "xlsx":
            path = write_xlsx(rows, output_dir / f"{stem}.xlsx")
        else:
            raise ValueError(f"Format tidak dikenal: {fmt}")
    except Exception as exc:  # noqa: BLE001 - report failure instead of crashing
        logger.exception("Gagal menulis laporan %s", fmt)
        return ExportResult(rows=rows, error=f"Gagal menulis laporan: {exc}")

    return ExportResult(rows=rows, report_path=path)


def safe_folder_name(group_id: str) -> str:
    """Sanitise a group name for use as a folder (PRD example: KELOMPOK_001)."""
    cleaned = "".join(
        character if character.isalnum() or character in ("_", "-") else "_"
        for character in group_id
    )
    return cleaned.strip("_") or UNGROUPED_LABEL


def copy_documents_to_folders(
    state: ReviewState,
    documents: dict[str, PdfInfo],
    output_dir: Path,
    overwrite: bool = False,
    progress=None,
) -> ExportResult:
    """Copy (never move) each PDF into its group folder under ``output_dir``.

    PRD section 10B: COPY is the default; the originals are never modified or
    deleted.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    plan: list[tuple[str, PdfInfo]] = []
    for group in state.groups:
        for key in group.member_keys:
            info = documents.get(key)
            if info is not None:
                plan.append((group.name, info))
    for key in state.ungrouped:
        info = documents.get(key)
        if info is not None:
            plan.append((UNGROUPED_LABEL, info))

    copied: list[Path] = []
    skipped: list[str] = []
    total = len(plan)

    for index, (folder_name, info) in enumerate(plan, start=1):
        if progress is not None:
            progress(index, total, info.filename)

        target_dir = output_dir / safe_folder_name(folder_name)
        target = target_dir / info.path.name

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            if target.exists() and not overwrite:
                skipped.append(info.filename)
                continue
            if target.exists():
                target.unlink()
            shutil.copy2(info.path, target)
            copied.append(target)
        except OSError as exc:
            logger.warning("Gagal menyalin %s: %s", info.filename, exc)
            skipped.append(info.filename)

    logger.info(
        "Penyalinan selesai: %d disalin, %d dilewati", len(copied), len(skipped)
    )
    return ExportResult(
        output_dir=output_dir,
        copied=tuple(copied),
        skipped=tuple(skipped),
    )


__all__ = [
    "EXPORT_VERSION",
    "ExportResult",
    "REPORT_COLUMNS",
    "ReportRow",
    "build_report_rows",
    "copy_documents_to_folders",
    "export_report",
    "extract_su_number",
    "safe_folder_name",
    "write_csv",
    "write_xlsx",
]
