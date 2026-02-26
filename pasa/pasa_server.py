"""
PASA Server (vLLM Edition) - Flask Orchestrator for PASA Paper Search
====================================================================
This Flask server exposes a REST API for the PASA paper search pipeline.

Key design:
  - crawler / selector models are served by **vLLM OpenAI servers**
  - this service only orchestrates the pipeline and calls vLLM via HTTP

Config:
  - reads `.pasa_env` next to this file (same format as KEY=VALUE)
  - important variables:
      PASA_INFERENCE_BACKEND=vllm
      PASA_GPU_ID=1
      PASA_CRAWLER_PATH, PASA_SELECTOR_PATH
      PASA_VLLM_CRAWLER_URL, PASA_VLLM_SELECTOR_URL (must include /v1)
      PASA_VLLM_CRAWLER_MODEL_NAME, PASA_VLLM_SELECTOR_MODEL_NAME
      PASA_PROMPTS_PATH (default: pasa/agent_prompt.json)

Endpoints:
  - GET  /health
  - POST /pasa/search
  - POST /pasa/search_async
  - GET  /pasa/jobs/<job_id>
  - GET  /pasa/jobs/<job_id>/result
  - DELETE /pasa/jobs/<job_id>
  - GET  /
"""

from __future__ import annotations

import atexit
import logging
import os
import sys
import threading
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from flask import Flask, jsonify, request
from flask_cors import CORS

# --------------------------------------------------------------------------- #
# Env loader (.pasa_env)
# --------------------------------------------------------------------------- #


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        print(f"⚠️  Warning: .pasa_env file not found at {env_path}", file=sys.stderr)
        return

    print(f"Loading PASA vLLM configuration from: {env_path}", file=sys.stderr)
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip()
    print("✅ PASA environment loaded successfully", file=sys.stderr)


def _load_env_candidates() -> None:
    root = Path(__file__).parent
    explicit = str(os.getenv('PASA_ENV_FILE') or '').strip()
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(root / '.pasa_env.local')
    candidates.append(root / '.pasa_env')

    for candidate in candidates:
        if candidate.exists():
            _load_env_file(candidate)
            return
    print('⚠️  No PASA env file found; using process environment only.', file=sys.stderr)


_load_env_candidates()

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("pasa_server")

# --------------------------------------------------------------------------- #
# Imports (after env loaded)
# --------------------------------------------------------------------------- #

try:
    from pasa.paper_agent import PaperAgent
    from pasa.vllm_agent import VLLMAgent
except Exception as e:
    print(f"❌ Failed to import PASA modules: {e}", file=sys.stderr)
    print("Please run from the `pasa/` directory or ensure `pasa` is on PYTHONPATH.", file=sys.stderr)
    raise

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

PASA_SERVER_HOST = os.getenv("PASA_SERVER_HOST", "0.0.0.0")
PASA_SERVER_PORT = int(os.getenv("PASA_SERVER_PORT", "8001"))

PASA_INFERENCE_BACKEND = os.getenv("PASA_INFERENCE_BACKEND", "vllm").strip().lower()

DEFAULT_CRAWLER_PATH = os.getenv("PASA_CRAWLER_PATH", "").strip()
DEFAULT_SELECTOR_PATH = os.getenv("PASA_SELECTOR_PATH", "").strip()

DEFAULT_PROMPTS_PATH = os.getenv("PASA_PROMPTS_PATH")
if not DEFAULT_PROMPTS_PATH:
    DEFAULT_PROMPTS_PATH = str(Path(__file__).parent / "pasa" / "agent_prompt.json")

PASA_VLLM_CRAWLER_URL = os.getenv("PASA_VLLM_CRAWLER_URL", "http://127.0.0.1:8101/v1").rstrip("/")
PASA_VLLM_SELECTOR_URL = os.getenv("PASA_VLLM_SELECTOR_URL", "http://127.0.0.1:8102/v1").rstrip("/")
PASA_VLLM_CRAWLER_MODEL_NAME = os.getenv("PASA_VLLM_CRAWLER_MODEL_NAME", "pasa-crawler")
PASA_VLLM_SELECTOR_MODEL_NAME = os.getenv("PASA_VLLM_SELECTOR_MODEL_NAME", "pasa-selector")
PASA_VLLM_LOGPROBS = int(os.getenv("PASA_VLLM_LOGPROBS", "20"))
PASA_VLLM_REQUEST_TIMEOUT = float(os.getenv("PASA_VLLM_REQUEST_TIMEOUT", "300"))

