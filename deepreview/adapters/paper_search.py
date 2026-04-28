from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote_plus

import httpx


@dataclass
class PaperSearchConfig:
    enabled: bool
    base_url: str | None
    api_key: str | None
    endpoint: str
    timeout_seconds: int
    health_endpoint: str
    health_timeout_seconds: int
    provider: str = 'pasa'
    deepxiv_base_url: str | None = None
    deepxiv_api_token: str | None = None
    deepxiv_timeout_seconds: int = 60
    deepxiv_top_k: int = 8
    deepxiv_default_source: str = 'arxiv'


@dataclass
class PaperReadConfig:
    base_url: str | None
    api_key: str | None
    endpoint: str
    timeout_seconds: int


@dataclass
class PaperSearchRuntimeState:
    enabled: bool
    started: bool
    availability: str
    base_url: str | None = None
    health_url: str | None = None
    error: str | None = None
    provider: str | None = None

    def to_dict(self) -> dict:
        return {
            'enabled': bool(self.enabled),
            'started': bool(self.started),
            'availability': str(self.availability or '').strip(),
            'base_url': str(self.base_url or '').strip() or None,
            'health_url': str(self.health_url or '').strip() or None,
            'error': str(self.error or '').strip() or None,
            'provider': str(self.provider or '').strip() or None,
        }


