# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for LandCAD Map Grouper.

Build with:
    .\\venv\\Scripts\\pyinstaller --clean --noconfirm LandCADMapGrouper.spec

The app writes logs, cache and the local database to ``data/`` next to the
executable, so no writable install location is required.
"""

from pathlib import Path

block_cipher = None

project_root = Path(SPECPATH)
entry_point = project_root / "main.py"

hidden_imports = [
    "pymupdf",
    "cv2",
    "numpy",
    "openpyxl",
    "openpyxl.styles",
    "openpyxl.workbook",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "services.export_service",
    "services.cache_service",
    "database.db",
]

# PySide6 ships far more than this app uses; dropping the heavy optional modules
# keeps the bundle practical for a desktop utility.
#
# ``pymupdf`` 2.x drags in ``pymupdf-layout``/``pymupdf4llm``, whose ONNX models
# alone account for ~49 MB. This app only renders pages, so those are excluded.
# Measured before/after: 363 MB -> see docs/architecture.md (Phase 7).
excludes = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick",
    "PySide6.QtQml",
    "PySide6.Qt3DCore",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia",
    "PySide6.QtBluetooth",
    "PySide6.QtNetworkAuth",
    "PySide6.QtWebSockets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "pymupdf4llm",
    "pymupdf_layout",
    "onnxruntime",
    "networkx",
    "matplotlib",
    "scipy",
    "pandas",
    "tkinter",
    "pytest",
]

a = Analysis(
    [str(entry_point)],
    pathex=[str(project_root)],
    binaries=[],
    datas=[],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# PyMuPDF's hook ships the ONNX layout/table models as *data*, which the
# ``excludes`` list cannot reach. They add ~49 MB and this app never uses them,
# so they are filtered out of the collected data files.
_DROPPED_DATA_PARTS = (
    "pymupdf/layout/resources/onnx",
    "pymupdf_layout",
    "pymupdf4llm",
    "onnxruntime",
)


def _keep_data(entry) -> bool:
    destination = entry[0].replace("\\", "/").lower()
    return not any(part in destination for part in _DROPPED_DATA_PARTS)


before = len(a.datas)
a.datas = [entry for entry in a.datas if _keep_data(entry)]
print(f"[spec] dropped {before - len(a.datas)} unused data files (ONNX models)")

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LandCADMapGrouper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LandCADMapGrouper",
)
