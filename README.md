<div align="center">

# DeepReviewer-2.0 (Backend-Only, Minimal OSS)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](./LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Backend Only](https://img.shields.io/badge/Mode-Backend_Only-0ea5e9?style=for-the-badge)](#)

Open-source minimal backend for the DeepReviewer-2.0 pipeline:

**PDF -> MinerU Markdown -> Review Agent Tool Loop -> Final Markdown -> Final PDF**

No frontend, no database, no uvicorn required.
Run directly with `python main.py ...`.

[中文文档](./README.zh-CN.md)

[Quick Start](#quick-start) •
[Configuration](#configuration) •
[CLI Usage](#cli-usage) •
[Output Format](#output-format) •
[External Services](#external-services) •
[Troubleshooting](#troubleshooting)

</div>

---

## Features

- Minimal backend-only architecture (local JSON storage)
- End-to-end asynchronous job execution
- Real-time status polling via CLI
- Strict review-tool workflow with MCP-like tools
- Usage accounting:
  - token usage
  - tool usage (per-tool counts)
  - paper-search usage
- Final artifacts:
  - `final_report.md`
  - `final_report.pdf` (website-style export with source-paper appendix + auto annotation overlays)

---

## Architecture

Each job is isolated under:

```text
data/jobs/<job_id>/
```

Core flow:

1. Submit PDF
2. Upload and parse with MinerU (v4 API)
3. Build review runtime context and run agent
4. Agent uses tools (`pdf_read_lines`, `pdf_annotate`, `paper_search`, ...)
5. Persist final markdown via `review_final_markdown_write`
6. Export final report PDF with website-aligned renderer:
   - branded cover (logo)
   - token usage summary (no points)
   - appended original source PDF pages
   - auto overlay callouts from review annotations

---

## Requirements

- Python `>=3.11`
- Linux/macOS (Windows also works with command adjustments)
- Network access to:
  - LLM endpoint (OpenAI-compatible)
  - MinerU (recommended production parser)
  - Optional paper search/read service (PASA or compatible)

---

## Quick Start

### 1) Install

```bash
cd <repo_root>
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

### 2) Configure

```bash
cp .env.example .env
```

Minimum practical setup:

```bash
# LLM (OpenAI-compatible)
BASE_URL=http://127.0.0.1:8004/v1
AGENT_MODEL=gpt-5.2
# Use Responses API when your provider supports it (otherwise keep false)
OPENAI_USE_RESPONSES_API=false
# API key is optional for local gateways without auth
# OPENAI_API_KEY=...
# Recommended for local gateways (prevents OpenAI tracing 401 log noise)
OPENAI_AGENTS_DISABLE_TRACING=1

# MinerU
MINERU_API_TOKEN=your_mineru_token

# Optional PASA adapter
PAPER_SEARCH_BASE_URL=http://127.0.0.1:8001
# Optional for local PASA without auth
PAPER_SEARCH_API_KEY=
PAPER_SEARCH_ENDPOINT=/pasa/search
```

### 3) Submit and track a job

```bash
python main.py submit --pdf /path/to/paper.pdf --wait-seconds 0
python main.py status --job-id <job_id>
python main.py watch --job-id <job_id> --interval 2 --timeout 1800
```

### 4) Fetch result

```bash
python main.py result --job-id <job_id> --format all
python main.py result --job-id <job_id> --format md
python main.py result --job-id <job_id> --format pdf
```

---

## Configuration

Copy `.env.example` to `.env`.

### LLM (OpenAI-compatible)

DeepReviewer-2.0 now supports generic OpenAI-compatible providers.
In most local deployments, setting only `BASE_URL` is enough.

- `BASE_URL` (preferred)
- `OPENAI_BASE_URL` (alias)
- `LLM_BASE_URL` (alias)
- `OPENAI_API_KEY` / `API_KEY` / `LLM_API_KEY` (optional if your gateway requires auth)
- `AGENT_MODEL` (default: `gpt-5.2`)
- `OPENAI_USE_RESPONSES_API` (default: `false`; set `true` to use OpenAI Responses API)
- `AGENT_RESUME_ATTEMPTS` (hard capped to 2)
- `OPENAI_AGENTS_DISABLE_TRACING` (recommended `1` for local OpenAI-compatible endpoints)

### Finalization gates

- `ENABLE_FINAL_GATES` (default `false`; set `true` to enforce backend final-write gates)
- `MIN_PAPER_SEARCH_CALLS_FOR_PDF_ANNOTATE` (default `3`)
- `MIN_PAPER_SEARCH_CALLS_FOR_FINAL` (default `3`)
- `MIN_DISTINCT_PAPER_QUERIES_FOR_FINAL` (default `3`)
- `MIN_ANNOTATIONS_FOR_FINAL` (default `10`)
- `MIN_ENGLISH_WORDS_FOR_FINAL` (default `0`, disabled)
- `FORCE_ENGLISH_OUTPUT` (default `true`)

### MinerU

- `MINERU_BASE_URL` (default `https://mineru.net/api/v4`)
- `MINERU_API_TOKEN`
- `MINERU_MODEL_VERSION` (default `vlm`)
- `MINERU_ALLOW_LOCAL_FALLBACK` (default `false`)

### Paper search/read

- `PAPER_SEARCH_BASE_URL`
- `PAPER_SEARCH_API_KEY` (optional for local PASA without auth)
- `PAPER_SEARCH_ENDPOINT` (default `/pasa/search`)
- `PAPER_READ_BASE_URL`
- `PAPER_READ_API_KEY`
- `PAPER_READ_ENDPOINT` (default `/read`)

If not configured, adapter falls back to arXiv metadata-level search/read.

---

## CLI Usage

### Submit

```bash
python main.py submit --pdf /path/to/paper.pdf --title "Optional title" --wait-seconds 10
```

Behavior:

- If completed within `wait-seconds`: returns `completed=true`
- Otherwise: returns current status + usage snapshot

### Status

```bash
python main.py status --job-id <job_id>
```

### Watch

```bash
python main.py watch --job-id <job_id> --interval 2 --timeout 1800
```

### Result

```bash
python main.py result --job-id <job_id> --format all
python main.py result --job-id <job_id> --format md
python main.py result --job-id <job_id> --format pdf
```

---

## Output Format

### `status` response (shape)

```json
{
  "job_id": "uuid",
  "status": "agent_running",
  "message": "...",
  "annotation_count": 0,
  "final_report_ready": false,
  "pdf_ready": false,
  "usage": {
    "token": {
      "requests": 0,
      "input_tokens": 0,
      "output_tokens": 0,
      "total_tokens": 0
    },
    "tool": {
      "total_calls": 0,
      "distinct_tools": 0,
      "per_tool": {}
    },
    "paper_search": {
      "total_calls": 0,
      "successful_calls": 0,
      "effective_calls": 0,
      "papers_found": 0,
      "distinct_queries": 0
    }
  },
  "artifacts": {
    "final_markdown_path": null,
    "report_pdf_path": null
  }
}
```

### Completed artifacts

- `data/jobs/<job_id>/final_report.md`
- `data/jobs/<job_id>/final_report.pdf`
- `data/jobs/<job_id>/events.jsonl`

`final_report.pdf` includes:
- Final markdown report section
- Token usage summary (input/output/total/requests)
- Original paper appendix pages
- Auto-rendered review overlays (when MinerU line bboxes are available)

### Export file formats

`final_report.md` (primary machine-readable review output):

- UTF-8 markdown text generated by `review_final_markdown_write`
- Contains the full final review content used for archive/export
- Designed to be directly reusable in downstream pipelines (RAG, QA, post-editing)

`final_report.pdf` (human-facing publication/export output):

- Cover page with report identity and run metadata
- Token usage fields (no points billing fields)
- Rendered final markdown body
- Appended original source PDF pages
- Automatic overlay callouts mapped from review annotations to source-page regions
- Footer/header branding and logo assets from `assets/logo-small.png` / `assets/logo.png`

### Job directory files and their roles

Each run is persisted in:

```text
data/jobs/<job_id>/
```

Main files:

- `job.json`: authoritative job state snapshot (status, usage, artifact paths, metadata)
- `events.jsonl`: append-only event timeline (status transitions, tool calls, export stats, failures)
- `source.pdf`: original uploaded paper
- `mineru_full.md`: MinerU full markdown parse result of source PDF
- `mineru_content_list.json`: MinerU structured layout list (page/text/bbox), used for annotation overlay mapping
- `mineru_result_raw.json`: raw MinerU response payload for debugging/audit
- `annotations.json`: normalized review annotations created by `pdf_annotate`
- `agent_prompt.txt`: frozen prompt snapshot used for that run (prompt parity auditing)
- `final_report.md`: final markdown report persisted by `review_final_markdown_write`
- `final_report.pdf`: final exported PDF report (report body + source appendix + overlay callouts)
- `worker.stdout.log`: worker stdout log (usually minimal)
- `worker.stderr.log`: worker stderr log (runtime warnings/errors, useful for diagnosis)

Possible optional files (depending on model/runtime behavior):

- `agent_final_output.txt`: last raw model final output
- `agent_final_output_attempt_<n>.txt`: per-attempt raw model outputs in resume mode

---

## External Services

### MinerU (required in strict mode)

1. Register at: `https://mineru.net/`
2. Generate API token in dashboard
3. Set `MINERU_API_TOKEN` in `.env`

### PASA (recommended for strong paper retrieval)

- Chinese local run guide in this repo: `pasa/README.zh-CN.md`
- Local run guide in this repo: `pasa/README.md`
- Official repo: `https://github.com/bytedance/pasa`
- Official README: `https://github.com/bytedance/pasa/blob/main/README.md`
- Set up models and serving stack per PASA docs + local integration guide above
- Get Serper token from: `https://serper.dev/` (required by PASA's Google search workflow)

Expose compatible HTTP endpoints (directly or via adapter layer):

- `POST /pasa/search` (default in this repo)
- `POST /search` (optional compatibility layer)

Then point this project via `PAPER_SEARCH_*` and `PAPER_READ_*`.

---

## Notes on Prompt Parity

This repository keeps the review agent **system prompt** aligned with the source DeepReviewer logic.
The initial **user prompt** is also exactly the same full review prompt string.

---

## Troubleshooting

- `RuntimeError: Agent finished without successful review_final_markdown_write`
  - Model ended before final write gate.
  - Check `events.jsonl` for phase progression and tool usage.

- Repeated final-write attempts
  - Latest code includes short-circuit and cancellation guards after final persistence.

- MinerU timeout/failure
  - Verify token validity and endpoint reachability.

- PASA timeout/failure
  - Verify service health and endpoint path (`/pasa/search` vs `/search`).

---

## Development

Run static sanity check:

```bash
python -m compileall deepreview main.py
```

---

## License

MIT License. See `LICENSE`.
Third-party attributions: see `THIRD_PARTY_NOTICES.md`.
