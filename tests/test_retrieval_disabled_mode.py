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


def _json_response(status_code: int, payload: object, url: str = 'http://search.local') -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request('GET', url),
    )


def _usage_stub():
    return SimpleNamespace(requests=0, input_tokens=0, output_tokens=0, total_tokens=0)


def _tool_map(runtime: ReviewRuntimeContext) -> dict[str, object]:
    return {tool.name: tool for tool in build_review_tools(runtime)}


def _invoke_tool(tool, runtime: ReviewRuntimeContext, payload: dict) -> dict:
    ctx = SimpleNamespace(context=runtime, usage=_usage_stub(), tool_name=tool.name)
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


def test_deepxiv_adapter_searches_directly_with_token(monkeypatch, tmp_path):
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'data'))
    get_settings.cache_clear()

    calls: list[dict] = []

    class DeepXivAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, **kwargs):
            calls.append({'url': url, **kwargs})
            if str(url).endswith('/stats/usage'):
                return _json_response(200, {'daily_limit': 1000, 'usage_history': []}, url)
            return _json_response(
                200,
                {
                    'total_count': 1,
                    'result': [
                        {
                            'arxiv_id': '1706.03762',
                            'title': 'Attention Is All You Need',
                            'abstract': 'Transformer abstract',
                            'url': 'https://arxiv.org/pdf/1706.03762.pdf',
                            'date': '2017-06-12T17:57:34Z',
                            'citation_count': 100,
                            'categories': ['cs.CL'],
                            'authors': [{'name': 'Ashish Vaswani', 'orgs': ['Google']}],
                            'score': 0.99,
                        }
                    ],
                },
                url,
            )

    monkeypatch.setattr(paper_search_module.httpx, 'AsyncClient', DeepXivAsyncClient)

    adapter = PaperSearchAdapter(
        search_cfg=PaperSearchConfig(
            enabled=True,
            base_url=None,
            api_key=None,
            endpoint='/pasa/search',
            timeout_seconds=120,
            health_endpoint='/health',
            health_timeout_seconds=1,
            provider='deepxiv',
            deepxiv_base_url='https://data.rag.ac.cn',
            deepxiv_api_token='deepxiv-token',
            deepxiv_top_k=8,
        ),
        read_cfg=PaperReadConfig(base_url=None, api_key=None, endpoint='/read', timeout_seconds=180),
    )

    state = _run(adapter.get_search_runtime_state()).to_dict()
    result = _run(adapter.search(query='transformer architecture'))

    assert state['availability'] == 'ready'
    assert state['provider'] == 'deepxiv'
    assert result['success'] is True
    assert result['provider'] == 'deepxiv'
    assert result['papers'][0]['provider'] == 'deepxiv'
    assert result['papers'][0]['arxiv_id'] == '1706.03762'
    retrieve_call = calls[-1]
    assert retrieve_call['headers']['Authorization'] == 'Bearer deepxiv-token'
    assert retrieve_call['params']['type'] == 'retrieve'
    assert retrieve_call['params']['top_k'] == 8
    assert retrieve_call['params']['source'] == 'arxiv'

    get_settings.cache_clear()


def test_deepxiv_adapter_requires_token(monkeypatch, tmp_path):
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'data'))
    get_settings.cache_clear()

    adapter = PaperSearchAdapter(
        search_cfg=PaperSearchConfig(
            enabled=True,
            base_url=None,
            api_key=None,
            endpoint='/pasa/search',
            timeout_seconds=120,
            health_endpoint='/health',
            health_timeout_seconds=1,
            provider='deepxiv',
            deepxiv_base_url='https://data.rag.ac.cn',
            deepxiv_api_token='',
        ),
        read_cfg=PaperReadConfig(base_url=None, api_key=None, endpoint='/read', timeout_seconds=180),
    )

    state = _run(adapter.get_search_runtime_state()).to_dict()
    result = _run(adapter.search(query='novelty retrieval'))

    assert state['availability'] == 'missing_api_token'
    assert state['started'] is False
    assert state['provider'] == 'deepxiv'
    assert result['status'] == 'not_started'
    assert result['paper_search_state']['availability'] == 'missing_api_token'

    get_settings.cache_clear()


def test_pasa_provider_keeps_remote_post_contract(monkeypatch, tmp_path):
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'data'))
    get_settings.cache_clear()

    calls: list[dict] = []

    class PasaAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, **kwargs):
            return _json_response(200, {'status': 'healthy'}, url)

        async def post(self, url, **kwargs):
            calls.append({'url': url, **kwargs})
            return _json_response(
                200,
                [{'title': 'PASA Paper', 'link': '2401.00001', 'snippet': 'abstract'}],
                url,
            )

    monkeypatch.setattr(paper_search_module.httpx, 'AsyncClient', PasaAsyncClient)

    adapter = PaperSearchAdapter(
        search_cfg=PaperSearchConfig(
            enabled=True,
            base_url='http://127.0.0.1:8001',
            api_key=None,
            endpoint='/pasa/search',
            timeout_seconds=120,
            health_endpoint='/health',
            health_timeout_seconds=1,
            provider='pasa',
        ),
        read_cfg=PaperReadConfig(base_url=None, api_key=None, endpoint='/read', timeout_seconds=180),
    )

    result = _run(adapter.search(query='legacy retrieval'))

    assert result['success'] is True
    assert result['provider'] == 'remote_list_adapted'
    assert result['papers'][0]['arxiv_id'] == '2401.00001'
    assert calls[0]['url'] == 'http://127.0.0.1:8001/pasa/search'
    assert calls[0]['json'] == {'query': 'legacy retrieval', 'question_list': None}

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
