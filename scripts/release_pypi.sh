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