PASA_ASYNC_MAX_WORKERS = max(1, int(os.getenv("PASA_ASYNC_MAX_WORKERS", "2")))
PASA_ASYNC_MAX_QUEUE = int(os.getenv("PASA_ASYNC_MAX_QUEUE", "64"))
PASA_ASYNC_JOB_TTL_SECONDS = int(os.getenv("PASA_ASYNC_JOB_TTL_SECONDS", "3600"))

# --------------------------------------------------------------------------- #
# Flask app
# --------------------------------------------------------------------------- #

app = Flask(__name__)
CORS(app)

# --------------------------------------------------------------------------- #
# Global agent cache (vLLM-backed)
# --------------------------------------------------------------------------- #

_CRAWLER_AGENT: Optional[VLLMAgent] = None
_SELECTOR_AGENT: Optional[VLLMAgent] = None
_MODEL_LOAD_ERROR: Optional[str] = None

_ASYNC_EXECUTOR = ThreadPoolExecutor(max_workers=PASA_ASYNC_MAX_WORKERS)
_JOB_LOCK = threading.Lock()
_JOBS: Dict[str, Dict[str, Any]] = {}
_JOB_FUTURES: Dict[str, Future] = {}


def load_models() -> None:
    """Initialize vLLM-backed agents and validate endpoints."""
    global _CRAWLER_AGENT, _SELECTOR_AGENT, _MODEL_LOAD_ERROR

    try:
        if PASA_INFERENCE_BACKEND != "vllm":
            raise RuntimeError(
                f"Unsupported PASA_INFERENCE_BACKEND={PASA_INFERENCE_BACKEND!r}. "
                "This server expects vLLM backend."
            )

        if not DEFAULT_CRAWLER_PATH:
            raise RuntimeError('PASA_CRAWLER_PATH is required.')
        if not DEFAULT_SELECTOR_PATH:
            raise RuntimeError('PASA_SELECTOR_PATH is required.')
        if not os.path.exists(DEFAULT_CRAWLER_PATH):
            raise FileNotFoundError(f"CRAWLER model path not found: {DEFAULT_CRAWLER_PATH}")
        if not os.path.exists(DEFAULT_SELECTOR_PATH):
            raise FileNotFoundError(f"SELECTOR model path not found: {DEFAULT_SELECTOR_PATH}")
        if not os.path.exists(DEFAULT_PROMPTS_PATH):
            raise FileNotFoundError(f"PROMPTS path not found: {DEFAULT_PROMPTS_PATH}")

        logger.info("=" * 70)
        logger.info("🚀 Starting PASA vLLM backend initialization")
        logger.info(f"GPU (expected): {os.getenv('PASA_GPU_ID', '1')}")
        logger.info(f"CRAWLER vLLM:  {PASA_VLLM_CRAWLER_URL} (model={PASA_VLLM_CRAWLER_MODEL_NAME})")
        logger.info(f"SELECTOR vLLM: {PASA_VLLM_SELECTOR_URL} (model={PASA_VLLM_SELECTOR_MODEL_NAME})")

        _CRAWLER_AGENT = VLLMAgent(
            base_url=PASA_VLLM_CRAWLER_URL,
            model_name=PASA_VLLM_CRAWLER_MODEL_NAME,
            tokenizer_path=DEFAULT_CRAWLER_PATH,
            use_chat_template=True,
            request_timeout=PASA_VLLM_REQUEST_TIMEOUT,
            logprobs=PASA_VLLM_LOGPROBS,
        )
        _SELECTOR_AGENT = VLLMAgent(
            base_url=PASA_VLLM_SELECTOR_URL,
            model_name=PASA_VLLM_SELECTOR_MODEL_NAME,
            tokenizer_path=None,
            use_chat_template=False,
            request_timeout=PASA_VLLM_REQUEST_TIMEOUT,
            logprobs=PASA_VLLM_LOGPROBS,
        )

        _CRAWLER_AGENT.ensure_ready()
        _SELECTOR_AGENT.ensure_ready()

        logger.info("✅ PASA vLLM backend is ready")
        logger.info("=" * 70)

    except Exception as e:
        _MODEL_LOAD_ERROR = str(e)
        logger.error(f"❌ Failed to initialize PASA vLLM backend: {e}")
        logger.error(traceback.format_exc())
        raise


