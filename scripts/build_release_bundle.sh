#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_STAGE_DIR="/tmp/DeepReviewer-v2-release"
STAGE_DIR="${1:-$DEFAULT_STAGE_DIR}"

echo "[1/4] Prepare staged release directory: $STAGE_DIR"
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"

echo "[2/4] Copy project with release-safe filters"
rsync -a --delete \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '.pytest_cache/' \
  --exclude '.ruff_cache/' \
  --exclude '.mypy_cache/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.env' \
  --exclude 'local.env' \
  --exclude 'pasa/.pasa_env' \
  --exclude 'pasa/.pasa_env.local' \
  --exclude 'data/jobs/*' \
  --include 'data/jobs/.gitkeep' \
  --exclude 'data/input/*' \
  --exclude 'data/*.pdf' \
  --exclude '*.egg-info/' \
  --exclude 'backend/.cache/' \
  "$ROOT_DIR"/ "$STAGE_DIR"/

mkdir -p "$STAGE_DIR/data/jobs"
touch "$STAGE_DIR/data/jobs/.gitkeep"

echo "[3/4] Run release audit in staged directory"
(
  cd "$STAGE_DIR"
  bash scripts/release_audit.sh
)

echo "[4/4] Staged release ready"
echo "STAGE_DIR=$STAGE_DIR"
