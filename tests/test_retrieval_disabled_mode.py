from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import deepreview.adapters.paper_search as paper_search_module
from deepreview.adapters.paper_search import PaperReadConfig, PaperSearchAdapter, PaperSearchConfig
from deepreview.config import Settings, get_settings
from deepreview.state import save_job_state
from deepreview.storage import job_dir
from deepreview.tools.review_tools import ReviewRuntimeContext, build_review_tools
from deepreview.types import JobState


def _run(coro):
    return asyncio.run(coro)


def _usage_stub():
    return SimpleNamespace(requests=0, input_tokens=0, output_tokens=0, total_tokens=0)


def _tool_map(runtime: ReviewRuntimeContext) -> dict[str, object]:
    return {tool.name: tool for tool in build_review_tools(runtime)}


def _invoke_tool(tool, runtime: ReviewRuntimeContext, payload: dict) -> dict:
    ctx = SimpleNamespace(context=runtime, usage=_usage_stub())
    return _run(tool.on_invoke_tool(ctx, json.dumps(payload)))


def _build_runtime(
    *,
    search_enabled: bool,
    search_base_url: str | None,
    search_runtime_state: dict,
    enable_final_gates: bool = True,
    min_annotations_for_final: int = 1,
) -> ReviewRuntimeContext:
    job_id = uuid4()
    save_job_state(JobState(id=job_id, title='debug', source_pdf_name='paper.pdf'))
    adapter = PaperSearchAdapter(
        search_cfg=PaperSearchConfig(
            enabled=search_enabled,
            base_url=search_base_url,
            api_key=None,
            endpoint='/pasa/search',
            timeout_seconds=120,
            health_endpoint='/health',
            health_timeout_seconds=1,
        ),
        read_cfg=PaperReadConfig(
            base_url=None,
            api_key=None,
            endpoint='/read',
            timeout_seconds=180,
        ),
    )
    settings = Settings(
        enable_final_gates=enable_final_gates,
        min_annotations_for_final=min_annotations_for_final,
    )
    runtime = ReviewRuntimeContext(
        job_id=str(job_id),
        job_dir=job_dir(job_id),
        page_index={1: ['hello world']},
        source_markdown='hello world',
        paper_adapter=adapter,
        paper_search_runtime_state=search_runtime_state,
        settings=settings,
    )
    Path(runtime.job_dir).mkdir(parents=True, exist_ok=True)
    return runtime


def _full_report_markdown() -> str:
    return """
## Summary
Short summary.

## Strengths
- One clear strength.

## Weaknesses
- One clear weakness.

## Key Issues
- One key issue.

## Actionable Suggestions
- One actionable suggestion.

## Storyline Options + Writing Outlines
- One storyline option.

## Priority Revision Plan
- One revision plan.

## Experiment Inventory & Research Experiment Plan
- One experiment plan.

## Scores
Final Score: 5/10
Post-Revision Target: [6, 7]/10
""".strip()


def test_adapter_returns_not_started_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'data'))
    get_settings.cache_clear()

    adapter = PaperSearchAdapter(
        search_cfg=PaperSearchConfig(
            enabled=False,
            base_url='http://127.0.0.1:8001',
            api_key=None,
            endpoint='/pasa/search',
            timeout_seconds=120,
            health_endpoint='/health',
            health_timeout_seconds=1,
        ),
        read_cfg=PaperReadConfig(base_url=None, api_key=None, endpoint='/read', timeout_seconds=180),
    )

    state = _run(adapter.get_search_runtime_state()).to_dict()
    result = _run(adapter.search(query='test retrieval'))

    assert state['availability'] == 'disabled_by_config'
    assert result['status'] == 'not_started'
    assert result['reason'] == 'paper_search_not_started'
    assert result['retry_required'] is False

    get_settings.cache_clear()


def test_adapter_returns_not_started_when_health_check_fails(monkeypatch, tmp_path):
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'data'))
    get_settings.cache_clear()

    class FailingAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *args, **kwargs):
            raise httpx.ConnectError('boom')

    monkeypatch.setattr(paper_search_module.httpx, 'AsyncClient', FailingAsyncClient)

    adapter = PaperSearchAdapter(
        search_cfg=PaperSearchConfig(
            enabled=True,
            base_url='http://search.local',
            api_key=None,
            endpoint='/pasa/search',
            timeout_seconds=120,
            health_endpoint='/health',
            health_timeout_seconds=1,
        ),
        read_cfg=PaperReadConfig(base_url=None, api_key=None, endpoint='/read', timeout_seconds=180),
    )

    state = _run(adapter.get_search_runtime_state()).to_dict()
    result = _run(adapter.search(query='test retrieval'))

    assert state['availability'] == 'health_check_failed'
    assert state['started'] is False
    assert 'boom' in str(state['error'])
    assert result['status'] == 'not_started'
    assert result['paper_search_state']['availability'] == 'health_check_failed'

    get_settings.cache_clear()


def test_paper_search_tool_does_not_increment_usage_when_search_not_started(monkeypatch, tmp_path):
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'data'))
    get_settings.cache_clear()

    runtime = _build_runtime(
        search_enabled=False,
        search_base_url='http://127.0.0.1:8001',
        search_runtime_state={
            'enabled': False,
            'started': False,
            'availability': 'disabled_by_config',
        },
    )
    tools = _tool_map(runtime)

    result = _invoke_tool(tools['paper_search'], runtime, {'query': 'novelty check'})

    assert result['status'] == 'not_started'
    assert result['reason'] == 'paper_search_not_started'
    assert result['can_start_pdf_annotate'] is True
    assert result['paper_search_usage']['total_calls'] == 0
    assert runtime.paper_search_usage.total_calls == 0
    assert result['next_action'] == 'enter_retrieval_disabled_mode'

    get_settings.cache_clear()


def test_retrieval_disabled_mode_bypasses_search_gates_and_persists_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'data'))
    get_settings.cache_clear()

    runtime = _build_runtime(
        search_enabled=False,
        search_base_url='http://127.0.0.1:8001',
        search_runtime_state={
            'enabled': False,
            'started': False,
            'availability': 'disabled_by_config',
        },
        enable_final_gates=True,
        min_annotations_for_final=1,
    )
    tools = _tool_map(runtime)

    annotate_result = _invoke_tool(
        tools['pdf_annotate'],
        runtime,
        {
            'page': 1,
            'start_line': 1,
            'end_line': 1,
            'comment': 'Needs clarification.',
        },
    )
    assert annotate_result['status'] == 'ok'

    final_result = _invoke_tool(
        tools['review_final_markdown_write'],
        runtime,
        {'markdown': _full_report_markdown()},
    )

    assert final_result['status'] == 'ok'
    assert final_result['final_report_persisted'] is True
    assert final_result['required_paper_search_calls'] == 0
    assert final_result['paper_search_state']['availability'] == 'disabled_by_config'
    assert 'novelty/comparison conclusions are deferred to manual verification' in (
        runtime.final_markdown_text or ''
    )
    assert 'no external references are listed' in (runtime.final_markdown_text or '')

    get_settings.cache_clear()
