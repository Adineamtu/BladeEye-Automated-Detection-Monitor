#!/usr/bin/env bash
set -euo pipefail

# Build frontend assets
npm --prefix frontend ci
npm --prefix frontend run build

# Package backend into a single executable
pyinstaller --clean --onefile --add-data "frontend/dist;frontend/dist" api.py

# Archive the binary
mkdir -p release
cp dist/api.exe release/
zip -j reactive_jam_windows.zip release/api.exe
rm -r release

echo "Created reactive_jam_windows.zip"
