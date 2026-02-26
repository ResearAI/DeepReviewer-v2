#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[1/7] Python syntax check"
python -m py_compile \
  deepreview/storage.py \
  deepreview/state.py \
  deepreview/adapters/paper_search.py \
  deepreview/tools/review_tools.py \
  deepreview/runner.py \
  pasa/pasa_server.py \
  pasa/pasa/utils.py

echo "[2/7] Secret pattern scan"
if rg -n --hidden --glob '!.git/**' --glob '!.venv/**' --glob '!scripts/release_audit.sh' \
  "(sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|BEGIN RSA PRIVATE KEY|BEGIN OPENSSH PRIVATE KEY|ghp_[A-Za-z0-9]{20,}|MINERU_API_TOKEN=eyJ|OPENAI_API_KEY=sk-)" .; then
  echo "Secret-like content detected. Please redact before release."
  exit 1
fi

echo "[3/7] Local env file check"
for f in .env local.env pasa/.pasa_env pasa/.pasa_env.local; do
  if [ -f "$f" ]; then
    echo "Release-blocker: local runtime env file exists: $f"
    echo "Remove it from the release package (keep only templates)."
    exit 1
  fi
done

echo "[4/7] Generated artifact check"
if [ -d data/jobs ] && find data/jobs -mindepth 1 ! -name '.gitkeep' | grep -q .; then
  echo "Release-blocker: data/jobs contains runtime artifacts."
  echo "Clean it and keep only data/jobs/.gitkeep."
  exit 1
fi
if [ -d data/input ] && find data/input -mindepth 1 | grep -q .; then
  echo "Release-blocker: data/input contains runtime files."
  exit 1
fi
if [ -d deepreview2_backend.egg-info ]; then
  echo "Release-blocker: deepreview2_backend.egg-info should not be published."
  exit 1
fi
if [ -d backend/.cache ] && find backend/.cache -type f | grep -q .; then
  echo "Release-blocker: backend/.cache contains generated files."
  exit 1
fi

echo "[5/7] Check required docs"
test -f README.md
test -f README.zh-CN.md
test -f THIRD_PARTY_NOTICES.md

echo "[6/7] Quick CLI safety check"
python main.py status --job-id invalid-job-id >/dev/null 2>&1 || true

echo "[7/7] README path sanity check"
if rg -n "/home/|/ssdwork/" README.md README.zh-CN.md pasa/README.md pasa/README.zh-CN.md pasa/PASA_DECOUPLING_README.md; then
  echo "Release-blocker: absolute internal paths found in docs."
  exit 1
fi

echo "release_audit: OK"
