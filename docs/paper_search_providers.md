# Paper Search Providers

DeepReviewer keeps the agent-facing tool name as `paper_search`, but the backend
retrieval provider is configurable. This keeps prompts, gates, usage accounting,
and final reports stable while allowing different retrieval infrastructure.

## Recommended Path: DeepXiv

Use this path when you want the simplest setup. No PASA model service is required.

1. Register for a DeepXiv API token.
2. Copy `.env.example` to `.env`.
3. Configure:

```bash
PAPER_SEARCH_ENABLED=true
PAPER_SEARCH_PROVIDER=deepxiv

DEEPXIV_API_BASE_URL=https://data.rag.ac.cn
DEEPXIV_API_TOKEN=your_deepxiv_token
DEEPXIV_REQUEST_TIMEOUT_SECONDS=60
DEEPXIV_RETRIEVE_TOP_K=8
DEEPXIV_DEFAULT_SOURCE=arxiv
```

Startup behavior:

- DeepReviewer checks `GET {DEEPXIV_API_BASE_URL}/stats/usage`.
- If the token is missing, `paper_search` returns `status=not_started` with
  `availability=missing_api_token`.
- If the health request fails, `paper_search` returns `status=not_started` with
  `availability=health_check_failed`.

Search behavior:

- `paper_search(query=...)` calls:
  `GET {DEEPXIV_API_BASE_URL}/arxiv/?type=retrieve&query=...&top_k=...&source=...`
- `question_list` is supported; up to 3 distinct questions are queried and
  merged into one deduplicated result list.
- Output is normalized to the same shape used by PASA-compatible mode:
  `title`, `abstract`, `arxiv_id`, `url`, `abs_url`, `pdf_url`, `authors`,
  `categories`, `citation_count`, and `provider=deepxiv`.

## Alternative Path: PASA

Use this path when you specifically want to run PASA locally.

1. Install PASA extras:

```bash
pip install -e ".[pasa]"
```

2. Configure the PASA service:

```bash
cp pasa/.pasa_env.example pasa/.pasa_env
# Edit model paths, ports, GPU selection, Serper token, and proxy settings.
```

3. Start PASA:

```bash
cd pasa
bash start_pasa_server.sh --background
```

4. Configure DeepReviewer:

```bash
PAPER_SEARCH_ENABLED=true
PAPER_SEARCH_PROVIDER=pasa
PAPER_SEARCH_BASE_URL=http://127.0.0.1:8001
PAPER_SEARCH_API_KEY=
PAPER_SEARCH_ENDPOINT=/pasa/search
PAPER_SEARCH_TIMEOUT_SECONDS=120
PAPER_SEARCH_HEALTH_ENDPOINT=/health
PAPER_SEARCH_HEALTH_TIMEOUT_SECONDS=5
```

Startup behavior:

- DeepReviewer checks `GET {PAPER_SEARCH_BASE_URL}{PAPER_SEARCH_HEALTH_ENDPOINT}`.
- If health fails, `paper_search` returns `status=not_started` and the run
  proceeds in retrieval-disabled mode.

Search behavior:

- `paper_search(query=...)` sends:

```json
{
  "query": "...",
  "question_list": null
}
```

to:

```text
POST {PAPER_SEARCH_BASE_URL}{PAPER_SEARCH_ENDPOINT}
```

PASA can return either a dictionary payload or a direct list of paper rows. Direct
list responses are adapted into the same paper list shape used by DeepReviewer.

## Retrieval-Disabled Mode

If search is disabled or the selected provider is not ready, DeepReviewer does
not keep retrying external retrieval. Instead, the tool returns:

```json
{
  "status": "not_started",
  "reason": "paper_search_not_started",
  "next_action": "enter_retrieval_disabled_mode"
}
```

The review run continues with manuscript-grounded review. Novelty and related-work
claims should be treated as deferred manual verification in that run.
