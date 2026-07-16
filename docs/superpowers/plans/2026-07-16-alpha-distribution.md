# Alpha Distribution (macOS DMG + PyPI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Consensus to alpha testers as (a) a signed + notarized macOS DMG and (b) a PyPI package `consensus-app` installable via `uv tool install consensus-app`.

**Architecture:** The PyPI side renames the distribution (import package and CLI stay `consensus`), makes the full feature set the default dependencies, and moves the top-level `evaluation` package under `consensus.evaluation` to avoid squatting a generic name. The macOS side uses PyInstaller with two executables in one bundle (`Consensus` GUI + `consensus-worker` console) so the sandboxed `execute_python` tool keeps working when frozen; local shell scripts handle codesign → notarize → staple → DMG.

**Tech Stack:** setuptools, uv (build/publish), PyInstaller ≥ 6, Pillow (icon generation), macOS `codesign`/`notarytool`/`stapler`/`hdiutil`/`sips`/`iconutil`.

**Spec:** `docs/superpowers/specs/2026-07-16-alpha-distribution-design.md`

## Global Constraints

- Use `uv` for all Python package operations — never bare `pip`.
- PyPI distribution name: `consensus-app`. Console scripts `consensus` and `consensus-mcp` and the import package `consensus` are unchanged.
- Versioning: plain PEP 440 releases (`1.99.0`, `1.99.1`, …) — never pre-release tags like `2.0.0a1` (they force `--prerelease=allow` on testers).
- macOS bundle identifier: `io.github.hherb.consensus`. App name: `Consensus.app`. Worker executable name: `consensus-worker`.
- Notary keychain profile name: `consensus-notary`. Codesign identity: auto-discovered `Developer ID Application`, overridable via `CODESIGN_IDENTITY` env var.
- Entitlements required for notarized CPython: `com.apple.security.cs.allow-unsigned-executable-memory`, `com.apple.security.cs.disable-library-validation`.
- Run the full test suite with `python -m pytest tests/ -q` (pytest-asyncio is in auto mode — async tests need no marker).
- License: AGPL-3.0-or-later.

---

### Task 1: Move `evaluation/` → `consensus/evaluation/`

The wheel currently ships a top-level `evaluation` package, which would squat a
generic import name on PyPI. Both consumer imports are try/except-guarded, so
behavior is identical after the move.

**Files:**
- Move: `evaluation/` → `consensus/evaluation/` (entire directory incl. `migrations/`)
- Modify: `consensus/evaluation/__main__.py`, `consensus/evaluation/eval_routes.py`, `consensus/evaluation/eval_db.py`, `consensus/evaluation/runner.py`, `consensus/evaluation/scorer.py` (import strings)
- Modify: `consensus/server.py:1190-1191`, `consensus/desktop.py:533-534`
- Modify: `pyproject.toml` (packages.find, package-data)

**Interfaces:**
- Produces: importable package `consensus.evaluation` with unchanged public API (`consensus.evaluation.eval_db.EvalDatabase`, `consensus.evaluation.eval_routes.register_eval_routes`); runnable as `python -m consensus.evaluation`.

- [ ] **Step 1: Move the directory with git**

```bash
git mv evaluation consensus/evaluation
rm -rf consensus/evaluation/__pycache__
```

- [ ] **Step 2: Rewrite internal absolute imports**

All internal imports use the `from evaluation.X import …` form. Rewrite them
(macOS sed syntax):

```bash
grep -rl "from evaluation\." consensus/evaluation --include="*.py" \
  | xargs sed -i '' 's/from evaluation\./from consensus.evaluation./g'
```

Verify nothing is left:

```bash
grep -rn "from evaluation\.\|import evaluation" consensus/evaluation --include="*.py"
```

Expected: only `consensus/evaluation/eval_db.py` lines with the *strings*
`"Could not import evaluation.cases for seeding"` / `"…evaluation.conditions…"`
(log messages, harmless) — no actual import statements.

- [ ] **Step 3: Fix the sys.path hack in runner.py**

`consensus/evaluation/runner.py:29` currently inserts the repo root assuming
the file lives one level below it. After the move it is two levels down.
Change:

```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

to:

```python
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
```

- [ ] **Step 4: Update the two guarded consumer import sites**

In `consensus/server.py` (~line 1190), inside the existing `try:` block, change:

```python
        from evaluation.eval_db import EvalDatabase
        from evaluation.eval_routes import register_eval_routes
```

to (relative imports, matching the module's style):

```python
        from .evaluation.eval_db import EvalDatabase
        from .evaluation.eval_routes import register_eval_routes
```

In `consensus/desktop.py` (~line 533), inside the existing `try:` block, change:

```python
                from evaluation.eval_db import EvalDatabase
                from evaluation.eval_routes import register_eval_routes
```

to:

```python
                from .evaluation.eval_db import EvalDatabase
                from .evaluation.eval_routes import register_eval_routes
```

- [ ] **Step 5: Update pyproject packaging config**

In `pyproject.toml` replace:

```toml
[tool.setuptools.packages.find]
include = ["consensus*", "evaluation*"]