def _shutdown_resources() -> None:
    for agent in (_CRAWLER_AGENT, _SELECTOR_AGENT):
        if agent is None:
            continue
        try:
            agent.close()
        except Exception:
            pass
    _ASYNC_EXECUTOR.shutdown(wait=False)


atexit.register(_shutdown_resources)


def run_pasa_search(
    query: str,
    expand_layers: int = 2,
    search_queries: int = 5,
    search_papers: int = 10,
    expand_papers: int = 20,
    threads_num: int = 0,
) -> List[Dict[str, str]]:
    """Execute PASA paper search pipeline synchronously."""
    global _CRAWLER_AGENT, _SELECTOR_AGENT

    if _CRAWLER_AGENT is None or _SELECTOR_AGENT is None:
        raise RuntimeError("PASA backend not ready. Check server startup logs.")

    logger.info(f"🔍 Starting PASA search for query: {query[:100]}...")
    logger.info(
        "📊 Parameters: expand_layers=%s, search_queries=%s, search_papers=%s, expand_papers=%s, threads_num=%s",
        expand_layers,
        search_queries,
        search_papers,
        expand_papers,
        threads_num,
    )

    end_date = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")

    paper_agent = PaperAgent(
        user_query=query,
        crawler=_CRAWLER_AGENT,
        selector=_SELECTOR_AGENT,
        end_date=end_date,
        expand_layers=expand_layers,
        search_queries=search_queries,
        search_papers=search_papers,
        expand_papers=expand_papers,
        threads_num=threads_num,
        prompts_path=DEFAULT_PROMPTS_PATH,
    )

    paper_agent.run()

    recall_titles = paper_agent.root.extra.get("recall_papers", [])
    recall_ids = paper_agent.root.extra.get("recall_arxiv_ids", [])
    recall_abs = paper_agent.root.extra.get("recall_abstracts", [])

    results: List[Dict[str, str]] = []
    for title, arxiv_id, abstract in zip(recall_titles, recall_ids, recall_abs):
        results.append({"title": title, "link": f"{arxiv_id}" if arxiv_id else "", "snippet": abstract})

    logger.info("✅ PASA search completed: %s papers found", len(results))
    return results


def _format_dt(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _serialize_job(job: Dict[str, Any], include_result: bool = True) -> Dict[str, Any]:
    payload = {
        "id": job["id"],
        "status": job["status"],
        "query": job.get("query"),
        "created_at": _format_dt(job.get("created_at")),
        "updated_at": _format_dt(job.get("updated_at")),
        "started_at": _format_dt(job.get("started_at")),
        "completed_at": _format_dt(job.get("completed_at")),
    }
    if job.get("status") == "failed":
        payload["error"] = job.get("error")
    if include_result and job.get("status") == "succeeded":
        payload["result"] = job.get("result", [])
    if job.get("started_at") and job.get("completed_at"):
        payload["duration_seconds"] = (job["completed_at"] - job["started_at"]).total_seconds()
    else:
        payload["duration_seconds"] = None
    return payload


def _cleanup_jobs() -> None:
    if PASA_ASYNC_JOB_TTL_SECONDS <= 0:
        return
    cutoff = datetime.utcnow() - timedelta(seconds=PASA_ASYNC_JOB_TTL_SECONDS)
    with _JOB_LOCK:
        expired = [
            job_id
            for job_id, job in _JOBS.items()
            if job.get("updated_at")
            and job["status"] in ("succeeded", "failed", "cancelled")
            and job["updated_at"] < cutoff
        ]
        for job_id in expired:
            _JOBS.pop(job_id, None)
            _JOB_FUTURES.pop(job_id, None)


def _run_async_job(job_id: str, params: Dict[str, Any]) -> None:
    started_at = datetime.utcnow()
    with _JOB_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["started_at"] = started_at
        job["updated_at"] = started_at
    try:
        results = run_pasa_search(**params)
    except Exception as e:
        logger.error("Async job %s failed: %s", job_id, e)
        logger.error(traceback.format_exc())
        finished_at = datetime.utcnow()
        with _JOB_LOCK:
            job = _JOBS.get(job_id)
            if job:
                job["status"] = "failed"
                job["error"] = str(e)
                job["updated_at"] = finished_at
                job["completed_at"] = finished_at
        return

    finished_at = datetime.utcnow()
    with _JOB_LOCK:
        job = _JOBS.get(job_id)
        if job:
            job["status"] = "succeeded"
            job["result"] = results
            job["updated_at"] = finished_at
            job["completed_at"] = finished_at


def _submit_job(params: Dict[str, Any]) -> str:
    _cleanup_jobs()
    with _JOB_LOCK:
        pending = sum(1 for job in _JOBS.values() if job["status"] in ("queued", "running"))
        if pending >= PASA_ASYNC_MAX_QUEUE:
            raise RuntimeError("Async queue is full")
        job_id = uuid4().hex
        now = datetime.utcnow()
        _JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "query": params.get("query"),
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "completed_at": None,
            "result": None,
            "error": None,
        }

    future = _ASYNC_EXECUTOR.submit(_run_async_job, job_id, params)
    with _JOB_LOCK:
        _JOB_FUTURES[job_id] = future
    return job_id


