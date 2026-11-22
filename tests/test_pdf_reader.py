import pytest

from core.pdf_reader import (
    DocumentStatus,
    PdfReadError,
    inspect_pdf,
    open_document,
    validate_pdf,
)


def test_inspect_real_sample_pdf(sample_pdfs):
    info = inspect_pdf(sample_pdfs[0])
    assert info.is_valid
    assert info.status is DocumentStatus.OK
    assert info.page_count == 2
    assert info.size_bytes > 0
    assert info.filename.startswith("SU_")


def test_inspect_generated_pdf(temp_pdf):
    path = temp_pdf("tiga.pdf", pages=3)
    info = inspect_pdf(path)
    assert info.is_valid
    assert info.page_count == 3


def test_missing_file_is_reported_not_raised(tmp_path):
    info = inspect_pdf(tmp_path / "tidak_ada.pdf")
    assert not info.is_valid
    assert info.status is DocumentStatus.MISSING
    assert "tidak ditemukan" in info.error_message.lower()


def test_corrupt_file_is_reported(tmp_path):
    bad = tmp_path / "rusak.pdf"
    bad.write_bytes(b"%PDF-1.7\nini bukan pdf yang valid")
    info = inspect_pdf(bad)
    assert not info.is_valid
    assert info.status is DocumentStatus.CORRUPT
    assert info.error_message


def test_non_pdf_extension_content_is_reported(tmp_path):
    bad = tmp_path / "gambar.pdf"
    bad.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)
    info = inspect_pdf(bad)
    assert not info.is_valid
    assert info.status in (DocumentStatus.CORRUPT, DocumentStatus.UNREADABLE)


def test_encrypted_pdf_is_flagged(temp_pdf, tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    plain = temp_pdf("polos.pdf", pages=1)
    encrypted = tmp_path / "terkunci.pdf"
    doc = pymupdf.open(plain)
    doc.save(
        encrypted,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="pemilik",
        user_pw="rahasia",
    )
    doc.close()

    info = inspect_pdf(encrypted)
    assert not info.is_valid
    assert info.status is DocumentStatus.ENCRYPTED
    assert info.is_encrypted


def test_empty_pdf_is_reported(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "kosong.pdf"
    path.write_bytes(
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [] /Count 0 >> endobj\n"
        b"trailer << /Root 1 0 R /Size 3 >>\n%%EOF\n"
    )

    with pymupdf.open(path) as doc:
        assert doc.page_count == 0

    info = inspect_pdf(path)
    assert not info.is_valid
    assert info.status is DocumentStatus.EMPTY


def test_validate_pdf_returns_message_for_invalid(tmp_path):
    ok, message = validate_pdf(tmp_path / "hantu.pdf")
    assert not ok
    assert message


def test_render_page_returns_png(sample_pdfs):
    with open_document(sample_pdfs[0]) as document:
        page = document.render_page(1, dpi=72)

    assert page.png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert page.page_number == 2
    assert page.page_count == 2
    assert page.width > 0 and page.height > 0


def test_render_respects_max_dimension(sample_pdfs):
    with open_document(sample_pdfs[0]) as document:
        huge = document.render_page(0, dpi=600)
        capped = document.render_page(0, dpi=600, max_dimension=1200)

    assert max(capped.width, capped.height) <= 1210
    assert capped.width <= huge.width


def test_render_invalid_page_index_raises(sample_pdfs):
    with open_document(sample_pdfs[0]) as document:
        with pytest.raises(PdfReadError):
            document.render_page(99, dpi=72)


def test_document_does_not_modify_original(sample_pdfs):
    path = sample_pdfs[0]
    before = (path.stat().st_size, path.stat().st_mtime_ns)
    with open_document(path) as document:
        document.render_page(0, dpi=72)
        document.page_count
    after = (path.stat().st_size, path.stat().st_mtime_ns)
    assert before == after


def test_opening_corrupt_document_raises(tmp_path):
    bad = tmp_path / "rusak.pdf"
    bad.write_bytes(b"bukan pdf sama sekali")
    with pytest.raises(PdfReadError):
        with open_document(bad):
            pass
