import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def sample_pdfs(project_root: Path) -> list[Path]:
    samples = sorted(project_root.glob("SU_*.pdf"))
    if not samples:
        pytest.skip("Contoh PDF SU_057/SU_058 tidak tersedia.")
    return samples


@pytest.fixture
def temp_pdf(tmp_path: Path):
    """Factory creating a small multi-page PDF at a chosen path."""

    def _create(name: str = "sample.pdf", pages: int = 2) -> Path:
        pymupdf = pytest.importorskip("pymupdf")
        path = tmp_path / name
        doc = pymupdf.open()
        for index in range(pages):
            page = doc.new_page(width=595, height=842)
            page.insert_text((72, 100), f"Halaman {index + 1}")
        doc.save(path)
        doc.close()
        return path

    return _create
