import numpy as np
import pytest

from core.clustering_engine import ClusterConfig, MapDocument
from core.pdf_reader import inspect_pdf
from services.grouping_service import (
    GroupedDocument,
    GroupingResult,
    group_documents,
    prepare_documents,
    prepare_grouped_document,
)


@pytest.fixture
def pair(sample_pdfs):
    return [inspect_pdf(path) for path in sample_pdfs]


# ---------------------------------------------------------------- preparation
def test_prepare_grouped_document_uses_parcel_free_image(pair, tmp_path):
    document = prepare_grouped_document(pair[0], cache_dir=tmp_path)

    assert isinstance(document, GroupedDocument)
    assert document.key == pair[0].key
    assert document.features.count > 0
    # parcel-free image differs from the raw normalized map
    assert document.comparable is not None
    assert not np.array_equal(document.image, document.comparable.image)


def test_prepare_documents_reports_progress(pair, tmp_path):
    events = []
    prepared, errors, cancelled = prepare_documents(
        pair, cache_dir=tmp_path, progress=lambda c, t, m: events.append((c, t))
    )

    assert len(prepared) == 2
    assert not errors
    assert not cancelled
    assert events[-1] == (2, 2)


def test_prepare_documents_continues_after_failure(pair, tmp_path):
    broken = type(pair[0])(
        path=pair[0].path.parent / "tidak-ada.pdf",
        filename="tidak-ada.pdf",
        size_bytes=0,
        modified_at=0.0,
        page_count=0,
        status=pair[0].status.__class__.MISSING,
        message="tidak ada",
    )
    prepared, errors, cancelled = prepare_documents([broken, pair[0]], cache_dir=tmp_path)

    assert len(prepared) == 1
    assert len(errors) == 1
    assert not cancelled


def test_prepare_documents_supports_cancellation(pair, tmp_path):
    prepared, errors, cancelled = prepare_documents(
        pair, cache_dir=tmp_path, should_cancel=lambda: True
    )
    assert cancelled
    assert prepared == []


def test_to_map_document_preserves_identity(pair, tmp_path):
    document = prepare_grouped_document(pair[0], cache_dir=tmp_path)
    map_doc = document.to_map_document()

    assert isinstance(map_doc, MapDocument)
    assert map_doc.key == document.key
    assert map_doc.filename == document.filename


# ------------------------------------------------------------------ grouping
def test_group_reference_documents_together(pair, tmp_path):
    result = group_documents(pair, cache_dir=tmp_path)

    assert isinstance(result, GroupingResult)
    assert result.group_count == 1
    assert result.ungrouped_count == 0

    group = result.clustering.groups[0]
    assert set(result.filenames(group.member_keys)) == {"SU_057.pdf", "SU_058.pdf"}
    assert group.min_pair_score >= 65.0


def test_grouping_reports_measured_pair(pair, tmp_path):
    result = group_documents(pair, cache_dir=tmp_path)
    edge = result.clustering.edges[0]
    assert edge.score >= 65.0
    assert not edge.skipped


def test_grouping_with_single_document(pair, tmp_path):
    result = group_documents([pair[0]], cache_dir=tmp_path)
    assert result.group_count == 0
    assert result.ungrouped_count == 1


def test_grouping_result_helpers(pair, tmp_path):
    result = group_documents(pair, cache_dir=tmp_path)
    group = result.clustering.groups[0]

    members = result.group_members(group.group_id)
    assert len(members) == 2
    assert result.group_members(999) == ()
    assert result.name_of("tidak-ada") == "tidak-ada"


def test_grouping_summary_mentions_group(pair, tmp_path):
    summary = group_documents(pair, cache_dir=tmp_path).summary()
    assert "Jumlah kelompok: 1" in summary
    assert "KELOMPOK_001" in summary
    assert "SU_057.pdf" in summary


def test_grouping_with_prepared_documents(pair, tmp_path):
    prepared, _, _ = prepare_documents(pair, cache_dir=tmp_path)
    result = group_documents(pair, cache_dir=tmp_path, prepared=prepared)
    assert result.group_count == 1


# ------------------------------------------------------------- strictness
def test_high_threshold_prevents_grouping(pair, tmp_path):
    strict = ClusterConfig(link_threshold=99.9)
    result = group_documents(pair, cache_dir=tmp_path, config=strict)
    assert result.group_count == 0
    assert result.ungrouped_count == 2


def test_low_threshold_groups_reference_pair(pair, tmp_path):
    loose = ClusterConfig(link_threshold=50.0)
    result = group_documents(pair, cache_dir=tmp_path, config=loose)
    assert result.group_count == 1
