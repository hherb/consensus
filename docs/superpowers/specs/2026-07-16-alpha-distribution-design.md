# Alpha Distribution: macOS App + PyPI Package — Design

**Date:** 2026-07-16
**Status:** Approved
**Goal:** Enable alpha testers to install Consensus either as a normal Mac app
(signed, notarized DMG) or via `uv tool install consensus-app` / `pip install
consensus-app`.

## Decisions (confirmed with user)

| Decision | Choice |
|---|---|
| Code signing | Signed + notarized (user has an Apple Developer ID in Xcode/Keychain) |
| PyPI distribution name | `consensus-app` (`consensus` is taken by an active geospatial package; CLI command and import name stay `consensus`) |
| Default dependencies | Batteries-included: base deps become the current `[all]` set |
| Build method | Local shell scripts run on the developer's Mac (no CI for now) |
| macOS bundler | PyInstaller (best-trodden path for pywebview apps) |
| Eval harness | Move top-level `evaluation` package to `consensus.evaluation` (avoids squatting a generic import name on PyPI) |

## Part A — PyPI package `consensus-app`

### pyproject.toml changes

- `name = "consensus-app"`. The console scripts (`consensus`, `consensus-mcp`)
  and the import package (`consensus`) are unchanged.
- Base `dependencies` absorb the current `[all]` set, preserving the
  Linux-only environment markers (`PyGObject`, `pycairo`). A plain
  `uv tool install consensus-app` yields the complete desktop + web app.
- Existing extras (`desktop`, `web`, `tools`, `memory`, `documents`,
  `images`, `all`) remain defined as empty lists so documented commands like
  `uv pip install -e ".[all]"` keep working.
- Add metadata: `readme = "README.md"`, `authors`, `keywords`, classifiers
  including `Development Status :: 3 - Alpha`,
  `License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)`,
  and `Programming Language :: Python :: 3.11/3.12/3.13`.
- Add `[project.urls]`: Homepage / Repository / Issues →
  `https://github.com/hherb/consensus`.
- `packages.find` includes only `consensus*` once the evaluation move (below)
  lands; the `evaluation` package-data entry moves with it.

### Move `evaluation` → `consensus.evaluation`

The repo currently ships a top-level `evaluation` package in the wheel, which
would squat a generic import name on PyPI. Mechanical move:
`evaluation/` → `consensus/evaluation/` (including its `migrations/`), update
the two guarded import sites (`consensus/server.py`,
`consensus/desktop.py`), internal absolute imports, any test imports, and
the `evaluation` entries in `packages.find` / `package-data`. Both consumer
imports are try/except-guarded, so behavior is identical after the move.

### Versioning policy

Plain PEP 440 releases (`1.99.0`, `1.99.1`, …), **not** pre-release tags like
`2.0.0a1`. Pre-release versions would force testers to pass
`--prerelease=allow` (uv) or `--pre` (pip), defeating "just works". Alpha
status is communicated via the Development Status classifier and README.
Version stays dynamic, read from `consensus.__version__`; bump `1.99` →
`1.99.0` for the first published build.

### Release script: `scripts/release_pypi.sh`

1. Clean `dist/`, run `uv build` (sdist + wheel).
2. Sanity-check wheel contents: must contain `consensus/static/index.html`
   and `consensus/migrations/*.sql`. Abort if missing.
3. `uv publish` — token via `UV_PUBLISH_TOKEN` env var. A `--test` flag
   publishes to TestPyPI instead.

### Documentation updates

README.md and CLAUDE.md install instructions gain/reflect
`uv tool install consensus-app` and `pip install consensus-app`.

## Part B — macOS app (PyInstaller → signed, notarized DMG)

### App icon

No logo exists anywhere in the repo. Create an original icon: SVG master →
rendered PNG set → `iconutil` → `Consensus.icns`. Sources and the generation
script live in `packaging/macos/`.

### Product-code changes (the only two)

1. **Sandbox worker launch** (`consensus/tools_python.py`): currently
   `sys.executable -m consensus.sandbox_worker`. In a frozen app
   `sys.executable` is the GUI binary, which would relaunch the app. Fix:
   when `getattr(sys, "frozen", False)`, launch the bundled console
   executable `consensus-worker` (second EXE target in the same .app) with
   the same stdin/stdout JSON protocol. Non-frozen behavior is unchanged.
