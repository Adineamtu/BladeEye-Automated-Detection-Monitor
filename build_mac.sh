#!/usr/bin/env bash
set -euo pipefail

# Build frontend assets
npm --prefix frontend ci
npm --prefix frontend run build

# Package backend into a single executable
pyinstaller --clean --onefile --add-data "frontend/dist:frontend/dist" api.py

# Archive the binary
mkdir -p release
cp dist/api release/
tar -czf reactive_jam_mac.tar.gz -C release api
rm -r release

echo "Created reactive_jam_mac.tar.gz"
