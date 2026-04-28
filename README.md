<div align="center">

# <img src="assets/logo-small.png" alt="DeepReviewer Logo" width="36" valign="middle" /> DeepReviewer 2.0

[![ACL 2025](https://img.shields.io/badge/ACL-2025-1f6feb?style=for-the-badge)](https://aclanthology.org/2025.acl-long.1420/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](./LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)

**Human-like deep thinking process for LLM-based paper review.**  
`PDF -> MinerU Markdown -> Review Agent Tool Loop -> Final Markdown -> Final PDF`

[Quick Start](#quick-start) • [Online Platform](#online-platform-and-api) • [Technical Report](#technical-report) • [Configuration](#configuration) • [CLI Usage](#cli-usage) • [Citation](#citation)

[中文文档](./README.zh-CN.md)

</div>

---

## News

- **[2026-03-04]** The **DeepReviewer 2.0 online platform** is now live. It is free for all scholars. Try it out at [deepscientist.cc](https://deepscientist.cc).
- **[2026-03-04]** After registration, you can use the **DeepReviewer 2.0 API service**: [AI Review API Workflow](https://deepscientist.cc/docs/English/API/AI_Review_API_Workflow).
- **[2026-03-04]** Video walkthrough: [YouTube Demo](https://www.youtube.com/watch?v=mMg5XzcaDCw).

---

## Features

| Feature | Description |
| :--- | :--- |
| End-to-End Review | Runs full asynchronous review from uploaded PDF to final Markdown and PDF report. |
| Tool-Grounded Reasoning | Agent uses review tools (`pdf_read_lines`, `pdf_annotate`, `paper_search`, etc.) to produce traceable output. |
| Usage Accounting | Tracks token usage, per-tool call counts, and paper-search statistics for each job. |
| Publication-Style Export | Produces `final_report.pdf` with branding, usage summary, source-paper appendix, and annotation overlays. |

---

## How It Works

Each review job is persisted under:

```text
data/jobs/<job_id>/
```

Pipeline:

1. Submit source PDF.
2. Parse with MinerU v4 into markdown and layout metadata.
3. Build review runtime context and run the review agent.
4. Agent iterates with tools (`pdf_read_lines`, `pdf_annotate`, `paper_search`, ...).
5. Persist final markdown with `review_final_markdown_write`.
6. Export final PDF report with source appendix and overlay callouts.

---

## Online Platform And API

- Web platform: [https://deepscientist.cc](https://deepscientist.cc)
- API docs (registration required): [https://deepscientist.cc/docs/English/API/AI_Review_API_Workflow](https://deepscientist.cc/docs/English/API/AI_Review_API_Workflow)
- Demo video: [https://www.youtube.com/watch?v=mMg5XzcaDCw](https://www.youtube.com/watch?v=mMg5XzcaDCw)

---

## Technical Report

- PDF: [DeepReviewer-v2.pdf](./technical_report/DeepReviewer-v2.pdf)

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

Minimal practical setup:

```bash
# LLM (OpenAI-compatible)
BASE_URL=http://127.0.0.1:8004/v1
AGENT_MODEL=gpt-5.2
OPENAI_USE_RESPONSES_API=false
OPENAI_AGENTS_DISABLE_TRACING=1
# OPENAI_API_KEY=...  # optional if your gateway requires auth

# MinerU
MINERU_API_TOKEN=your_mineru_token

# Recommended paper search: DeepXiv direct API
PAPER_SEARCH_ENABLED=true
PAPER_SEARCH_PROVIDER=deepxiv
DEEPXIV_API_BASE_URL=https://data.rag.ac.cn
DEEPXIV_API_TOKEN=your_deepxiv_token
DEEPXIV_RETRIEVE_TOP_K=8
DEEPXIV_DEFAULT_SOURCE=arxiv
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

DeepReviewer 2.0 supports generic OpenAI-compatible providers and two paper-search adapters:
DeepXiv direct API (`PAPER_SEARCH_PROVIDER=deepxiv`, recommended) and local PASA
(`PAPER_SEARCH_PROVIDER=pasa`, advanced fallback).

For the full provider setup guide, see `docs/paper_search_providers.md`.

### LLM Settings

| Variable | Description | Default |
| :--- | :--- | :--- |
| `BASE_URL` | Preferred OpenAI-compatible base URL | - |
| `OPENAI_BASE_URL` / `LLM_BASE_URL` | Alias of base URL | - |
| `OPENAI_API_KEY` / `API_KEY` / `LLM_API_KEY` | API key if gateway requires auth | Optional |
| `AGENT_MODEL` | Review model name | `gpt-5.2` |
| `OPENAI_USE_RESPONSES_API` | Use Responses API when provider supports it | `false` |
| `OPENAI_AGENTS_DISABLE_TRACING` | Disable tracing noise in local gateways | Recommended `1` |
| `AGENT_RESUME_ATTEMPTS` | Resume attempts (hard cap) | `2` |

### Finalization Gates

| Variable | Default | Description |
| :--- | :--- | :--- |
| `ENABLE_FINAL_GATES` | `false` | Enable backend final-write gating |
| `MIN_PAPER_SEARCH_CALLS_FOR_PDF_ANNOTATE` | `3` | Minimum search calls before dense annotation |
| `MIN_PAPER_SEARCH_CALLS_FOR_FINAL` | `3` | Minimum search calls before finalization |
| `MIN_DISTINCT_PAPER_QUERIES_FOR_FINAL` | `3` | Minimum distinct paper queries |
| `MIN_ANNOTATIONS_FOR_FINAL` | `10` | Minimum annotation count |
| `MIN_ENGLISH_WORDS_FOR_FINAL` | `0` | Minimum English words (`0` disables) |
| `FORCE_ENGLISH_OUTPUT` | `true` | Force English final output |

### MinerU And Paper Search

| Variable Group | Notes |
| :--- | :--- |
| `MINERU_BASE_URL`, `MINERU_API_TOKEN`, `MINERU_MODEL_VERSION` | MinerU parser setup |
| `PAPER_SEARCH_PROVIDER` | `deepxiv` for direct hosted retrieval, or `pasa` for a local PASA service |
| `DEEPXIV_*` | Recommended DeepXiv direct retrieval settings |
| `PAPER_SEARCH_*` | PASA-compatible remote search settings used when `PAPER_SEARCH_PROVIDER=pasa` |
| `PAPER_READ_*` | Optional external paper-read service; unset uses arXiv metadata fallback |

DeepXiv direct mode is the default recommended path:

```bash
PAPER_SEARCH_ENABLED=true
PAPER_SEARCH_PROVIDER=deepxiv
DEEPXIV_API_BASE_URL=https://data.rag.ac.cn
DEEPXIV_API_TOKEN=your_deepxiv_token
DEEPXIV_RETRIEVE_TOP_K=8
DEEPXIV_DEFAULT_SOURCE=arxiv
```

PASA mode is still supported for users who want to run the local retrieval stack:

```bash
PAPER_SEARCH_ENABLED=true
PAPER_SEARCH_PROVIDER=pasa
PAPER_SEARCH_BASE_URL=http://127.0.0.1:8001
PAPER_SEARCH_ENDPOINT=/pasa/search
PAPER_SEARCH_HEALTH_ENDPOINT=/health
```

If `PAPER_SEARCH_ENABLED=false`, the selected provider is missing required settings
(for example, missing `DEEPXIV_API_TOKEN` in DeepXiv mode), or provider health
checks fail, `paper_search` returns `status=not_started` and the run proceeds in
retrieval-disabled mode instead of repeatedly retrying external search.

---

## CLI Usage

| Command | Purpose |
| :--- | :--- |
| `python main.py submit --pdf /path/to/paper.pdf` | Submit a new review job |
| `python main.py status --job-id <job_id>` | Get one-shot status snapshot |
| `python main.py watch --job-id <job_id> --interval 2 --timeout 1800` | Poll progress until timeout/completion |
| `python main.py result --job-id <job_id> --format all` | Fetch markdown + pdf outputs |

### Output artifacts

- `data/jobs/<job_id>/final_report.md`
- `data/jobs/<job_id>/final_report.pdf`
- `data/jobs/<job_id>/events.jsonl`

`final_report.pdf` includes:

- final markdown content
- token usage summary (input/output/total/requests)
- original paper appendix pages
- auto-rendered review overlays (when MinerU line bboxes are available)

---

## External Services

### MinerU (required in strict mode)

1. Register at [https://mineru.net/](https://mineru.net/)
2. Create API token in dashboard
3. Set `MINERU_API_TOKEN` in `.env`

### DeepXiv (recommended)

DeepXiv is the simplest production path because it does not require running PASA
models locally.

1. Register for a DeepXiv API token.
2. Set `PAPER_SEARCH_PROVIDER=deepxiv`.
3. Set `DEEPXIV_API_TOKEN` in `.env`.
4. Keep `DEEPXIV_API_BASE_URL=https://data.rag.ac.cn` unless your deployment uses a different endpoint.

DeepReviewer calls:

- `GET /stats/usage` for startup health checks.
- `GET /arxiv/?type=retrieve&query=...&top_k=...&source=...` for `paper_search`.

The `paper_search` tool output format remains the same as PASA-compatible mode,
so review prompts, gates, and reports do not need separate handling.

### PASA (advanced fallback)

- Local guide: `pasa/README.md`
- Chinese local guide: `pasa/README.zh-CN.md`
- Official repo: [https://github.com/bytedance/pasa](https://github.com/bytedance/pasa)
- Serper token (required by PASA Google workflow): [https://serper.dev/](https://serper.dev/)
- If PASA/external search is not configured, search is not started by default in that run, so automatic novelty comparison is unavailable.

Configure:

```bash
PAPER_SEARCH_ENABLED=true
PAPER_SEARCH_PROVIDER=pasa
PAPER_SEARCH_BASE_URL=http://127.0.0.1:8001
PAPER_SEARCH_ENDPOINT=/pasa/search
PAPER_SEARCH_HEALTH_ENDPOINT=/health
```

Expose compatible endpoint(s):

- `POST /pasa/search` (default)
- `POST /search` (optional compatibility path)

---

## Troubleshooting

- `RuntimeError: Agent finished without successful review_final_markdown_write`
  - Model ended before final-write gate completion.
  - Check `events.jsonl` for phase progression and tool usage.

- MinerU timeout/failure
  - Verify token validity and endpoint reachability.

- DeepXiv search is not started
  - Verify `PAPER_SEARCH_PROVIDER=deepxiv`.
  - Verify `DEEPXIV_API_TOKEN` is set.
  - Verify `DEEPXIV_API_BASE_URL` can serve `/stats/usage`.

- PASA timeout/failure
  - Verify service health and endpoint path (`/pasa/search` vs `/search`).

---

## Citation

If you use DeepReview in your research, please cite:

```bibtex
@inproceedings{zhu-etal-2025-deepreview,
    title = "{D}eep{R}eview: Improving {LLM}-based Paper Review with Human-like Deep Thinking Process",
    author = "Zhu, Minjun  and
      Weng, Yixuan  and
      Yang, Linyi  and
      Zhang, Yue",
    editor = "Che, Wanxiang  and
      Nabende, Joyce  and
      Shutova, Ekaterina  and
      Pilehvar, Mohammad Taher",
    booktitle = "Proceedings of the 63rd Annual Meeting of the Association for Computational Linguistics (Volume 1: Long Papers)",
    month = jul,
    year = "2025",
    address = "Vienna, Austria",
    publisher = "Association for Computational Linguistics",
    url = "https://aclanthology.org/2025.acl-long.1420/",
    doi = "10.18653/v1/2025.acl-long.1420",
    pages = "29330--29355",
    ISBN = "979-8-89176-251-0"
}
```

---

## Community & Support

**WeChat Discussion Groups**  
Scan the QR codes below to join.
<table>
  <tr>
    <td><img src="wechat/wechat1.jpg" width="220" alt="WeChat Group 1"/></td>
    <td><img src="wechat/wechat2.jpg" width="220" alt="WeChat Group 2"/></td>
  </tr>
</table>

---

## License

MIT License. See `LICENSE`.  
Third-party attributions: `THIRD_PARTY_NOTICES.md`.
