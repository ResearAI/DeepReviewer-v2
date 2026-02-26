"""
Legacy PASA tool (transformers-local loading)
=============================================
This file is kept for reference only. It loads models locally via transformers
and is NOT the recommended production path (use the vLLM stack instead).

Recommended:
  - start: `vllm_tools/start_pasa_server.sh`
  - client: `tools/pasa_tool.py`
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from mcp.server.fastmcp import FastMCP

# Ensure `pasa` modules are imported from `vllm_tools/`
_VLLM_TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_VLLM_TOOLS))

os.environ["http_proxy"] = os.environ.get("http_proxy", "http://127.0.0.1:58887")
os.environ["https_proxy"] = os.environ.get("https_proxy", "http://127.0.0.1:58887")

# Set CUDA device for legacy PASA (defaults to GPU1 for consistency)
pasa_gpu = os.environ.get("PASA_GPU_ID", "1")
os.environ["CUDA_VISIBLE_DEVICES"] = pasa_gpu
os.environ["PASA_CUDA_DEVICE"] = "cuda:0"

from pasa.models import Agent  # noqa: E402
from pasa.paper_agent import PaperAgent  # noqa: E402

mcp = FastMCP("pasa_search_legacy")
logger = logging.getLogger(__name__)

DEFAULT_CRAWLER_PATH = os.getenv("PASA_CRAWLER_PATH", "").strip()
DEFAULT_SELECTOR_PATH = os.getenv("PASA_SELECTOR_PATH", "").strip()
DEFAULT_PROMPTS_PATH = os.getenv("PASA_PROMPTS_PATH", str(_VLLM_TOOLS / "pasa" / "agent_prompt.json"))

logger.info("Pre-loading CRAWLER_AGENT models … (this may take a while)")
_CRAWLER_AGENT: Agent = Agent(DEFAULT_CRAWLER_PATH)  # type: ignore
logger.info("Pre-loading SELECTOR_AGENT models … (this may take a while)")
_SELECTOR_AGENT: Agent = Agent(DEFAULT_SELECTOR_PATH)  # type: ignore
logger.info("✅ PASA models loaded successfully (legacy).")


def _run_pasa_agent(
    query: str,
    expand_layers: int,
    search_queries: int,
    search_papers: int,
    expand_papers: int,
    threads_num: int,
) -> List[Dict[str, str]]:
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
    for t, i, a in zip(recall_titles, recall_ids, recall_abs):
        results.append({"title": t, "link": f"{i}" if i else "", "snippet": a})
    return results


@mcp.tool()
async def pasa_search(query: str, task_cache_dir: Optional[str] = None) -> List[Dict[str, str]]:
    expand_layers: int = 2
    search_queries: int = 5
    search_papers: int = 10
    expand_papers: int = 20
    threads_num: int = 0

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        _run_pasa_agent,
        query,
        expand_layers,
        search_queries,
        search_papers,
        expand_papers,
        threads_num,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
