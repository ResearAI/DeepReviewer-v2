from __future__ import annotations

import asyncio
import json
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx

from deepreview.adapters.markdown_parser import MarkdownParseResult, parse_pdf_locally


@dataclass
class MineruConfig:
    base_url: str
    api_token: str | None
    model_version: str
    upload_endpoint: str
    poll_endpoint_templates: list[str]
    poll_interval_seconds: float
    poll_timeout_seconds: int
    allow_local_fallback: bool


@dataclass
class MineruParseResult:
    markdown: str
    content_list: list[dict[str, Any]] | None
    batch_id: str | None
    raw_result: dict[str, Any] | None
    provider: str
    warning: str | None = None


class MineruAdapter:
    def __init__(self, cfg: MineruConfig):
        self.cfg = cfg

    @property
    def configured(self) -> bool:
        return bool(self.cfg.api_token and self.cfg.base_url)

    async def parse_pdf(self, *, pdf_path: Path, data_id: str) -> MineruParseResult:
        pdf_bytes = pdf_path.read_bytes()

        if not self.configured:
            if not self.cfg.allow_local_fallback:
                raise RuntimeError('MinerU is not configured and local fallback is disabled')
            return self._local_fallback(
                pdf_bytes,
                warning='MinerU is not configured; used local pypdf parser fallback.',
            )

        try:
            return await self._parse_via_mineru(pdf_path=pdf_path, pdf_bytes=pdf_bytes, data_id=data_id)
        except Exception as exc:
            if not self.cfg.allow_local_fallback:
                raise RuntimeError(f'MinerU parse failed and fallback is disabled: {exc}') from exc
            return self._local_fallback(
                pdf_bytes,
                warning=f'MinerU parse failed; used local pypdf parser fallback. reason={type(exc).__name__}: {exc}',
            )

    async def _parse_via_mineru(
        self,
        *,
        pdf_path: Path,
        pdf_bytes: bytes,
        data_id: str,
    ) -> MineruParseResult:
        assert self.cfg.api_token is not None
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.cfg.api_token}',
        }
        payload = {
            'files': [{'name': pdf_path.name, 'data_id': data_id}],
            'model_version': self.cfg.model_version,
        }

        upload_url = self._build_url(self.cfg.upload_endpoint)
        timeout = max(20, int(self.cfg.poll_timeout_seconds))

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(upload_url, headers=headers, json=payload)
            response.raise_for_status()
            apply_result = response.json()

            if int(apply_result.get('code', -1)) != 0:
                raise RuntimeError(f"MinerU apply upload URL failed: {apply_result}")

            data = apply_result.get('data') or {}
            batch_id = str(data.get('batch_id') or '').strip() or None
            urls = data.get('file_urls')
            if not isinstance(urls, list) or not urls:
                raise RuntimeError(f'MinerU response missing file_urls: {apply_result}')

            for url in urls:
                if not isinstance(url, str) or not url.strip():
                    raise RuntimeError(f'Invalid upload URL in MinerU response: {url!r}')
                put_resp = await client.put(url, content=pdf_bytes)
                if put_resp.status_code != 200:
                    raise RuntimeError(f'MinerU upload failed ({put_resp.status_code}) for {url}')

            if not batch_id:
                raise RuntimeError('MinerU response missing batch_id')

            raw_result = await self._poll_batch_result(
                client=client,
                batch_id=batch_id,
                apply_payload=apply_result,
            )
            markdown, content_list = await self._extract_outputs(client=client, payload=raw_result)
            if not markdown.strip():
                raise RuntimeError('MinerU returned empty markdown result')

        return MineruParseResult(
            markdown=markdown,
            content_list=content_list,
            batch_id=batch_id,
            raw_result=raw_result,
            provider='mineru_v4',
        )

    async def _poll_batch_result(
        self,
        *,
        client: httpx.AsyncClient,
        batch_id: str,
        apply_payload: dict[str, Any],
    ) -> dict[str, Any]:
        deadline = asyncio.get_event_loop().time() + max(30, int(self.cfg.poll_timeout_seconds))
        last_payload: dict[str, Any] | None = None

        status_urls = self._build_status_urls(batch_id=batch_id, apply_payload=apply_payload)
        if not status_urls:
            raise RuntimeError('No MinerU status polling URL available')

        while asyncio.get_event_loop().time() < deadline:
            for status_url in status_urls:
                try:
                    resp = await client.get(status_url, headers={'Authorization': f'Bearer {self.cfg.api_token}'})
                except Exception:
                    continue

                if resp.status_code >= 500:
                    continue
                if resp.status_code == 404:
                    continue

                payload = self._safe_json(resp)
                if not isinstance(payload, dict):
                    continue

                last_payload = payload
                if self._is_terminal_success(payload):
                    return payload
                if self._is_terminal_failure(payload):
                    raise RuntimeError(f'MinerU batch failed: {json.dumps(payload, ensure_ascii=False)}')

            await asyncio.sleep(max(0.8, float(self.cfg.poll_interval_seconds)))

        if last_payload is not None:
            raise TimeoutError(
                f'MinerU batch polling timeout: batch_id={batch_id}, last={json.dumps(last_payload, ensure_ascii=False)}'
            )
        raise TimeoutError(f'MinerU batch polling timeout without payload: batch_id={batch_id}')

    async def _extract_outputs(
        self,
        *,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
    ) -> tuple[str, list[dict[str, Any]] | None]:
        markdown = self._extract_markdown_from_payload(payload)
        content_list = self._extract_content_list_from_payload(payload)

        md_url = self._extract_first_url(payload, keys=('markdown_url', 'md_url', 'full_md_url', 'full_md'))
        if not markdown and md_url:
            markdown = await self._download_text(client, md_url)

        content_url = self._extract_first_url(
            payload,
            keys=('content_list_url', 'content_list_json_url', 'content_list_json'),
        )
        if content_list is None and content_url:
            content_list = await self._download_json_list(client, content_url)

        zip_url = self._extract_first_url(
            payload,
            keys=('full_zip_url', 'zip_url', 'result_zip_url', 'download_url'),
        )
        if zip_url and (not markdown or content_list is None):
            md_from_zip, content_from_zip = await self._download_from_zip(client, zip_url)
            if not markdown and md_from_zip:
                markdown = md_from_zip
            if content_list is None and content_from_zip is not None:
                content_list = content_from_zip

        if not markdown:
            nested = payload.get('data') if isinstance(payload.get('data'), dict) else payload
            files = nested.get('files') if isinstance(nested, dict) else None
            if isinstance(files, list):
                md_parts: list[str] = []
                for item in files:
                    if not isinstance(item, dict):
                        continue
                    for key in ('markdown', 'md', 'full_md'):
                        value = item.get(key)
                        if isinstance(value, str) and value.strip():
                            md_parts.append(value.strip())
                markdown = '\n\n---\n\n'.join(md_parts)

        return markdown or '', content_list

    def _extract_markdown_from_payload(self, payload: dict[str, Any]) -> str:
        candidate_dicts: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            candidate_dicts.append(payload)
            data = payload.get('data')
            if isinstance(data, dict):
                candidate_dicts.append(data)
                result = data.get('result')
                if isinstance(result, dict):
                    candidate_dicts.append(result)

        for data in candidate_dicts:
            for key in ('markdown', 'md', 'full_md', 'full_markdown'):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        return ''

    def _extract_content_list_from_payload(self, payload: dict[str, Any]) -> list[dict[str, Any]] | None:
        candidate_dicts: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            candidate_dicts.append(payload)
            data = payload.get('data')
            if isinstance(data, dict):
                candidate_dicts.append(data)

        for data in candidate_dicts:
            for key in ('content_list', 'content_list_json', 'mineru_content_list'):
                value = data.get(key)
                if isinstance(value, list):
                    rows = [row for row in value if isinstance(row, dict)]
                    return rows

        return None

    def _extract_first_url(self, payload: dict[str, Any], *, keys: tuple[str, ...]) -> str | None:
        queue: list[Any] = [payload]
        while queue:
            current = queue.pop(0)
            if isinstance(current, dict):
                for key, value in current.items():
                    if key in keys and isinstance(value, str) and value.strip():
                        return self._resolve_possible_url(value)
                    if isinstance(value, (dict, list)):
                        queue.append(value)
            elif isinstance(current, list):
                queue.extend(current)
        return None

    async def _download_text(self, client: httpx.AsyncClient, url: str) -> str:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text

    async def _download_json_list(self, client: httpx.AsyncClient, url: str) -> list[dict[str, Any]] | None:
        resp = await client.get(url)
        resp.raise_for_status()
        payload = self._safe_json(resp)
        if isinstance(payload, list):
            rows = [row for row in payload if isinstance(row, dict)]
            return rows
        return None

    async def _download_from_zip(
        self,
        client: httpx.AsyncClient,
        url: str,
    ) -> tuple[str, list[dict[str, Any]] | None]:
        resp = await client.get(url)
        resp.raise_for_status()

        markdown_parts: list[str] = []
        content_list: list[dict[str, Any]] | None = None

        with zipfile.ZipFile(BytesIO(resp.content), 'r') as zf:
            for name in zf.namelist():
                lower = name.lower()
                if lower.endswith('.md'):
                    try:
                        markdown_parts.append(zf.read(name).decode('utf-8', errors='ignore'))
                    except Exception:
                        continue
                if lower.endswith('_content_list.json') and content_list is None:
                    try:
                        payload = json.loads(zf.read(name).decode('utf-8', errors='ignore'))
                    except Exception:
                        payload = None
                    if isinstance(payload, list):
                        content_list = [row for row in payload if isinstance(row, dict)]

        return ('\n\n---\n\n'.join(part.strip() for part in markdown_parts if part.strip()), content_list)

    def _build_status_urls(self, *, batch_id: str, apply_payload: dict[str, Any]) -> list[str]:
        urls: list[str] = []

        def add(value: str | None) -> None:
            if not value:
                return
            resolved = self._resolve_possible_url(value)
            if resolved and resolved not in urls:
                urls.append(resolved)

        data = apply_payload.get('data') if isinstance(apply_payload.get('data'), dict) else {}
        if isinstance(data, dict):
            for key in ('status_url', 'result_url', 'batch_status_url', 'batch_result_url'):
                raw = data.get(key)
                if isinstance(raw, str):
                    add(raw)

        for template in self.cfg.poll_endpoint_templates:
            if '{batch_id}' not in template:
                continue
            add(template.format(batch_id=batch_id))

        return urls

    def _is_terminal_success(self, payload: dict[str, Any]) -> bool:
        if self._extract_markdown_from_payload(payload):
            return True

        state = self._extract_state(payload)
        if state in {'done', 'completed', 'success', 'succeeded', 'finished'}:
            return True

        code = payload.get('code')
        if isinstance(code, int) and code == 0:
            data = payload.get('data')
            if isinstance(data, dict):
                if data.get('full_zip_url') or data.get('markdown') or data.get('md'):
                    return True
                extract_result = data.get('extract_result')
                if isinstance(extract_result, list):
                    for item in extract_result:
                        if not isinstance(item, dict):
                            continue
                        item_state = str(item.get('state') or '').strip().lower()
                        if item_state in {'done', 'completed', 'success', 'succeeded', 'finished'}:
                            if (
                                item.get('full_zip_url')
                                or item.get('zip_url')
                                or item.get('result_zip_url')
                                or item.get('markdown_url')
                                or item.get('md_url')
                                or item.get('full_md_url')
                                or item.get('markdown')
                                or item.get('md')
                            ):
                                return True

        return False

    def _is_terminal_failure(self, payload: dict[str, Any]) -> bool:
        state = self._extract_state(payload)
        if state in {'failed', 'error', 'aborted'}:
            return True

        code = payload.get('code')
        if isinstance(code, int) and code != 0:
            msg = str(payload.get('msg') or payload.get('message') or '').lower()
            # MinerU may return "task not found or expire" on one polling endpoint
            # while another endpoint still has valid progress/result for the same batch.
            # Treat this known pattern as non-terminal so the caller can keep polling.
            if code == -60012 and ('task not found' in msg or 'expire' in msg):
                return False
            if msg and 'processing' not in msg and 'running' not in msg:
                return True

        return False

    def _extract_state(self, payload: dict[str, Any]) -> str:
        candidates: list[Any] = [payload]
        data = payload.get('data') if isinstance(payload.get('data'), dict) else None
        if isinstance(data, dict):
            candidates.append(data)
            result = data.get('result')
            if isinstance(result, dict):
                candidates.append(result)

        for item in candidates:
            if not isinstance(item, dict):
                continue
            for key in ('state', 'status', 'task_state', 'batch_state'):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip().lower()

        return ''

    def _local_fallback(self, pdf_bytes: bytes, *, warning: str | None = None) -> MineruParseResult:
        parsed: MarkdownParseResult = parse_pdf_locally(pdf_bytes)
        return MineruParseResult(
            markdown=parsed.markdown,
            content_list=parsed.content_list,
            batch_id=None,
            raw_result=None,
            provider=parsed.provider,
            warning=warning,
        )

    def _build_url(self, endpoint: str) -> str:
        return f"{self.cfg.base_url.rstrip('/')}/{endpoint.lstrip('/')}"

    def _resolve_possible_url(self, value: str) -> str:
        token = str(value or '').strip()
        if not token:
            return ''
        if token.startswith('http://') or token.startswith('https://'):
            return token
        return self._build_url(token)

    def _safe_json(self, response: httpx.Response) -> dict[str, Any] | None:
        try:
            payload = response.json()
        except Exception:
            return None
        if isinstance(payload, dict):
            return payload
        return None