class PaperSearchAdapter:
    def __init__(self, search_cfg: PaperSearchConfig, read_cfg: PaperReadConfig):
        self.search_cfg = search_cfg
        self.read_cfg = read_cfg
        self._search_state_cache: PaperSearchRuntimeState | None = None

    @property
    def search_configured(self) -> bool:
        return bool(self.search_cfg.enabled and self._configured_search_base_url())

    @property
    def read_configured(self) -> bool:
        return bool(self.read_cfg.base_url)

    async def search(
        self,
        *,
        query: str | None = None,
        question_list: list[str] | None = None,
    ) -> dict:
        state = await self.get_search_runtime_state()
        if not state.started:
            return self._search_not_started_payload(
                state=state,
                query=query,
                question_list=question_list,
            )
        try:
            if self._search_provider() == 'deepxiv':
                return await self._search_deepxiv(query=query, question_list=question_list)
            return await self._search_remote(query=query, question_list=question_list)
        except Exception as exc:
            self._search_state_cache = PaperSearchRuntimeState(
                enabled=bool(self.search_cfg.enabled),
                started=False,
                availability='became_unavailable_during_run',
                base_url=self._configured_search_base_url(),
                health_url=self._search_health_url(),
                error=f'{type(exc).__name__}: {exc}',
                provider=self._search_provider(),
            )
            raise

    async def read_papers(self, *, items: list[dict]) -> dict:
        if self.read_configured:
            return await self._read_remote(items)
        return await self._read_arxiv_fallback(items)

    async def get_search_runtime_state(
        self,
        *,
        force_refresh: bool = False,
    ) -> PaperSearchRuntimeState:
        if self._search_state_cache is not None and not force_refresh:
            return self._search_state_cache

        provider = self._search_provider()
        base_url = self._configured_search_base_url()
        health_url = self._search_health_url()
        if not bool(self.search_cfg.enabled):
            state = PaperSearchRuntimeState(
                enabled=False,
                started=False,
                availability='disabled_by_config',
                base_url=base_url,
                health_url=health_url,
                provider=provider,
            )
            self._search_state_cache = state
            return state

        if not base_url:
            state = PaperSearchRuntimeState(
                enabled=True,
                started=False,
                availability='missing_base_url',
                base_url=None,
                health_url=health_url,
                provider=provider,
            )
            self._search_state_cache = state
            return state

        if provider == 'deepxiv' and not str(self.search_cfg.deepxiv_api_token or '').strip():
            state = PaperSearchRuntimeState(
                enabled=True,
                started=False,
                availability='missing_api_token',
                base_url=base_url,
                health_url=health_url,
                error='DEEPXIV_API_TOKEN is required when PAPER_SEARCH_PROVIDER=deepxiv.',
                provider=provider,
            )
            self._search_state_cache = state
            return state

        if provider != 'deepxiv' and not str(self.search_cfg.health_endpoint or '').strip():
            state = PaperSearchRuntimeState(
                enabled=True,
                started=True,
                availability='ready',
                base_url=base_url,
                health_url=None,
                provider=provider,
            )
            self._search_state_cache = state
            return state

        headers = self._search_headers()

        try:
            async with httpx.AsyncClient(
                timeout=max(1, int(self.search_cfg.health_timeout_seconds)),
            ) as client:
                if provider == 'deepxiv':
                    response = await client.get(health_url, headers=headers, params={'days': 1})
                else:
                    response = await client.get(health_url, headers=headers)
            response.raise_for_status()

            payload = None
            try:
                payload = response.json()
            except Exception:
                payload = None

            if isinstance(payload, dict):
                status = str(payload.get('status') or '').strip().lower()
                if status and status not in {'healthy', 'ok', 'ready'}:
                    raise RuntimeError(
                        str(payload.get('error') or payload.get('message') or f'health status={status}')
                    )
                if 'models_loaded' in payload and not bool(payload.get('models_loaded')):
                    raise RuntimeError(
                        str(payload.get('error') or payload.get('message') or 'models_loaded=false')
                    )

            state = PaperSearchRuntimeState(
                enabled=True,
                started=True,
                availability='ready',
                base_url=base_url,
                health_url=health_url,
                provider=provider,
            )
        except Exception as exc:
            state = PaperSearchRuntimeState(
                enabled=True,
                started=False,
                availability='health_check_failed',
                base_url=base_url,
                health_url=health_url,
                error=f'{type(exc).__name__}: {exc}',
                provider=provider,
            )

        self._search_state_cache = state
        return state

    def _search_provider(self) -> str:
        provider = str(self.search_cfg.provider or '').strip().lower()
        if provider in {'deepxiv', 'pasa'}:
            return provider
        return 'pasa'

    def _configured_search_base_url(self) -> str | None:
        if self._search_provider() == 'deepxiv':
            return str(self.search_cfg.deepxiv_base_url or '').strip() or None
        return str(self.search_cfg.base_url or '').strip() or None

    def _search_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._search_provider() == 'deepxiv':
            headers['Accept'] = 'application/json'
            api_key = str(self.search_cfg.deepxiv_api_token or '').strip()
        else:
            api_key = str(self.search_cfg.api_key or '').strip()
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'
        return headers

    def _search_health_url(self) -> str | None:
        base_url = str(self._configured_search_base_url() or '').strip()
        if self._search_provider() == 'deepxiv':
            health_endpoint = '/stats/usage'
        else:
            health_endpoint = str(self.search_cfg.health_endpoint or '').strip()
        if not base_url or not health_endpoint:
            return None
        return f"{base_url.rstrip('/')}/{health_endpoint.lstrip('/')}"

    def _search_not_started_payload(
        self,
        *,
        state: PaperSearchRuntimeState,
        query: str | None,
        question_list: list[str] | None,
    ) -> dict:
        questions = [q for q in (question_list or []) if str(q or '').strip()]
        query_text = str(query or '').strip()
        if query_text and query_text not in questions:
            questions = [query_text, *questions]

        return {
            'status': 'not_started',
            'success': False,
            'reason': 'paper_search_not_started',
            'message': 'External paper search was not started in this run.',
            'query': query_text,
            'questions': questions,
            'papers': [],
            'count': 0,
            'question_results': [],
            'retry_required': False,
            'next_action': 'enter_retrieval_disabled_mode',
            'next_steps': [
                'Proceed without external literature search in this run.',
                'Mark novelty/comparison conclusions as deferred manual verification.',
                'If external literature search is required, start the retrieval service and rerun the job.',
            ],
            'paper_search_state': state.to_dict(),
        }

    def _normalize_search_questions(
        self,
        *,
        query: str | None,
        question_list: list[str] | None,
    ) -> list[str]:
        questions = [str(q or '').strip() for q in (question_list or []) if str(q or '').strip()]
        query_text = str(query or '').strip()
        if query_text and query_text not in questions:
            questions = [query_text, *questions]

        cleaned: list[str] = []
        seen: set[str] = set()
        for question in questions:
            normalized = ' '.join(question.split())
            key = normalized.lower()
            if not normalized or key in seen:
                continue
            seen.add(key)
            cleaned.append(normalized)
        return cleaned[:3]

    async def _search_deepxiv(
        self,
        *,
        query: str | None,
        question_list: list[str] | None,
    ) -> dict:
        questions = self._normalize_search_questions(query=query, question_list=question_list)
        if not questions:
            return {
                'success': False,
                'reason': 'empty_query',
                'message': 'Query cannot be empty. Provide query or question_list.',
                'query': '',
                'questions': [],
                'papers': [],
                'count': 0,
                'question_results': [],
                'provider': 'deepxiv',
            }

        question_results = []
        for question in questions:
            question_results.append(await self._run_single_deepxiv_query(question))

        merged_papers: list[dict] = []
        seen: set[str] = set()
        for group in question_results:
            for paper in group.get('papers', []):
                if not isinstance(paper, dict):
                    continue
                key = str(paper.get('arxiv_id') or paper.get('id') or paper.get('url') or '').strip()
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                merged_papers.append(paper)

        success = any(bool(group.get('success')) for group in question_results)
        first_error = next(
            (
                str(group.get('error'))
                for group in question_results
                if isinstance(group.get('error'), str) and group.get('error')
            ),
            '',
        )
        payload = {
            'success': success,
            'provider': 'deepxiv',
            'query': questions[0],
            'questions': questions,
            'papers': merged_papers,
            'count': len(merged_papers),
            'question_results': question_results,
            'paper_search_state': (await self.get_search_runtime_state()).to_dict(),
        }
        if not success:
            payload.update(
                {
                    'reason': 'all_queries_failed',
                    'error': first_error or 'DeepXiv search failed for all questions.',
                    'message': first_error or 'DeepXiv search failed for all questions.',
                    'retry_required': True,
                    'retry_tool': 'paper_search',
                    'next_steps': [
                        'Check DEEPXIV_API_TOKEN and DEEPXIV_API_BASE_URL.',
                        'Retry paper_search after the DeepXiv service is reachable.',
                    ],
                }
            )
        return payload

    async def _run_single_deepxiv_query(self, question: str) -> dict:
        base_url = str(self.search_cfg.deepxiv_base_url or '').strip()
        url = f"{base_url.rstrip('/')}/arxiv/"
        params = {
            'type': 'retrieve',
            'query': question,
            'top_k': max(1, min(100, int(self.search_cfg.deepxiv_top_k or 8))),
            'source': self._normalize_deepxiv_source(self.search_cfg.deepxiv_default_source),
        }
        headers = self._search_headers()
        try:
            async with httpx.AsyncClient(
                timeout=max(5, int(self.search_cfg.deepxiv_timeout_seconds or 60)),
            ) as client:
                response = await client.get(url, headers=headers, params=params)
            try:
                data = response.json()
            except Exception:
                data = response.text

            if response.status_code != 200:
                return self._deepxiv_query_error(
                    question=question,
                    status_code=response.status_code,
                    payload=data,
                )

            if not isinstance(data, dict):
                return self._deepxiv_query_error(
                    question=question,
                    status_code=None,
                    payload=data,
                    fallback='DeepXiv returned a non-JSON payload.',
                )

            papers = self._format_deepxiv_papers(data.get('result'))
            return {
                'question': question,
                'query': question,
                'success': True,
                'count': len(papers),
                'papers': papers,
                'provider': 'deepxiv',
                'total_count': int(data.get('total_count') or len(papers)),
            }
        except Exception as exc:
            return {
                'question': question,
                'query': question,
                'success': False,
                'count': 0,
                'papers': [],
                'provider': 'deepxiv',
                'reason': 'request_failed',
                'error': f'{type(exc).__name__}: {exc}',
                'message': 'DeepXiv request failed before a valid response was returned.',
                'next_steps': [
                    'Check DeepXiv network connectivity and credentials.',
                    'Retry paper_search after the service is reachable.',
                ],
            }

    def _deepxiv_query_error(
        self,
        *,
        question: str,
        status_code: int | None,
        payload: object,
        fallback: str | None = None,
    ) -> dict:
        error = self._extract_remote_error(payload, fallback=fallback or 'DeepXiv request failed.')
        if status_code in {401, 403}:
            reason = 'auth_error'
            message = 'DeepXiv authentication failed. Check DEEPXIV_API_TOKEN.'
        elif status_code == 429:
            reason = 'rate_limit'
            message = 'DeepXiv rate limit exceeded.'
        elif status_code == 404:
            reason = 'endpoint_not_found'
            message = 'DeepXiv endpoint was not found. Check DEEPXIV_API_BASE_URL.'
        else:
            reason = 'request_failed'
            message = error
        return {
            'question': question,
            'query': question,
            'success': False,
            'count': 0,
            'papers': [],
            'provider': 'deepxiv',
            'reason': reason,
            'error': message,
            'message': message,
            'next_steps': [
                'Verify DeepXiv API settings.',
                'Retry paper_search after fixing the service/token problem.',
            ],
        }

    def _extract_remote_error(self, payload: object, *, fallback: str) -> str:
        if isinstance(payload, dict):
            for key in ('error', 'message', 'detail'):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        if isinstance(payload, str) and payload.strip():
            return payload.strip()
        return fallback

    def _normalize_deepxiv_source(self, source: object) -> str:
        value = str(source or '').strip().lower()
        if value in {'arxiv', 'biorxiv', 'medrxiv'}:
            return value
        return 'arxiv'

    def _normalize_deepxiv_authors(self, raw_authors: object) -> list[dict]:
        if not isinstance(raw_authors, list):
            return []
        authors: list[dict] = []
        for raw in raw_authors:
            if isinstance(raw, dict):
                name = str(raw.get('name') or '').strip()
                orgs_raw = raw.get('orgs')
                orgs = [str(item or '').strip() for item in orgs_raw] if isinstance(orgs_raw, list) else []
                orgs = [item for item in orgs if item]
                if name or orgs:
                    authors.append({'name': name, 'orgs': orgs})
            elif isinstance(raw, str) and raw.strip():
                authors.append({'name': raw.strip(), 'orgs': []})
        return authors

    def _format_deepxiv_papers(self, results: object) -> list[dict]:
        if not isinstance(results, list):
            return []

        papers: list[dict] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            arxiv_id = str(item.get('arxiv_id') or item.get('id') or '').strip()
            title = str(item.get('title') or 'Untitled').strip()
            abstract = str(item.get('abstract') or item.get('tldr') or '').strip()
            pdf_url = str(item.get('url') or '').strip()
            abs_url = f'https://arxiv.org/abs/{arxiv_id}' if arxiv_id else ''
            papers.append(
                {
                    'id': arxiv_id,
                    'arxiv_id': arxiv_id,
                    'title': title,
                    'abstract': abstract,
                    'url': abs_url,
                    'abs_url': abs_url,
                    'pdf_url': pdf_url or (f'https://arxiv.org/pdf/{arxiv_id}.pdf' if arxiv_id else ''),
                    'source': 'deepxiv',
                    'provider': 'deepxiv',
                    'tldr': str(item.get('tldr') or '').strip(),
                    'citation_count': item.get('citation_count'),
                    'categories': item.get('categories') if isinstance(item.get('categories'), list) else [],
                    'authors': self._normalize_deepxiv_authors(item.get('authors')),
                    'published_at': item.get('date'),
                    'deepxiv_score': item.get('score'),
                }
            )
        return papers

    async def _search_remote(
        self,
        *,
        query: str | None,
        question_list: list[str] | None,
    ) -> dict:
        assert self.search_cfg.base_url is not None

        url = f"{self.search_cfg.base_url.rstrip('/')}/{self.search_cfg.endpoint.lstrip('/')}"
        headers = {'Content-Type': 'application/json'}
        api_key = str(self.search_cfg.api_key or '').strip()
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'
        payload = {'query': query, 'question_list': question_list}

        async with httpx.AsyncClient(timeout=max(20, int(self.search_cfg.timeout_seconds))) as client:
            response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()

        data = response.json()
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            papers = [self._normalize_remote_paper_item(item) for item in data if isinstance(item, dict)]
            papers = [row for row in papers if row]
            questions = [q for q in (question_list or []) if str(q or '').strip()]
            query_text = str(query or '').strip()
            if query_text and query_text not in questions:
                questions = [query_text, *questions]
            return {
                'success': True,
                'provider': 'remote_list_adapted',
                'query': query_text,
                'questions': questions,
                'papers': papers,
                'count': len(papers),
                'question_results': [
                    {
                        'question': q,
                        'success': bool(papers),
                        'count': len(papers),
                        'papers': papers,
                    }
                    for q in (questions or ([query_text] if query_text else []))
                ],
            }
        return {
            'success': False,
            'error': 'invalid_remote_payload',
            'papers': [],
            'count': 0,
        }

    async def _read_remote(self, items: list[dict]) -> dict:
        assert self.read_cfg.base_url is not None

        url = f"{self.read_cfg.base_url.rstrip('/')}/{self.read_cfg.endpoint.lstrip('/')}"
        headers = {'Content-Type': 'application/json'}
        api_key = str(self.read_cfg.api_key or '').strip()
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'

        async with httpx.AsyncClient(timeout=max(20, int(self.read_cfg.timeout_seconds))) as client:
            response = await client.post(url, headers=headers, json={'items': items})
        response.raise_for_status()

        data = response.json()
        if isinstance(data, dict):
            return data
        return {
            'success': False,
            'error': 'invalid_remote_payload',
            'items': [],
        }

    async def _search_arxiv_fallback(
        self,
        *,
        query: str | None,
        question_list: list[str] | None,
    ) -> dict:
        questions = [q for q in (question_list or []) if str(q or '').strip()]
        if not questions and query:
            questions = [query]
        if not questions:
            return {
                'success': False,
                'error': 'empty_query',
                'papers': [],
                'count': 0,
                'question_results': [],
                'provider': 'arxiv_fallback',
            }

        all_papers: list[dict] = []
        seen: set[str] = set()
        question_results: list[dict] = []

        for q in questions:
            papers = await self._arxiv_query(q, max_results=8)
            question_results.append(
                {
                    'question': q,
                    'success': bool(papers),
                    'count': len(papers),
                    'papers': papers,
                }
            )
            for paper in papers:
                key = str(paper.get('arxiv_id') or paper.get('url') or '')
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                all_papers.append(paper)

        return {
            'success': True,
            'query': questions[0],
            'questions': questions,
            'papers': all_papers,
            'count': len(all_papers),
            'question_results': question_results,
            'provider': 'arxiv_fallback',
        }

    async def _read_arxiv_fallback(self, items: list[dict]) -> dict:
        normalized = [item for item in items if isinstance(item, dict)]
        if not normalized:
            return {
                'success': False,
                'error': 'empty_items',
                'items': [],
                'provider': 'arxiv_fallback',
            }

        outputs: list[dict] = []
        for item in normalized[:8]:
            arxiv_id = str(item.get('id') or item.get('arxiv_id') or '').strip()
            question = str(item.get('question') or '').strip()
            title_hint = str(item.get('title') or '').strip()

            if not arxiv_id and title_hint:
                guessed = await self._arxiv_query(title_hint, max_results=1)
                if guessed:
                    arxiv_id = str(guessed[0].get('arxiv_id') or '').strip()

            if not arxiv_id:
                outputs.append(
                    {
                        'id': '',
                        'question': question,
                        'success': False,
                        'error': 'missing_arxiv_id',
                    }
                )
                continue

            detail = await self._arxiv_fetch_single(arxiv_id)
            if not detail:
                outputs.append(
                    {
                        'id': arxiv_id,
                        'question': question,
                        'success': False,
                        'error': 'paper_not_found',
                    }
                )
                continue

            answer = self._build_read_answer(detail=detail, question=question)
            outputs.append(
                {
                    'id': arxiv_id,
                    'question': question,
                    'success': True,
                    'paper': detail,
                    'answer': answer,
                }
            )

        return {
            'success': True,
            'items': outputs,
            'count': len(outputs),
            'provider': 'arxiv_fallback',
        }

    def _build_read_answer(self, *, detail: dict, question: str) -> str:
        title = str(detail.get('title') or '').strip()
        abstract = str(detail.get('abstract') or '').strip()
        if not abstract:
            abstract = 'No abstract available.'

        if not question:
            return f"Title: {title}\n\nAbstract:\n{abstract}"

        return (
            f"Question: {question}\n\n"
            f"From paper '{title}', available evidence (abstract-level) is:\n{abstract}\n\n"
            'Note: This fallback reader uses arXiv metadata/abstract, not full-text deep parsing.'
        )

    async def _arxiv_query(self, question: str, *, max_results: int) -> list[dict]:
        tokens = self._question_to_arxiv_query(question)
        query = quote_plus(tokens)
        url = (
            'https://export.arxiv.org/api/query?'
            f'search_query=all:{query}&start=0&max_results={max(1, min(16, max_results))}'
        )

        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.get(url)
        response.raise_for_status()

        return self._parse_arxiv_feed(response.text)

    async def _arxiv_fetch_single(self, arxiv_id: str) -> dict | None:
        clean = arxiv_id.strip()
        if not clean:
            return None

        query = quote_plus(f'id:{clean}')
        url = f'https://export.arxiv.org/api/query?search_query={query}&start=0&max_results=1'

        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.get(url)
        response.raise_for_status()

        papers = self._parse_arxiv_feed(response.text)
        return papers[0] if papers else None

    def _question_to_arxiv_query(self, question: str) -> str:
        text = re.sub(r'\s+', ' ', str(question or '').strip().lower())
        text = re.sub(r'[^a-z0-9\s-]', ' ', text)
        tokens = [tok for tok in text.split(' ') if tok]
        stop = {
            'what', 'which', 'how', 'are', 'is', 'the', 'for', 'of', 'to', 'in', 'and', 'on', 'with',
            'recent', 'papers', 'methods', 'paper', 'about', 'does', 'can', 'be', 'used', 'that',
        }
        kept = [tok for tok in tokens if tok not in stop]
        return ' '.join(kept[:10]) or text

    def _normalize_remote_paper_item(self, item: dict) -> dict:
        title = str(item.get('title') or '').strip()
        snippet = str(item.get('snippet') or item.get('abstract') or '').strip()
        link = str(item.get('link') or item.get('url') or '').strip()
        raw_id = str(item.get('id') or item.get('arxiv_id') or '').strip()

        # Common PASA list response uses "link" as arXiv identifier.
        arxiv_id = raw_id
        if not arxiv_id and link and 'http' not in link:
            arxiv_id = link
        if arxiv_id.startswith('arXiv:'):
            arxiv_id = arxiv_id.split(':', 1)[1].strip()

        abs_url = ''
        pdf_url = ''
        if arxiv_id:
            abs_url = f'https://arxiv.org/abs/{arxiv_id}'
            pdf_url = f'https://arxiv.org/pdf/{arxiv_id}.pdf'
        elif link.startswith('http://') or link.startswith('https://'):
            abs_url = link

        return {
            'id': arxiv_id or link,
            'arxiv_id': arxiv_id,
            'title': title,
            'abstract': snippet,
            'url': abs_url or link,
            'abs_url': abs_url or link,
            'pdf_url': pdf_url,
            'source': 'remote',
        }

    def _parse_arxiv_feed(self, xml_text: str) -> list[dict]:
        root = ET.fromstring(xml_text)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        papers: list[dict] = []

        for entry in root.findall('atom:entry', ns):
            entry_id = entry.findtext('atom:id', default='', namespaces=ns)
            title = entry.findtext('atom:title', default='', namespaces=ns).strip()
            summary = entry.findtext('atom:summary', default='', namespaces=ns).strip()
            published = entry.findtext('atom:published', default='', namespaces=ns).strip()
            updated = entry.findtext('atom:updated', default='', namespaces=ns).strip()

            authors: list[str] = []
            for author in entry.findall('atom:author', ns):
                name = author.findtext('atom:name', default='', namespaces=ns).strip()
                if name:
                    authors.append(name)

            arxiv_id = entry_id.rsplit('/', 1)[-1] if entry_id else ''
            abs_url = f'https://arxiv.org/abs/{arxiv_id}' if arxiv_id else ''
            pdf_url = f'https://arxiv.org/pdf/{arxiv_id}.pdf' if arxiv_id else ''

            papers.append(
                {
                    'title': title,
                    'abstract': summary,
                    'authors': authors,
                    'published': published,
                    'updated': updated,
                    'arxiv_id': arxiv_id,
                    'url': abs_url,
                    'abs_url': abs_url,
                    'pdf_url': pdf_url,
                    'source': 'arxiv',
                }
            )

        return papers


def normalize_question_list(raw: object) -> list[str]:
    raw_items: list[str] = []
    if isinstance(raw, list):
        raw_items.extend(str(item).strip() for item in raw if str(item).strip())

    if isinstance(raw, str):
        text = raw.strip()
        if text:
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                raw_items.extend(str(item).strip() for item in parsed if str(item).strip())
            else:
                raw_items.extend(
                    line.strip('-• \t') for line in text.splitlines() if line.strip('-• \t')
                )

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        normalized = ' '.join(item.split())
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(normalized)
    return cleaned[:3]
