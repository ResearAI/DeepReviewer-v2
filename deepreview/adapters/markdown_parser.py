from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from pypdf import PdfReader


@dataclass
class MarkdownParseResult:
    markdown: str
    pages: list[str]
    content_list: list[dict[str, Any]] | None = None
    provider: str = 'local_pypdf'


def parse_pdf_locally(pdf_bytes: bytes) -> MarkdownParseResult:
    reader = PdfReader(BytesIO(pdf_bytes))
    pages: list[str] = []
    for page in reader.pages:
        text = (page.extract_text() or '').strip()
        pages.append(text)

    markdown_lines: list[str] = []
    for index, page_text in enumerate(pages, start=1):
        markdown_lines.append(f'## Page {index}')
        markdown_lines.append('')
        markdown_lines.append(page_text)
        markdown_lines.append('')

    content_list: list[dict[str, Any]] = []
    for page_idx, page_text in enumerate(pages):
        for line in page_text.splitlines():
            normalized = line.strip()
            if normalized:
                content_list.append({'page_idx': page_idx, 'type': 'text', 'text': normalized})

    markdown = '\n'.join(markdown_lines).strip()
    return MarkdownParseResult(markdown=markdown, pages=pages, content_list=content_list)


def build_page_index(
    markdown: str,
    content_list: list[dict[str, Any]] | None = None,
) -> dict[int, list[str]]:
    if content_list:
        pages: dict[int, list[str]] = {}
        for item in content_list:
            if not isinstance(item, dict):
                continue
            page_idx = item.get('page_idx')
            if not isinstance(page_idx, int):
                continue
            text = str(item.get('text') or '').strip()
            if not text:
                continue
            pages.setdefault(page_idx + 1, []).append(text)

        if pages:
            return {page: lines for page, lines in sorted(pages.items(), key=lambda x: x[0])}

    heading_pattern = re.compile(r'(?im)^\s*##\s*page\s+(\d+)\s*$')
    matches = list(heading_pattern.finditer(markdown or ''))
    if matches:
        mapped: dict[int, list[str]] = {}
        for idx, match in enumerate(matches):
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown)
            body = (markdown[start:end] or '').strip()
            page = int(match.group(1))
            lines = [line.strip() for line in body.splitlines() if line.strip()]
            mapped[page] = lines
        return mapped

    lines = [line.strip() for line in (markdown or '').splitlines() if line.strip()]
    return {1: lines}


def flatten_page_index(page_index: dict[int, list[str]]) -> list[tuple[int, int, str]]:
    rows: list[tuple[int, int, str]] = []
    for page in sorted(page_index.keys()):
        lines = page_index.get(page, [])
        for offset, text in enumerate(lines, start=1):
            rows.append((page, offset, text))
    return rows
