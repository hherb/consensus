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
# `build/` is setuptools' staging tree and `*.egg-info/` its metadata cache;
# neither is cleaned between runs: a module deleted or moved since the last
# build (e.g. app_discussion_flow.py, replaced by the app_discussion_flow/
# package) survives in build/lib and is packaged into the new wheel alongside
# its replacement.  Clean all three.
rm -rf dist build ./*.egg-info
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

# The presence checks above cannot see a *stale* file, so assert separately
# that no name is shipped both as `X.py` and as the package `X/`.  That pair
# is what a dirty staging tree produces when a module is split into a
# package, and the shadowing `X.py` silently wins the import.  The cleanup
# above prevents it; this makes sure it can never come back unnoticed.
check_no_shadowed_packages() {
    local shadowed
    # `unzip -l` rows start with the size; the name is everything from the
    # 4th field on, taken by offset so paths containing spaces survive.
    shadowed=$(awk '$1 ~ /^[0-9]+$/ { print substr($0, index($0, $4)) }' <<< "$LISTING" | awk '
        { paths[NR] = $0 }
        END {
            for (i in paths) {
                module = paths[i]
                if (module !~ /\.py$/) continue
                package = module
                sub(/\.py$/, "/", package)
                for (j in paths)
                    if (index(paths[j], package) == 1) { print module; break }
            }
        }')
    [[ -z "$shadowed" ]] || {
        echo "ERROR: wheel ships a module shadowing a package of the same name:"
        echo "$shadowed" | sed 's/^/  /'
        exit 1
    }
}
check_no_shadowed_packages
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
