"""Tests for paths and packaging-sensitive behaviour (Phase 7)."""

import sys
from pathlib import Path

import config


# --------------------------------------------------------------------- paths
def test_runtime_root_is_project_when_not_frozen():
    assert not getattr(sys, "frozen", False)
    assert config.RUNTIME_ROOT == Path(config.__file__).resolve().parent


def test_data_dirs_under_runtime_root():
    assert config.DATA_DIR == config.RUNTIME_ROOT / "data"
    assert config.LOG_DIR == config.DATA_DIR / "logs"
    assert config.CACHE_DIR == config.DATA_DIR / "cache"
    assert config.OUTPUT_DIR == config.DATA_DIR / "output"


def test_frozen_root_uses_executable_dir(monkeypatch, tmp_path):
    """When frozen, writable data must sit next to the exe, not in _internal.

    Regression: the packaged build wrote ``data/`` into ``_internal``, which is
    read-only in a normal installation.
    """
    exe = tmp_path / "app" / "LandCADMapGrouper.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))

    assert config._runtime_root() == exe.parent
    assert config._runtime_root() != config.PROJECT_ROOT


def test_paths_ensure_creates_every_directory():
    paths = config.PATHS.ensure()
    for directory in (
        paths.data,
        paths.logs,
        paths.cache,
        paths.output,
        paths.normalized,
        paths.features,
    ):
        assert directory.exists()


def test_app_version_is_current():
    assert config.APP_VERSION.startswith("0.9")


# --------------------------------------------------------------------- spec
def test_spec_file_exists_and_is_parseable():
    project = Path(__file__).resolve().parents[1]
    spec = project / "LandCADMapGrouper.spec"
    assert spec.exists()

    content = spec.read_text(encoding="utf-8")
    # The spec must drop the unused ONNX models, otherwise the bundle grows by
    # ~49 MB for no benefit.
    assert "pymupdf/layout/resources/onnx" in content
    assert "a.datas" in content
    assert "EXE(" in content and "COLLECT(" in content


def test_build_script_exists():
    project = Path(__file__).resolve().parents[1]
    script = project / "build_exe.bat"
    assert script.exists()
    assert "PyInstaller" in script.read_text(encoding="utf-8")


def test_gitignore_covers_build_outputs():
    project = Path(__file__).resolve().parents[1]
    ignored = (project / ".gitignore").read_text(encoding="utf-8")
    for entry in ("build/", "dist/", "data/landcad.db"):
        assert entry in ignored

    # The PyInstaller spec is a deliverable, not a build artefact: ignoring it
    # would silently drop the packaging definition from version control.
    assert "*.spec" not in ignored
    assert (project / "LandCADMapGrouper.spec").exists()


def test_no_network_usage_in_sources():
    """PRD: fully offline. Guard against accidentally adding remote calls."""
    project = Path(__file__).resolve().parents[1]
    suspicious = ("requests.", "urllib.request", "httpx.", "aiohttp")
    offenders = []
    for path in project.rglob("*.py"):
        if any(part in path.parts for part in (".venv", "build", "dist", "tests")):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(token in text for token in suspicious):
            offenders.append(path.name)
    assert offenders == []


def test_originals_never_written():
    """PRD: never modify the source PDFs. Guard the obvious write modes."""
    project = Path(__file__).resolve().parents[1]
    offenders = []
    for path in project.rglob("*.py"):
        if any(part in path.parts for part in (".venv", "build", "dist", "tests")):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "open_document_for_write" in text or '"r+"' in text or "'r+'" in text:
            offenders.append(path.name)
    assert offenders == []
