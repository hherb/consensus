#!/usr/bin/env bash
# Generate Consensus.icns from make_icon.py output (macOS only: sips, iconutil).
set -euo pipefail
cd "$(dirname "$0")"

# Ephemeral uv environment: the system python3 may not have Pillow, and
# --no-project keeps uv from syncing the whole repo env just for the icon.
uv run --no-project --with pillow python make_icon.py

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
cp icon_1024.png Consensus.iconset/icon_512x512@2x.png

iconutil -c icns Consensus.iconset -o Consensus.icns
rm -rf Consensus.iconset
echo "Wrote $(pwd)/Consensus.icns"