[tool.setuptools.package-data]
consensus = ["static/*", "migrations/*.sql"]
evaluation = ["static/*", "migrations/*.sql"]
```

with:

```toml
[tool.setuptools.packages.find]
include = ["consensus*"]

[tool.setuptools.package-data]
consensus = ["static/*", "migrations/*.sql"]
"consensus.evaluation" = ["static/*", "migrations/*.sql"]
```

(`consensus/evaluation/migrations/` contains an `__init__.py`, so it is
discovered as a package; the glob ships its `.sql` files. The `static/*`
glob ships the eval UI assets — eval.html, eval.js, eval_style.css — which
the pre-move config also shipped.)

- [ ] **Step 6: Reinstall editable and run the full test suite**

```bash
uv pip install -e ".[all]"
python -m pytest tests/ -q
```

Expected: all tests pass (same count as on main — ~2250). Also verify the
moved package imports and the eval UI degrades gracefully nowhere:

```bash
python -c "from consensus.evaluation.eval_db import EvalDatabase; print('ok')"
python -c "from consensus.evaluation.eval_routes import register_eval_routes; print('ok')"
```

Expected: `ok` twice.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: move evaluation package under consensus.evaluation

Avoids shipping a generic top-level 'evaluation' package in the wheel
once the project is published to PyPI."
```

---

### Task 2: PyPI metadata — rename to `consensus-app`, full deps by default

**Files:**
- Modify: `pyproject.toml` (build-system, `[project]`, extras, urls)
- Modify: `consensus/__init__.py` (version `1.99` → `1.99.0`)
- Modify: `README.md` (Installation section)
- Modify: `CLAUDE.md` (install commands note)

**Interfaces:**
- Produces: wheel `dist/consensus_app-1.99.0-py3-none-any.whl` with console scripts `consensus`, `consensus-mcp`; Task 4's release script and Task 7's build script read the version via `sed -n 's/^__version__ = "\(.*\)"$/\1/p' consensus/__init__.py`.

- [ ] **Step 1: Update `consensus/__init__.py`**

```python
"""Consensus - A moderated discussion platform for humans and AI."""

__version__ = "1.99.0"
```

- [ ] **Step 2: Rewrite pyproject.toml metadata**

Replace the `[build-system]`, `[project]`, and `[project.optional-dependencies]`
sections (keep `[project.scripts]`, `[tool.setuptools.dynamic]`, and the
sections edited in Task 1 as they are):

```toml
[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"

[project]
name = "consensus-app"
dynamic = ["version"]
description = "Moderated multi-party discussions between humans and AI — structured analytical methods, tools, and RAG"
readme = "README.md"
license = "AGPL-3.0-or-later"
license-files = ["LICENSE"]
requires-python = ">=3.11"
authors = [{ name = "Horst Herb" }]
keywords = ["ai", "llm", "discussion", "deliberation", "multi-agent", "moderation", "consensus"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Science/Research",
    "Programming Language :: Python :: 3 :: Only",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Scientific/Engineering :: Artificial Intelligence",
    "Topic :: Communications :: Conferencing",
]
dependencies = [
    "httpx>=0.27",
    "python-dotenv>=1.0",
    "trafilatura>=2.0.0",
    "pywebview>=5.0",
    "PyGObject>=3.42; sys_platform == 'linux'",
    "pycairo>=1.24; sys_platform == 'linux'",
    "aiohttp>=3.14.1",
    "sqlite-vec>=0.1.0",
    "numpy>=1.26",
    "pdfplumber>=0.10",
    "Pillow>=12.2.0",
]

[project.optional-dependencies]
# All features install by default since the alpha releases. These empty
# extras are kept so documented commands like `uv pip install -e ".[all]"`
# keep working.
desktop = []
web = []
tools = []
memory = []
documents = []
images = []
all = []

[project.urls]
Homepage = "https://github.com/hherb/consensus"
Repository = "https://github.com/hherb/consensus"
Issues = "https://github.com/hherb/consensus/issues"
```

Notes for the implementer:
- `license = "AGPL-3.0-or-later"` is the PEP 639 SPDX form; do NOT also add a
  `License ::` classifier (setuptools ≥ 77 rejects mixing them).
- The base `dependencies` list is the union of the old base + old `[all]`.

- [ ] **Step 3: Build and inspect the wheel**

```bash
rm -rf dist
uv build
unzip -l dist/consensus_app-1.99.0-py3-none-any.whl | grep -E \
  "consensus/static/index.html|consensus/migrations/001_baseline.sql|consensus/evaluation/migrations/001_baseline.sql"
```

Expected: all three paths listed. Also confirm no top-level `evaluation/`:

```bash
unzip -l dist/consensus_app-1.99.0-py3-none-any.whl | grep -c "^.*[0-9] *evaluation/" || echo "clean"
```

Expected: `clean` (or `0`).

- [ ] **Step 4: Smoke-test the wheel in a scratch venv**

```bash
uv venv /tmp/consensus-wheel-test
uv pip install --python /tmp/consensus-wheel-test/bin/python dist/consensus_app-1.99.0-py3-none-any.whl
/tmp/consensus-wheel-test/bin/consensus --help
/tmp/consensus-wheel-test/bin/python -c "import consensus; print(consensus.__version__)"
```

Expected: argparse usage text from `consensus --help`; `1.99.0` from the
version check. Then smoke-run the web server from the installed wheel
(port 8199 to avoid clashing with a dev instance):

```bash
/tmp/consensus-wheel-test/bin/consensus --web --port 8199 &
SERVER_PID=$!
sleep 3
curl -sf http://127.0.0.1:8199/health && echo " <- health OK"
curl -sf http://127.0.0.1:8199/ | grep -q "<title>" && echo "index OK"
kill $SERVER_PID
rm -rf /tmp/consensus-wheel-test
```

Expected: the health endpoint returns 200 (`health OK`) and the root serves
the UI HTML (`index OK`) — proving static files and migrations shipped in
the wheel actually work at runtime.

- [ ] **Step 5: Update README.md Installation section**

Replace the block from `## Installation` up to (not including) `## Usage` with:

````markdown
## Installation

Requires Python 3.11+. Recommended: [uv](https://docs.astral.sh/uv/)
(`curl -LsSf https://astral.sh/uv/install.sh | sh`).

```bash
# Install from PyPI as a global command (recommended)
uv tool install consensus-app

# Or with pip into the current environment
pip install consensus-app
```

macOS users can instead download the notarized `Consensus-<version>.dmg` from
the [releases page](https://github.com/hherb/consensus/releases) and drag
Consensus into Applications.

> **Note:** The PyPI *distribution* is named `consensus-app` (the name
> `consensus` was taken), but the command and the import package are plainly
> `consensus`.

### From source (development)

```bash
git clone https://github.com/hherb/consensus.git
cd consensus
uv tool install -e .          # editable global command
# or: uv pip install -e .     # editable, into the active venv
```

All features (desktop, web, documents, memory, images) are installed by
default. The old extras (`[all]`, `[desktop]`, …) still parse but are empty.

> **Linux desktop mode:** Install GTK dev libraries first so PyGObject can compile inside the uv venv:
> ```bash
> sudo apt install libgirepository-2.0-dev libcairo2-dev pkg-config python3-dev gir1.2-gtk-3.0 gir1.2-webkit2-4.1
> ```
> On Ubuntu 22.04 or older, use `libgirepository1.0-dev` instead of `libgirepository-2.0-dev`.
````

Also update the MCP-server section further down: change `uv pip install -e ".[all]"` to `uv pip install -e .`.

- [ ] **Step 6: Update CLAUDE.md install commands**

In the `## Commands` section, replace:

```bash
# Install
uv pip install -e .              # base (httpx only)
uv pip install -e ".[desktop]"   # + pywebview
uv pip install -e ".[web]"       # + aiohttp
uv pip install -e ".[all]"       # everything
```

with:

```bash
# Install (all features are default dependencies; old extras are empty aliases)
uv pip install -e .
# PyPI distribution name is `consensus-app` (import/CLI stay `consensus`):
#   uv tool install consensus-app
```

- [ ] **Step 7: Run tests and commit**

```bash
python -m pytest tests/ -q
git add pyproject.toml consensus/__init__.py README.md CLAUDE.md
git commit -m "feat: rename PyPI distribution to consensus-app with full deps by default"
```

---

### Task 3: Frozen-mode support (`consensus/frozen.py` + tools_python branches)

Inside a PyInstaller bundle `sys.executable` is the GUI app itself, so
`sys.executable -m consensus.sandbox_worker` would relaunch the GUI, and
`uv pip install` cannot install into a frozen bundle.

**Files:**
- Create: `consensus/frozen.py`
- Modify: `consensus/tools_python.py` (worker launch ~line 256; `_install_package_handler` ~line 451)
- Test: `tests/test_frozen_packaging.py`

**Interfaces:**
- Produces: `consensus.frozen.is_frozen() -> bool` and `consensus.frozen.worker_command() -> tuple[str, list[str]]` (executable path, args). Task 6's bundle places the worker binary at `Contents/MacOS/consensus-worker`, i.e. sibling of `sys.executable` — `worker_command()` relies on that.
- Consumes: `ToolResult` dataclass from `consensus/tools.py` (fields `content`, `is_error`, `metadata`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_frozen_packaging.py`:

```python
"""Tests for PyInstaller frozen-mode support (consensus.frozen + tools_python)."""

import sys
from pathlib import Path

from consensus import frozen


def test_is_frozen_false_by_default():
    assert frozen.is_frozen() is False


def test_worker_command_unfrozen():
    exe, args = frozen.worker_command()
    assert exe == sys.executable
    assert args == ["-m", "consensus.sandbox_worker"]


def test_is_frozen_true_when_sys_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert frozen.is_frozen() is True


def test_worker_command_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    exe, args = frozen.worker_command()
    assert Path(exe).name == "consensus-worker"
    assert Path(exe).parent == Path(sys.executable).resolve().parent
    assert args == []


async def test_install_package_refused_when_frozen(monkeypatch):
    from consensus.tools_python import _install_package_handler

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    result = await _install_package_handler(
        {"package_name": "numpy", "reason": "testing"}, None, None
    )
    assert result.is_error
    assert "not available" in result.content
    assert result.metadata == {"status": "unavailable_frozen"}
```

(pytest-asyncio runs in auto mode per `pytest.ini` — no marker needed.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_frozen_packaging.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'consensus.frozen'` (collection error).

- [ ] **Step 3: Create `consensus/frozen.py`**

```python
"""Helpers for running inside a PyInstaller-frozen app bundle."""

import sys
from pathlib import Path


def is_frozen() -> bool:
    """Return True when running from a PyInstaller bundle."""
    return getattr(sys, "frozen", False) is True


def worker_command() -> tuple[str, list[str]]:
    """Return (executable, args) that launch the sandbox worker.

    In a frozen app the worker is the sibling ``consensus-worker``
    executable bundled next to the GUI binary (Contents/MacOS on macOS);
    otherwise it is ``python -m consensus.sandbox_worker``.
    """
    if is_frozen():
        worker = Path(sys.executable).resolve().parent / "consensus-worker"
        return str(worker), []
    return sys.executable, ["-m", "consensus.sandbox_worker"]
```

- [ ] **Step 4: Wire it into `consensus/tools_python.py`**

Add to the module's imports (near the other relative imports):

```python
from .frozen import is_frozen, worker_command
```

In `execute_python_handler` (~line 256) replace:

```python
    # Prepare subprocess command
    python_exe = sys.executable
    worker_args = ["-m", "consensus.sandbox_worker"]
```

with:

```python
    # Prepare subprocess command (frozen app bundles a dedicated worker binary)
    python_exe, worker_args = worker_command()
```

(`_build_macos_sandbox_cmd(python_exe, worker_args, sandbox_dir)` simply
concatenates `[sandbox-exec, -p, profile, python_exe] + worker_args`, so it
works unchanged with an empty args list.)

In `_install_package_handler` (~line 451), insert at the very top of the
function body, before `package_name = …`:

```python
    if is_frozen():
        return ToolResult(
            content=(
                "Package installation is not available in the bundled "
                "desktop app. Install Consensus from PyPI instead "
                "(`uv tool install consensus-app`) to use this feature."
            ),
            is_error=True,
            metadata={"status": "unavailable_frozen"},
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_frozen_packaging.py -v`
Expected: 5 passed.

- [ ] **Step 6: Run the full suite (guards against regressions in tools tests)**

Run: `python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add consensus/frozen.py consensus/tools_python.py tests/test_frozen_packaging.py
git commit -m "feat: frozen-mode support for sandbox worker and package install"
```

---

### Task 4: PyPI release script

**Files:**
- Create: `scripts/release_pypi.sh` (mode 755)

**Interfaces:**
- Consumes: version via `sed` from `consensus/__init__.py` (Task 2); wheel layout guarantees from Task 1/2.
- Produces: published `consensus-app` release (or `--build-only` verification).

- [ ] **Step 1: Write the script**

Create `scripts/release_pypi.sh`:

```bash
#!/usr/bin/env bash
# Build and publish the consensus-app package.
#
# Usage:
#   scripts/release_pypi.sh                # build, verify, publish to PyPI
#   scripts/release_pypi.sh --test        # publish to TestPyPI instead
#   scripts/release_pypi.sh --build-only  # build + verify, no upload
#
# Publishing requires UV_PUBLISH_TOKEN to hold a PyPI (or TestPyPI) API token.
set -euo pipefail

cd "$(dirname "$0")/.."

MODE="${1:-}"
VERSION=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' consensus/__init__.py)
[[ -n "$VERSION" ]] || { echo "ERROR: could not read version from consensus/__init__.py"; exit 1; }
WHEEL="dist/consensus_app-${VERSION}-py3-none-any.whl"

echo "Building consensus-app ${VERSION}"
rm -rf dist
uv build

[[ -f "$WHEEL" ]] || { echo "ERROR: expected wheel $WHEEL not found"; exit 1; }

# Capture the listing once: piping unzip into `grep -q` under pipefail
# dies with SIGPIPE (141) when grep exits on the first match.
LISTING=$(unzip -l "$WHEEL")

check_wheel_contains() {
    grep -q "$1" <<< "$LISTING" \
        || { echo "ERROR: '$1' missing from wheel"; exit 1; }
}
check_wheel_contains "consensus/static/index.html"
check_wheel_contains "consensus/migrations/001_baseline.sql"
check_wheel_contains "consensus/evaluation/migrations/001_baseline.sql"
check_wheel_contains "consensus/evaluation/static/eval.html"
echo "Wheel contents OK: $WHEEL"

case "$MODE" in
    --build-only)
        echo "Build-only mode; skipping upload." ;;
    --test)
        uv publish --publish-url https://test.pypi.org/legacy/ ;;
    "")
        uv publish ;;
    *)
        echo "ERROR: unknown option '$MODE'"; exit 1 ;;
esac
```

```bash
chmod +x scripts/release_pypi.sh
```

- [ ] **Step 2: Verify the build path works**

Run: `scripts/release_pypi.sh --build-only`
Expected output ends with:

```
Wheel contents OK: dist/consensus_app-1.99.0-py3-none-any.whl
Build-only mode; skipping upload.
```

- [ ] **Step 3: Verify the unknown-option guard**

Run: `scripts/release_pypi.sh --bogus; echo "exit=$?"`
Expected: `ERROR: unknown option '--bogus'` and `exit=1`.

- [ ] **Step 4: Commit**

```bash
git add scripts/release_pypi.sh
git commit -m "feat: PyPI release script with wheel-content verification"
```

---

### Task 5: App icon (generated, committed)

No logo exists in the repo. Generate a simple, original geometric icon
(overlapping speech bubbles on an indigo gradient squircle) with Pillow, then
convert to `.icns`. The generated `Consensus.icns` is committed so app builds
don't depend on regeneration.

**Files:**
- Create: `packaging/macos/make_icon.py`
- Create: `packaging/macos/make_icns.sh` (mode 755)
- Create (generated, committed): `packaging/macos/Consensus.icns`
- Modify: `.gitignore` (ignore iconset intermediates)

**Interfaces:**
- Produces: `packaging/macos/Consensus.icns`, referenced by Task 6's spec file.

- [ ] **Step 1: Write the icon generator**

Create `packaging/macos/make_icon.py`:

```python
"""Generate the Consensus app icon as a 1024x1024 PNG.

Design: two overlapping speech bubbles (the discussion) converging on a
shared centre dot (the consensus), on an indigo gradient squircle with the
standard macOS Big Sur margin.
"""

from PIL import Image, ImageDraw

SIZE = 1024
# macOS icons leave ~10% transparent margin around the squircle
MARGIN = 100
RADIUS = 185

TOP_COLOR = (74, 95, 193)      # indigo
BOTTOM_COLOR = (36, 45, 99)    # deep indigo
BUBBLE_A = (255, 255, 255, 235)
BUBBLE_B = (159, 180, 255, 210)
CENTER_DOT = (36, 45, 99, 255)


def gradient_squircle() -> Image.Image:
    """Vertical gradient clipped to a rounded rectangle with margin."""
    grad = Image.new("RGBA", (SIZE, SIZE))
    draw = ImageDraw.Draw(grad)
    span = SIZE - 2 * MARGIN
    for y in range(MARGIN, SIZE - MARGIN):
        t = (y - MARGIN) / span
        color = tuple(
            round(TOP_COLOR[i] + (BOTTOM_COLOR[i] - TOP_COLOR[i]) * t)
            for i in range(3)
        )
        draw.line([(MARGIN, y), (SIZE - MARGIN, y)], fill=color + (255,))

    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [MARGIN, MARGIN, SIZE - MARGIN, SIZE - MARGIN],
        radius=RADIUS, fill=255,
    )
    out = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out


def bubble(draw: ImageDraw.ImageDraw, box: tuple, tail: list, color: tuple) -> None:
    """A rounded speech bubble with a triangular tail."""
    draw.rounded_rectangle(box, radius=90, fill=color)
    draw.polygon(tail, fill=color)


def main() -> None:
    img = gradient_squircle()
    overlay = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Left bubble, tail pointing down-left
    bubble(
        draw,
        (215, 280, 620, 560),
        [(300, 540), (270, 660), (420, 560)],
        BUBBLE_B,
    )
    # Right bubble, tail pointing down-right, overlapping the first
    bubble(
        draw,
        (400, 420, 810, 700),
        [(730, 680), (760, 800), (610, 700)],
        BUBBLE_A,
    )
    # Consensus dot in the overlap zone
    draw.ellipse((465, 455, 555, 545), fill=CENTER_DOT)

    img = Image.alpha_composite(img, overlay)
    img.save("icon_1024.png")
    print("Wrote icon_1024.png")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write the icns converter**

Create `packaging/macos/make_icns.sh`:

```bash
#!/usr/bin/env bash
# Generate Consensus.icns from make_icon.py output (macOS only: sips, iconutil).
set -euo pipefail
cd "$(dirname "$0")"

python3 make_icon.py

rm -rf Consensus.iconset
mkdir Consensus.iconset

# Canonical iconset entries (name -> pixel size)
sips -z 16 16     icon_1024.png --out Consensus.iconset/icon_16x16.png      >/dev/null
sips -z 32 32     icon_1024.png --out Consensus.iconset/icon_16x16@2x.png   >/dev/null
sips -z 32 32     icon_1024.png --out Consensus.iconset/icon_32x32.png      >/dev/null
sips -z 64 64     icon_1024.png --out Consensus.iconset/icon_32x32@2x.png   >/dev/null
sips -z 128 128   icon_1024.png --out Consensus.iconset/icon_128x128.png    >/dev/null
sips -z 256 256   icon_1024.png --out Consensus.iconset/icon_128x128@2x.png >/dev/null
sips -z 256 256   icon_1024.png --out Consensus.iconset/icon_256x256.png    >/dev/null
sips -z 512 512   icon_1024.png --out Consensus.iconset/icon_256x256@2x.png >/dev/null
sips -z 512 512   icon_1024.png --out Consensus.iconset/icon_512x512.png    >/dev/null
cp icon_1024.png Consensus.iconset/icon_512x512@2x.png

iconutil -c icns Consensus.iconset -o Consensus.icns
rm -rf Consensus.iconset
echo "Wrote $(pwd)/Consensus.icns"
```

```bash
chmod +x packaging/macos/make_icns.sh
```

- [ ] **Step 3: Generate and verify**

```bash
packaging/macos/make_icns.sh
file packaging/macos/Consensus.icns
```

Expected: `…Consensus.icns: Mac OS X icon, …` and the script prints the
output path. Visually inspect the PNG:

```bash
open packaging/macos/icon_1024.png
```

Expected: two overlapping speech bubbles with a dot in the overlap on an
indigo rounded square. (Aesthetic tweaks to coordinates/colors are fine —
keep the file paths and output names identical.)

- [ ] **Step 4: Ignore intermediates, commit the source + icns**

Append to `.gitignore`:

```
# macOS icon build intermediates
packaging/macos/Consensus.iconset/
packaging/macos/icon_1024.png
```

```bash
git add .gitignore packaging/macos/make_icon.py packaging/macos/make_icns.sh packaging/macos/Consensus.icns
git commit -m "feat: generated macOS app icon"
```

---

### Task 6: PyInstaller bundle (two executables) + entitlements

**Files:**
- Create: `packaging/macos/launch_app.py`
- Create: `packaging/macos/launch_worker.py`
- Create: `packaging/macos/consensus.spec`
- Create: `packaging/macos/entitlements.plist`
- Modify: `.gitignore` (`.build-venv/`)

**Interfaces:**
- Consumes: `consensus.desktop.launch_desktop(debug: bool = False)`, `consensus.sandbox_worker.main()`, `consensus.frozen.worker_command()` contract (worker binary sibling to GUI binary), `packaging/macos/Consensus.icns` (Task 5).
- Produces: `build/macos/dist/Consensus.app` with `Contents/MacOS/Consensus` (GUI) and `Contents/MacOS/consensus-worker` (console). Task 7's script invokes PyInstaller with this spec.

- [ ] **Step 1: Write the two entry scripts**

Create `packaging/macos/launch_app.py`:

```python
"""GUI entry point for the frozen Consensus.app bundle."""

from consensus.config import load_env
from consensus.desktop import launch_desktop


def main() -> None:
    load_env()
    launch_desktop()


if __name__ == "__main__":
    main()
```

Create `packaging/macos/launch_worker.py`:

```python
"""Console entry point for the bundled sandbox worker executable."""

from consensus.sandbox_worker import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write the entitlements**

Create `packaging/macos/entitlements.plist` (required so notarized CPython
can use ctypes/libffi and load its bundled dylibs):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
    <true/>
    <key>com.apple.security.cs.disable-library-validation</key>
    <true/>
</dict>
</plist>
```

- [ ] **Step 3: Write the PyInstaller spec**

Create `packaging/macos/consensus.spec`:

```python
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

from PyInstaller.utils.hooks import collect_all

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
worker_a = Analysis(
    [str(SPEC_DIR / "launch_worker.py")],
    pathex=[str(REPO_ROOT)],
    hiddenimports=["numpy", "PIL"],
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
```

- [ ] **Step 4: Build unsigned and verify the bundle shape**

```bash
uv venv .build-venv
uv pip install --python .build-venv/bin/python . pyinstaller
.build-venv/bin/python -m PyInstaller --noconfirm \
    --distpath build/macos/dist --workpath build/macos/work \
    packaging/macos/consensus.spec
test -x "build/macos/dist/Consensus.app/Contents/MacOS/Consensus" && echo "GUI exe OK"
test -x "build/macos/dist/Consensus.app/Contents/MacOS/consensus-worker" && echo "worker exe OK"
```

Expected: `GUI exe OK` and `worker exe OK`.

- [ ] **Step 5: Smoke-test the frozen worker binary**

The worker reads code on stdin and prints a JSON result:

```bash
echo 'print(2 + 2)' | "build/macos/dist/Consensus.app/Contents/MacOS/consensus-worker"
```

Expected: a single JSON line whose stdout field contains `4` (e.g.
`{"stdout": "4\n", …}`).

Also verify numpy is importable inside the frozen worker (needed for
sandboxed scientific code):

```bash
echo 'import numpy; print(numpy.__version__)' | "build/macos/dist/Consensus.app/Contents/MacOS/consensus-worker"
```

Expected: JSON with a numpy version string in stdout, no error field.

- [ ] **Step 6: Smoke-test the GUI app**

```bash
open build/macos/dist/Consensus.app
```

Expected: the Consensus window opens with the setup UI (this is unsigned —
Gatekeeper does not quarantine locally-built apps). Quit the app afterwards.
If the window fails to open, run the binary directly to see the traceback:

```bash
"build/macos/dist/Consensus.app/Contents/MacOS/Consensus"
```

- [ ] **Step 7: Ignore the build venv and commit**

Append to `.gitignore`:

```
.build-venv/
```

(`build/` and `dist/` are already ignored.)

```bash
git add .gitignore packaging/macos/launch_app.py packaging/macos/launch_worker.py \
        packaging/macos/consensus.spec packaging/macos/entitlements.plist
git commit -m "feat: PyInstaller spec for macOS app bundle with sandbox worker"
```

---

### Task 7: DMG build script (sign → notarize → staple → DMG)

**Files:**
- Create: `scripts/build_macos_dmg.sh` (mode 755)

**Interfaces:**
- Consumes: `packaging/macos/consensus.spec` (Task 6), `packaging/macos/entitlements.plist` (Task 6), `packaging/macos/make_icns.sh` (Task 5), version-sed contract (Task 2).
- Produces: `build/macos/Consensus-<version>.dmg`, signed, notarized, stapled.

- [ ] **Step 1: Write the script**

Create `scripts/build_macos_dmg.sh`:

```bash
#!/usr/bin/env bash
# Build, sign, notarize, and package Consensus.app into a distributable DMG.
#
# One-time setup:
#   1. Ensure your "Developer ID Application" certificate is in the login
#      keychain (Xcode -> Settings -> Accounts -> Manage Certificates).
#   2. Store notarization credentials (use an app-specific password from
#      https://account.apple.com, and your Team ID from the Apple Developer
#      membership page):
#        xcrun notarytool store-credentials consensus-notary \
#            --apple-id YOUR_APPLE_ID --team-id YOUR_TEAM_ID
#
# Usage:
#   scripts/build_macos_dmg.sh                  # full signed + notarized DMG
#   scripts/build_macos_dmg.sh --skip-notarize  # signed only (local testing)
#
# Environment overrides:
#   CODESIGN_IDENTITY   e.g. "Developer ID Application: Jane Doe (TEAMID123)"
#                       (default: first Developer ID Application in keychain)
#   NOTARY_PROFILE      notarytool keychain profile (default: consensus-notary)
set -euo pipefail

cd "$(dirname "$0")/.."

SKIP_NOTARIZE=0
[[ "${1:-}" == "--skip-notarize" ]] && SKIP_NOTARIZE=1

VERSION=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' consensus/__init__.py)
[[ -n "$VERSION" ]] || { echo "ERROR: could not read version"; exit 1; }
APP="build/macos/dist/Consensus.app"
DMG="build/macos/Consensus-${VERSION}.dmg"
ENTITLEMENTS="packaging/macos/entitlements.plist"
NOTARY_PROFILE="${NOTARY_PROFILE:-consensus-notary}"

# --- 0. Icon (generate if missing) ------------------------------------------
[[ -f packaging/macos/Consensus.icns ]] || packaging/macos/make_icns.sh

# --- 1. Build the .app in a fresh venv ---------------------------------------
rm -rf build/macos .build-venv
uv venv .build-venv
uv pip install --python .build-venv/bin/python . pyinstaller
.build-venv/bin/python -m PyInstaller --noconfirm \
    --distpath build/macos/dist --workpath build/macos/work \
    packaging/macos/consensus.spec
[[ -d "$APP" ]] || { echo "ERROR: $APP was not produced"; exit 1; }

# --- 2. Codesign inside-out ---------------------------------------------------
if [[ -z "${CODESIGN_IDENTITY:-}" ]]; then
    # Capture first, then scan without an early-exit consumer: piping the
    # producer into `head -1` can SIGPIPE it under pipefail (same bug class
    # as the release script's wheel check).
    IDENTITIES=$(security find-identity -v -p codesigning)
    CODESIGN_IDENTITY=$(awk -F'"' '/Developer ID Application/ && !found {print $2; found=1}' <<< "$IDENTITIES")
fi
[[ -n "$CODESIGN_IDENTITY" ]] || {
    echo "ERROR: no 'Developer ID Application' identity in keychain."
    echo "Set CODESIGN_IDENTITY or install your certificate via Xcode."
    exit 1
}
echo "Signing with: $CODESIGN_IDENTITY"

# Nested libraries first (no entitlements on plain dylibs) ...
find "$APP" -type f \( -name "*.dylib" -o -name "*.so" \) -print0 \
    | xargs -0 -I{} codesign --force --timestamp --options runtime \
        --sign "$CODESIGN_IDENTITY" "{}"
# ... then the auxiliary executable, then the bundle (signs the main exe).
codesign --force --timestamp --options runtime \
    --entitlements "$ENTITLEMENTS" --sign "$CODESIGN_IDENTITY" \
    "$APP/Contents/MacOS/consensus-worker"
codesign --force --timestamp --options runtime \
    --entitlements "$ENTITLEMENTS" --sign "$CODESIGN_IDENTITY" "$APP"
codesign --verify --strict --deep "$APP"
echo "Codesign OK"

# --- 3. Notarize + staple the app --------------------------------------------
if [[ $SKIP_NOTARIZE -eq 0 ]]; then
    ditto -c -k --keepParent "$APP" build/macos/Consensus.zip
    xcrun notarytool submit build/macos/Consensus.zip \
        --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$APP"
fi

# --- 4. Build the DMG ---------------------------------------------------------
STAGE="build/macos/dmg-stage"
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "Consensus" -srcfolder "$STAGE" -ov -format UDZO "$DMG"

# --- 5. Sign, notarize, staple the DMG ----------------------------------------
codesign --force --timestamp --sign "$CODESIGN_IDENTITY" "$DMG"
if [[ $SKIP_NOTARIZE -eq 0 ]]; then
    xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$DMG"
    xcrun stapler validate "$DMG"
    spctl -a -vv "$APP"
fi

echo "Done: $DMG"
```

```bash
chmod +x scripts/build_macos_dmg.sh
```

- [ ] **Step 2: Test the signed (non-notarized) path**

Run: `scripts/build_macos_dmg.sh --skip-notarize`
Expected: ends with `Done: build/macos/Consensus-1.99.0.dmg`; the log shows
`Signing with: Developer ID Application: …` and `Codesign OK`. Verify:

```bash
codesign --verify --strict --deep build/macos/dist/Consensus.app && echo "verified"
hdiutil verify build/macos/Consensus-1.99.0.dmg
```

Expected: `verified`, and hdiutil reports the checksum is valid.

If no `Developer ID Application` identity exists on the build machine, the
script must fail with the explicit error message — confirm that behavior
matches instead, and flag it to the user rather than ad-hoc signing.

- [ ] **Step 3: Full notarized run (requires one-time credential setup)**

If the `consensus-notary` profile is not yet stored, this step needs the
user's Apple ID + app-specific password — pause and ask them to run the
`store-credentials` command from the script header, then:

Run: `scripts/build_macos_dmg.sh`
Expected: two `notarytool submit … --wait` invocations each report
`status: Accepted`; `spctl -a -vv` prints `accepted` with
`source=Notarized Developer ID`; final line `Done: build/macos/Consensus-1.99.0.dmg`.

- [ ] **Step 4: Manual GUI verification of the notarized app**

```bash
open build/macos/dist/Consensus.app
```

Manually verify in the running app: the window opens, a discussion can be
created, and an `execute_python` turn works (proves the signed frozen worker
runs under the hardened runtime). This is the user-facing acceptance check —
report results, and if sandbox-exec is blocked by signing, capture the error
text for follow-up.

- [ ] **Step 5: Commit**

```bash
git add scripts/build_macos_dmg.sh
git commit -m "feat: signed + notarized macOS DMG build script"
```

---

### Task 8: Alpha tester documentation

**Files:**
- Create: `docs/alpha_testing.md`

**Interfaces:**
- Consumes: install channels produced by Tasks 2-7.

- [ ] **Step 1: Write the tester guide**

Create `docs/alpha_testing.md`:

````markdown
# Consensus Alpha Testing Guide

Thanks for helping test Consensus! Two ways to install — pick one.

## Option 1: macOS app (easiest)

1. Download `Consensus-<version>.dmg` from the
   [releases page](https://github.com/hherb/consensus/releases).
2. Open the DMG and drag **Consensus** into **Applications**.
3. Launch Consensus from Applications or Spotlight.

The app is signed and notarized — it should open without warnings.

## Option 2: Python package (macOS, Linux, Windows)

Requires Python 3.11+. Install [uv](https://docs.astral.sh/uv/) first:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then:

```bash
uv tool install consensus-app
consensus            # desktop app
consensus --web      # or: browser UI at http://127.0.0.1:8080
```

Upgrade later with `uv tool upgrade consensus-app`.

> Linux desktop mode needs GTK/WebKit libraries — see the README's
> installation section. If in doubt, use `consensus --web`.

## First-run setup

1. Open the **Providers** tab and add an API key for at least one provider
   (OpenRouter recommended — one key, many models).
2. Create entities (AI participants) in the **Profiles** tab, or use the
   defaults.
3. Start a discussion from the **New Discussion** tab.

## Where your data lives

- macOS: `~/Library/Application Support/consensus/`
- Linux: `~/.local/share/consensus/`

Delete that directory to reset the app completely.

## Known limitations (alpha)

- The Mac app cannot install extra Python packages for the
  `install_python_package` tool — use the `uv tool install` variant if a
  discussion needs additional libraries for code execution.
- First launch may take a few seconds while the database is created.

## Reporting problems

File issues at <https://github.com/hherb/consensus/issues> with:

- What you did, what you expected, what happened instead
- Your platform (macOS version / Linux distro) and install method (DMG or PyPI)
- Any error text. For the Mac app, logs appear in Console.app; for the CLI,
  copy the terminal output.
````

- [ ] **Step 2: Link it from the README**

In `README.md`, at the end of the Installation section (after the from-source
block added in Task 2), append:

```markdown
Alpha testers: see [docs/alpha_testing.md](docs/alpha_testing.md) for a
step-by-step install and feedback guide.
```

- [ ] **Step 3: Commit**

```bash
git add docs/alpha_testing.md README.md
git commit -m "docs: alpha tester installation guide"
```

---

## Final verification (after all tasks)

- [ ] `python -m pytest tests/ -q` — full suite green.
- [ ] `scripts/release_pypi.sh --build-only` — wheel builds and content checks pass.
- [ ] `scripts/build_macos_dmg.sh` — notarized DMG produced; `spctl -a -vv` accepted.
- [ ] Manual: install the DMG on the dev machine (drag to /Applications), launch, run a short discussion with an `execute_python` turn.
- [ ] Publishing to PyPI (`scripts/release_pypi.sh`) and uploading the DMG to a GitHub Release are done by the user when ready — do not publish without their go-ahead.
