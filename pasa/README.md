# PASA Local Deployment Guide (DeepReviewer-2.0 Adapted Edition)

This directory provides a PASA service layout that can be called directly by DeepReviewer:

- Two vLLM OpenAI-compatible inference services (crawler + selector)
- One Flask orchestrator (`pasa_server.py`)
- One unified start/stop script (`start_pasa_server.sh`)

DeepReviewer accesses PASA with:

- `PAPER_SEARCH_BASE_URL=http://127.0.0.1:8001`
- `PAPER_SEARCH_ENDPOINT=/pasa/search`

---

## Official References

- PASA official repository: <https://github.com/bytedance/pasa>
- Official README: <https://github.com/bytedance/pasa/blob/main/README.md>
- Crawler model: <https://huggingface.co/bytedance-research/pasa-7b-crawler>
- Selector model: <https://huggingface.co/bytedance-research/pasa-7b-selector>
- Dataset: <https://huggingface.co/datasets/CarlanLark/pasa-dataset>
- Serper API signup: <https://serper.dev/>

---

## 1. Requirements

- Linux + NVIDIA GPU
- Python 3.10+ (3.11 recommended)
- Working CUDA environment (compatible with your PyTorch / vLLM versions)
- Network access to Hugging Face, arXiv, and Serper

---

## 2. Install Dependencies

Run this in your Python environment:

```bash
cd <repo_root>/pasa
pip install --upgrade pip
pip install \
  torch transformers \
  vllm "openai>=1.52,<1.76" \
  flask flask-cors \
  requests httpx arxiv \
  beautifulsoup4 lxml
```

Notes:

- `start_pasa_server.sh` checks `import vllm` before startup.
- `pasa/pasa/utils.py` loads the local paper DB at import time, so configure these file paths first.

---

## 3. Download Models and Data

### 3.1 Download PASA Models

Example using `huggingface-cli`:

```bash
# crawler
huggingface-cli download bytedance-research/pasa-7b-crawler \
  --local-dir /data/models/pasa-7b-crawler

# selector
huggingface-cli download bytedance-research/pasa-7b-selector \
  --local-dir /data/models/pasa-7b-selector
```

Then point paths in `.pasa_env` to your local model directories.

### 3.2 Prepare PASA Retrieval Data (Local Paper DB)

The current code reads:

- `PASA_PAPER_DB` (for example: `cs_paper_2nd.zip`)
- `PASA_PAPER_ID` (for example: `id2paper.json`)

Download them from the official dataset page, store locally, then configure them in `.pasa_env`.

---

## 4. Configure PASA Environment File

Recommended setup:

```bash
cd <repo_root>/pasa
cp .pasa_env.example .pasa_env.local
vim .pasa_env.local
```

Notes:
- `pasa_server.py` loads env files in this order: `$PASA_ENV_FILE` -> `.pasa_env.local` -> `.pasa_env`
- Keep machine-specific settings in `.pasa_env.local` and do not commit them

Key configuration example:

```bash
# GPU
PASA_GPU_ID=1

# Flask server
PASA_SERVER_HOST=0.0.0.0
PASA_SERVER_PORT=8001

# Model paths (must exist)
PASA_CRAWLER_PATH=/data/models/pasa-7b-crawler
PASA_SELECTOR_PATH=/data/models/pasa-7b-selector
PASA_PROMPTS_PATH=pasa/agent_prompt.json

# vLLM service endpoints
PASA_VLLM_HOST=127.0.0.1
PASA_VLLM_CRAWLER_PORT=8101
PASA_VLLM_SELECTOR_PORT=8102
PASA_VLLM_CRAWLER_URL=http://127.0.0.1:8101/v1
PASA_VLLM_SELECTOR_URL=http://127.0.0.1:8102/v1
PASA_VLLM_CRAWLER_MODEL_NAME=pasa-crawler
PASA_VLLM_SELECTOR_MODEL_NAME=pasa-selector

# Serper key (now read from env var; no longer hardcoded)
PASA_SERPER_API_KEY=your_serper_api_key
PASA_SERPER_SEARCH_URL=https://google.serper.dev/search

# Local paper DB (set real paths)
PASA_PAPER_DB=/data/pasa/cs_paper_2nd.zip
PASA_PAPER_ID=/data/pasa/id2paper.json
```

---

## 5. Start and Stop

### Start in foreground (recommended for debugging)

```bash
cd <repo_root>/pasa
bash start_pasa_server.sh
```

### Start in background (recommended for long-running use)

```bash
cd <repo_root>/pasa
bash start_pasa_server.sh --background
```

### Stop all processes

```bash
cd <repo_root>/pasa
bash start_pasa_server.sh --stop
```

### Restart

```bash
cd <repo_root>/pasa
bash start_pasa_server.sh --restart
```

---

## 6. Verify Service

### Health check

```bash
curl http://127.0.0.1:8001/health
```

Expected fields:

- `"status": "healthy"`
- `"crawler_ready": true`
- `"selector_ready": true`

### Search API test

```bash
curl -X POST http://127.0.0.1:8001/pasa/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Papers about contrastive learning",
    "expand_layers": 1,
    "search_queries": 2,
    "search_papers": 5,
    "expand_papers": 5,
    "threads_num": 0
  }'
```

### Built-in test script

```bash
cd <repo_root>/pasa
python test_pasa_decoupling.py
```

---

## 7. API Endpoints

- `GET /`
- `GET /health`
- `POST /pasa/search`
- `POST /pasa/search_async`
- `GET /pasa/jobs/<job_id>`
- `GET /pasa/jobs/<job_id>/result`
- `DELETE /pasa/jobs/<job_id>`

---

## 8. Integrate with DeepReviewer

Set the following in `<repo_root>/.env`:

```bash
PAPER_SEARCH_BASE_URL=http://127.0.0.1:8001
PAPER_SEARCH_ENDPOINT=/pasa/search
PAPER_SEARCH_API_KEY=
```

---

## 9. FAQ

1. `vllm` import fails
- Ensure the Python environment used by the startup script has `vllm` installed.

2. Model path does not exist
- Check `PASA_CRAWLER_PATH` and `PASA_SELECTOR_PATH` in `.pasa_env`.

3. `/health` is unhealthy
- Ensure `PASA_VLLM_*_MODEL_NAME` matches the vLLM `--served-model-name`.
- Check logs: `/tmp/pasa_vllm_crawler.log`, `/tmp/pasa_vllm_selector.log`, `/tmp/pasa_server.log`.

4. `/pasa/search` errors or returns empty results
- Ensure `PASA_SERPER_API_KEY` is configured correctly.
- Ensure your network/proxy can access `google.serper.dev` and arXiv.

5. Importing `pasa/pasa/utils.py` fails on startup
- Usually caused by invalid `PASA_PAPER_DB` or `PASA_PAPER_ID` paths. Fix paths and restart.