2. **`install_python_package`** (`consensus/tools_python.py`): runs
   `uv pip install` into the live environment — impossible in a frozen
   bundle and `uv` may not exist on tester machines. When frozen, return a
   clean `ToolResult` error: package installation is not available in the
   Mac app; use the PyPI install instead.

### PyInstaller spec: `packaging/macos/consensus.spec`

- Launcher script `packaging/macos/launch_app.py` calls
  `consensus.desktop.launch_desktop()` directly (no argparse; a .app gets no
  CLI arguments).
- Two EXE targets in one BUNDLE:
  - `Consensus` — windowed GUI entry.
  - `consensus-worker` — console entry running
    `consensus.sandbox_worker` main, used by the sandboxed `execute_python`
    tool.
- Data collection: `consensus/static` and `consensus/migrations` collected to
  identical relative destinations, so existing `__file__`-relative resolution
  (`desktop.py`, `migrator.py`) works unmodified. `collect_all` for
  `sqlite_vec` (bundled loadable extension), `trafilatura`, `pdfplumber`,
  `certifi`. `numpy` / `Pillow` / `aiohttp` are covered by PyInstaller's
  built-in hooks.
- BUNDLE settings: name `Consensus.app`, bundle identifier
  `io.github.hherb.consensus`, icon `Consensus.icns`,
  `CFBundleShortVersionString` read from `consensus.__version__`, Info.plist
  keys `NSHighResolutionCapable = true`, `LSMinimumSystemVersion = 12.0`.

### Build script: `scripts/build_macos_dmg.sh`

`set -euo pipefail`; every step verified before the next.

1. Fresh build venv via uv; install project (full deps) + `pyinstaller`.
2. Run PyInstaller with the spec.
3. Codesign the .app inside-out with the Developer ID Application identity
   (auto-discovered via `security find-identity`, overridable by env var),
   hardened runtime enabled, entitlements file with
   `com.apple.security.cs.allow-unsigned-executable-memory` and
   `com.apple.security.cs.disable-library-validation` (required for
   CPython/ctypes under notarization).
4. Notarize the zipped .app: `xcrun notarytool submit --wait
   --keychain-profile consensus-notary`; then `xcrun stapler staple` the app.
   One-time credential setup (`xcrun notarytool store-credentials
   consensus-notary --apple-id … --team-id …`) is documented in the script
   header and the alpha-testing doc.
5. Build DMG with `hdiutil` (app + `/Applications` symlink), named
   `Consensus-<version>.dmg`.
6. Sign the DMG, notarize it, staple it.
7. Verify: `spctl -a -vv` on the app, `xcrun stapler validate` on both.

### Distribution

DMG uploaded manually (e.g. attached to a GitHub Release). Out of band for
the scripts.

## Testing

- **Wheel:** build, install into a scratch venv with uv, verify both entry
  points exist, smoke-run `consensus --web` (server starts, `/` serves the
  UI, health endpoint responds).
- **Mac app:** after build, `spctl` acceptance check (scripted); manual GUI
  smoke test including an `execute_python` turn to prove the frozen worker
  path works.
- **Unit tests:** frozen-mode branches in `tools_python.py` tested with
  monkeypatched `sys.frozen` (worker command construction; frozen
  `install_python_package` refusal). No existing tests should change.

## Out of scope

Windows/Linux installers, CI/GitHub Actions automation, auto-update
(Sparkle), Mac App Store distribution, Homebrew cask.

## Risks

- **PyInstaller hidden imports:** sqlite-vec / trafilatura / pdfplumber pull
  data files and dynamic imports; mitigated with `collect_all` and a manual
  smoke test of document ingestion in the bundled app.
- **Import-name collision:** the unrelated PyPI `consensus` package also
  installs an importable package with a similar name; irrelevant for
  `uv tool install` (isolated venv) and acceptable for alpha, noted in
  README.
- **Notarization failures:** usually caused by unsigned nested binaries;
  the inside-out signing step signs all Mach-O files found in the bundle
  before signing the .app itself.
