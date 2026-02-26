from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote_plus

import httpx


@dataclass
class PaperSearchConfig:
    base_url: str | None
    api_key: str | None
    endpoint: str
    timeout_seconds: int


@dataclass
class PaperReadConfig:
    base_url: str | None
    api_key: str | None
    endpoint: str
    timeout_seconds: int


class PaperSearchAdapter:
    def __init__(self, search_cfg: PaperSearchConfig, read_cfg: PaperReadConfig):
        self.search_cfg = search_cfg
        self.read_cfg = read_cfg

    @property
    def search_configured(self) -> bool:
        return bool(self.search_cfg.base_url)

    @property
    def read_configured(self) -> bool:
        return bool(self.read_cfg.base_url)

    async def search(
        self,
        *,
        query: str | None = None,
        question_list: list[str] | None = None,
    ) -> dict:
        if self.search_configured:
            return await self._search_remote(query=query, question_list=question_list)
        return await self._search_arxiv_fallback(query=query, question_list=question_list)

    async def read_papers(self, *, items: list[dict]) -> dict:
        if self.read_configured:
            return await self._read_remote(items)
        return await self._read_arxiv_fallback(items)

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
