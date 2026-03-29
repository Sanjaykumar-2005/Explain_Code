#!/usr/bin/env bash
# Git Bash / WSL: run from repo root with `bash backend/run-dev.sh` or `cd backend && bash run-dev.sh`
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/Scripts/python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
