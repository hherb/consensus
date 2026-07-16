# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the macOS Consensus.app bundle.
#
# Two executables share one bundle:
#   Consensus          -- windowed GUI (pywebview)
#   consensus-worker   -- console worker for sandboxed execute_python
#
# Build (normally via scripts/build_macos_dmg.sh):
#   python -m PyInstaller --noconfirm packaging/macos/consensus.spec
import pathlib
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

SPEC_DIR = pathlib.Path(SPECPATH).resolve()
REPO_ROOT = SPEC_DIR.parents[1]
sys.path.insert(0, str(REPO_ROOT))

import consensus  # noqa: E402  (version for the bundle metadata)

datas = [
    (str(REPO_ROOT / "consensus" / "static"), "consensus/static"),
    (str(REPO_ROOT / "consensus" / "migrations"), "consensus/migrations"),
    (
        str(REPO_ROOT / "consensus" / "evaluation" / "migrations"),
        "consensus/evaluation/migrations",
    ),
]
binaries = []
hiddenimports = ["consensus.sandbox_worker"]

# Packages with data files / native extensions PyInstaller cannot trace fully.
for pkg in ("sqlite_vec", "trafilatura", "pdfplumber", "certifi"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

app_a = Analysis(
    [str(SPEC_DIR / "launch_app.py")],
    pathex=[str(REPO_ROOT)],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
)

# The worker imports user-requested modules dynamically; bundle the
# scientific stack that ships with the app so sandboxed code can use it.
# PIL needs collect_submodules: its __init__ imports nothing eagerly, so
# "PIL" alone would leave PIL.Image out of the worker's archive.
worker_a = Analysis(
    [str(SPEC_DIR / "launch_worker.py")],
    pathex=[str(REPO_ROOT)],
    hiddenimports=["numpy"] + collect_submodules("PIL"),
)

app_pyz = PYZ(app_a.pure)
worker_pyz = PYZ(worker_a.pure)

app_exe = EXE(
    app_pyz,
    app_a.scripts,
    [],
    exclude_binaries=True,
    name="Consensus",
    console=False,
)

worker_exe = EXE(
    worker_pyz,
    worker_a.scripts,
    [],
    exclude_binaries=True,
    name="consensus-worker",
    console=True,
)

coll = COLLECT(
    app_exe,
    app_a.binaries,
    app_a.datas,
    worker_exe,
    worker_a.binaries,
    worker_a.datas,
    name="Consensus",
)

app = BUNDLE(
    coll,
    name="Consensus.app",
    icon=str(SPEC_DIR / "Consensus.icns"),
    bundle_identifier="io.github.hherb.consensus",
    version=consensus.__version__,
    info_plist={
        "CFBundleName": "Consensus",
        "CFBundleDisplayName": "Consensus",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
    },
)
