#!/bin/bash
set -euo pipefail

# 1. Start API in background
export PYTHONPATH="${PYTHONPATH:-}:."
python3 main.py --host 127.0.0.1 --port 8000 --center-freq 868000000