def _parse_int(data: Dict[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid parameter: {key}")


def _parse_search_params(data: Dict[str, Any]) -> Dict[str, Any]:
    query = data.get("query")
    if not query:
        raise ValueError("Missing required parameter: query")

    return {
        "query": query,
        "expand_layers": _parse_int(data, "expand_layers", 2),
        "search_queries": _parse_int(data, "search_queries", 5),
        "search_papers": _parse_int(data, "search_papers", 10),
        "expand_papers": _parse_int(data, "expand_papers", 20),
        "threads_num": _parse_int(data, "threads_num", 0),
    }


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@app.route("/", methods=["GET"])
def index():
    return jsonify(
        {
            # Keep the same response shape as the legacy server for compatibility.
            "service": "PASA Server",
            "version": "1.0.0",
            "status": "running",
            "description": "Independent Flask service for PASA paper search",
            "endpoints": {
                "POST /pasa/search": "Execute PASA paper search",
                "POST /pasa/search_async": "Execute PASA paper search asynchronously",
                "GET /pasa/jobs/<job_id>": "Get async job status",
                "GET /pasa/jobs/<job_id>/result": "Get async job result",
                "DELETE /pasa/jobs/<job_id>": "Cancel async job (if not started)",
                "GET /health": "Health check and vLLM readiness",
                "GET /": "This page",
            },
        }
    )


@app.route("/health", methods=["GET"])
def health():
    global _CRAWLER_AGENT, _SELECTOR_AGENT, _MODEL_LOAD_ERROR

    crawler_ready = False
    selector_ready = False
    try:
        crawler_ready = bool(_CRAWLER_AGENT and _CRAWLER_AGENT.is_ready())
        selector_ready = bool(_SELECTOR_AGENT and _SELECTOR_AGENT.is_ready())
    except Exception as e:
        _MODEL_LOAD_ERROR = str(e)

    models_ready = crawler_ready and selector_ready

    # Keep legacy keys (crawler_loaded/selector_loaded/gpu) so existing clients
    # don't break, but also expose vLLM-specific details.
    with _JOB_LOCK:
        queued_jobs = sum(1 for job in _JOBS.values() if job["status"] == "queued")
        running_jobs = sum(1 for job in _JOBS.values() if job["status"] == "running")
        total_jobs = len(_JOBS)

    payload = {
        "status": "healthy" if models_ready else "unhealthy",
        "models_loaded": models_ready,
        "crawler_loaded": crawler_ready,
        "selector_loaded": selector_ready,
        "error": _MODEL_LOAD_ERROR,
        "gpu": os.getenv("PASA_GPU_ID", "1"),
        "crawler_path": DEFAULT_CRAWLER_PATH,
        "selector_path": DEFAULT_SELECTOR_PATH,
        "prompts_path": DEFAULT_PROMPTS_PATH,
        # Extended fields (non-breaking additions)
        "backend": PASA_INFERENCE_BACKEND,
        "crawler_ready": crawler_ready,
        "selector_ready": selector_ready,
        "vllm": {
            "crawler_url": PASA_VLLM_CRAWLER_URL,
            "selector_url": PASA_VLLM_SELECTOR_URL,
            "crawler_model_name": PASA_VLLM_CRAWLER_MODEL_NAME,
            "selector_model_name": PASA_VLLM_SELECTOR_MODEL_NAME,
        },
        "async": {
            "max_workers": PASA_ASYNC_MAX_WORKERS,
            "max_queue": PASA_ASYNC_MAX_QUEUE,
            "job_ttl_seconds": PASA_ASYNC_JOB_TTL_SECONDS,
            "queued": queued_jobs,
            "running": running_jobs,
            "total": total_jobs,
        },
    }

    return jsonify(payload), (200 if models_ready else 503)


@app.route("/pasa/search", methods=["POST"])
def pasa_search_endpoint():
    global _CRAWLER_AGENT, _SELECTOR_AGENT, _MODEL_LOAD_ERROR

    if _CRAWLER_AGENT is None or _SELECTOR_AGENT is None:
        return (
            jsonify(
                {
                    "error": "Backend not ready",
                    "message": _MODEL_LOAD_ERROR or "PASA vLLM backend is not initialized",
                }
            ),
            503,
        )

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON request"}), 400

    try:
        params = _parse_search_params(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    async_mode = bool(data.get("async") or data.get("async_mode") or data.get("background"))
    if async_mode:
        try:
            job_id = _submit_job(params)
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 429
        return (
            jsonify(
                {
                    "job_id": job_id,
                    "status": "queued",
                    "status_url": f"/pasa/jobs/{job_id}",
                    "result_url": f"/pasa/jobs/{job_id}/result",
                }
            ),
            202,
        )

    try:
        results = run_pasa_search(**params)
        return jsonify(results), 200
    except Exception as e:
        logger.error("Error in /pasa/search endpoint: %s", e)
        logger.error(traceback.format_exc())
        return jsonify({"error": "Internal server error", "message": str(e)}), 500


@app.route("/pasa/search_async", methods=["POST"])
def pasa_search_async_endpoint():
    global _CRAWLER_AGENT, _SELECTOR_AGENT, _MODEL_LOAD_ERROR

    if _CRAWLER_AGENT is None or _SELECTOR_AGENT is None:
        return (
            jsonify(
                {
                    "error": "Backend not ready",
                    "message": _MODEL_LOAD_ERROR or "PASA vLLM backend is not initialized",
                }
            ),
            503,
        )

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON request"}), 400

    try:
        params = _parse_search_params(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        job_id = _submit_job(params)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 429

    return (
        jsonify(
            {
                "job_id": job_id,
                "status": "queued",
                "status_url": f"/pasa/jobs/{job_id}",
                "result_url": f"/pasa/jobs/{job_id}/result",
            }
        ),
        202,
    )


@app.route("/pasa/jobs/<job_id>", methods=["GET"])
def pasa_job_status(job_id: str):
    _cleanup_jobs()
    include_result = request.args.get("include_result", "1").lower() in ("1", "true", "yes")
    with _JOB_LOCK:
        job = _JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(_serialize_job(job, include_result=include_result)), 200


@app.route("/pasa/jobs/<job_id>/result", methods=["GET"])
def pasa_job_result(job_id: str):
    _cleanup_jobs()
    with _JOB_LOCK:
        job = _JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    status = job.get("status")
    if status == "succeeded":
        return jsonify(job.get("result", [])), 200
    if status == "failed":
        return jsonify({"error": job.get("error") or "Job failed"}), 500
    if status == "cancelled":
        return jsonify({"error": "Job was cancelled"}), 409
    return jsonify({"status": status}), 202


@app.route("/pasa/jobs/<job_id>", methods=["DELETE"])
def pasa_job_cancel(job_id: str):
    _cleanup_jobs()
    with _JOB_LOCK:
        job = _JOBS.get(job_id)
        future = _JOB_FUTURES.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    if job["status"] in ("succeeded", "failed", "cancelled"):
        return jsonify(_serialize_job(job, include_result=True)), 200

    if future and future.cancel():
        now = datetime.utcnow()
        with _JOB_LOCK:
            job = _JOBS.get(job_id)
            if job:
                job["status"] = "cancelled"
                job["updated_at"] = now
                job["completed_at"] = now
        return jsonify(_serialize_job(job, include_result=True)), 200

    return jsonify({"error": "Job is already running and cannot be cancelled"}), 409


if __name__ == "__main__":
    try:
        load_models()
        logger.info("=" * 70)
        logger.info("🚀 Starting PASA Flask Server (vLLM)")
        logger.info("📡 Server: http://%s:%s", PASA_SERVER_HOST, PASA_SERVER_PORT)
        logger.info("=" * 70)
        app.run(host=PASA_SERVER_HOST, port=PASA_SERVER_PORT, debug=False, threaded=True)
    except Exception:
        sys.exit(1)
