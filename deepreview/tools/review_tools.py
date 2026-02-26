from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from agents import RunContextWrapper, function_tool

from deepreview.adapters.paper_search import PaperSearchAdapter, normalize_question_list
from deepreview.config import Settings
from deepreview.report.final_report import validate_final_report
from deepreview.state import mutate_job_state
from deepreview.storage import annotations_path, append_event, write_json_atomic, write_text_atomic
from deepreview.types import AnnotationItem, PaperSearchUsage


def _normalize_signature(text: str) -> str:
    return ' '.join(str(text or '').strip().lower().split())


def _count_papers(result: dict[str, Any]) -> int:
    direct = result.get('count')
    try:
        if int(direct or 0) > 0:
            return int(direct or 0)
    except Exception:
        pass

    papers = result.get('papers')
    if isinstance(papers, list):
        rows = [row for row in papers if isinstance(row, dict)]
        if rows:
            return len(rows)

    question_results = result.get('question_results')
    total = 0
    if isinstance(question_results, list):
        for row in question_results:
            if not isinstance(row, dict):
                continue
            try:
                total += max(0, int(row.get('count') or 0))
            except Exception:
                continue
    return total


_REVIEW_RECOMMENDED_ANNOTATION_MIN = 12
_REVIEW_RECOMMENDED_ANNOTATION_MAX = 25
_FINAL_REPORT_SECTION_HEADING_PATTERN = re.compile(r'^\s{0,3}#{1,6}\s+(.+?)\s*$')
_REQUIRED_FINAL_REPORT_SECTIONS: list[tuple[str, str, tuple[str, ...]]] = [
    ('summary', 'Summary', ('summary',)),
    ('strengths', 'Strengths', ('strengths',)),
    ('weaknesses', 'Weaknesses', ('weaknesses',)),
    ('key_issues', 'Key Issues', ('key issues', 'issues')),
    ('actionable_suggestions', 'Actionable Suggestions', ('actionable suggestions', 'suggestions')),
    (
        'storyline_options_writing_outlines',
        'Storyline Options + Writing Outlines',
        ('storyline options + writing outlines', 'storyline options', 'writing outlines', 'storylines'),
    ),
    ('priority_revision_plan', 'Priority Revision Plan', ('priority revision plan', 'revision plan')),
    (
        'experiment_inventory_research_experiment_plan',
        'Experiment Inventory & Research Experiment Plan',
        (
            'experiment inventory & research experiment plan',
            'experiment inventory',
            'research experiment plan',
        ),
    ),
    (
        'novelty_verification_related_work_matrix',
        'Novelty Verification & Related-Work Matrix',
        (
            'novelty verification & related-work matrix',
            'novelty verification & related work matrix',
            'novelty verification',
            'related-work matrix',
            'related work matrix',
        ),
    ),
    ('references', 'References', ('references', 'reference')),
    ('scores', 'Scores', ('scores', 'score', 'final score')),
]


def _build_annotation_gate_hint(*, total_calls: int, required_calls: int) -> str:
    if total_calls >= required_calls:
        return (
            f'paper_search total calls so far: {total_calls} (>= {required_calls}). '
            'You can now start paragraph-by-paragraph PDF annotation.'
        )
    remaining = max(0, required_calls - total_calls)
    return (
        f'paper_search total calls so far: {total_calls}. '
        f'Run at least {remaining} more paper_search call(s) before starting pdf_annotate.'
    )


def _build_annotation_progress_hint(
    *,
    total_annotations: int,
    final_trigger_min: int,
    recommended_min: int,
    recommended_max: int,
) -> str:
    if total_annotations < final_trigger_min:
        remaining = final_trigger_min - total_annotations
        return (
            f'Current review annotations: {total_annotations}. '
            f'Final-report gate requires >= {final_trigger_min}; add about {remaining} more.'
        )
    if total_annotations < recommended_min:
        return (
            f'Current review annotations: {total_annotations}. '
            f'Final-report trigger is satisfied; usual quality range is {recommended_min}-{recommended_max}.'
        )
    if total_annotations > recommended_max:
        return (
            f'Current review annotations: {total_annotations} (> {recommended_max}). '
            'Prefer consolidating high-impact findings and avoid low-value over-annotation.'
        )
    return (
        f'Current review annotations: {total_annotations} '
        f'(within recommended range {recommended_min}-{recommended_max}).'
    )


def _flatten_page_index(page_index: dict[int, list[str]]) -> list[tuple[int, int, str]]:
    rows: list[tuple[int, int, str]] = []
    for page in sorted(page_index.keys()):
        lines = page_index.get(page) or []
        for line_no, text in enumerate(lines, start=1):
            rows.append((page, line_no, text))
    return rows


def _coerce_markdown_text(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _coerce_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    if '\n' in text:
        return [line.strip(' -\t') for line in text.splitlines() if line.strip(' -\t')]
    if ';' in text:
        return [line.strip(' -\t') for line in text.split(';') if line.strip(' -\t')]
    return [text]


def _normalize_final_report_section_token(value: Any) -> str:
    token = str(value or '').strip().lower()
    if not token:
        return ''
    token = token.replace('&', ' and ')
    token = token.replace('+', ' ')
    token = token.replace('/', ' ')
    token = token.replace('\\', ' ')
    token = token.replace('_', ' ')
    token = token.replace('-', ' ')
    token = re.sub(r'[^0-9a-z\s]', ' ', token)
    token = re.sub(r'\s+', ' ', token).strip()
    return token


def _required_final_report_section_order() -> list[str]:
    return [section_id for section_id, _title, _aliases in _REQUIRED_FINAL_REPORT_SECTIONS]


def _required_final_report_section_titles() -> dict[str, str]:
    return {section_id: title for section_id, title, _aliases in _REQUIRED_FINAL_REPORT_SECTIONS}


def _required_final_report_alias_map() -> dict[str, str]:
    alias_map: dict[str, str] = {}
    for section_id, title, aliases in _REQUIRED_FINAL_REPORT_SECTIONS:
        for raw_alias in (section_id, title, *aliases):
            normalized_alias = _normalize_final_report_section_token(raw_alias)
            if normalized_alias:
                alias_map[normalized_alias] = section_id
    return alias_map


def _resolve_final_report_section_id(section_key: Any) -> str | None:
    normalized = _normalize_final_report_section_token(section_key)
    if not normalized:
        return None
    alias_map = _required_final_report_alias_map()
    direct = alias_map.get(normalized)
    if direct:
        return direct
    for alias, section_id in alias_map.items():
        if alias and alias in normalized:
            return section_id
    return None


def _coerce_section_markdown(value: Any, *, list_as_bullets: bool = True) -> str:
    if value is None:
        return ''
    if isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value if str(item).strip()]
        if not items:
            return ''
        if list_as_bullets:
            return '\n'.join(f'- {item}' for item in items)
        return '\n'.join(items)
    return str(value).strip()


def _strip_leading_section_heading(*, section_id: str, content: str) -> str:
    text = str(content or '').strip()
    if not text:
        return ''
    lines = text.splitlines()
    if not lines:
        return text
    first = lines[0].strip()
    matched = _FINAL_REPORT_SECTION_HEADING_PATTERN.match(first)
    if not matched:
        return text
    heading_text = matched.group(1)
    if _resolve_final_report_section_id(heading_text) != section_id:
        return text
    idx = 1
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    return '\n'.join(lines[idx:]).strip()


def _normalize_final_report_sections(raw_sections: Any) -> dict[str, str]:
    normalized: dict[str, str] = {}
    if not isinstance(raw_sections, dict):
        return normalized
    for raw_key, raw_value in raw_sections.items():
        section_id = _resolve_final_report_section_id(raw_key)
        if not section_id:
            continue
        content = _coerce_section_markdown(raw_value, list_as_bullets=True).strip()
        if content:
            normalized[section_id] = content
    return normalized


def _build_sections_from_legacy_fields(
    *,
    summary: Any,
    strengths: Any,
    weaknesses: Any,
    issues: Any,
    suggestions: Any,
    storylines: Any,
) -> dict[str, str]:
    sections: dict[str, str] = {}

    summary_text = _coerce_markdown_text(summary)
    if summary_text:
        sections['summary'] = summary_text

    strengths_text = _coerce_section_markdown(strengths, list_as_bullets=True)
    if strengths_text:
        sections['strengths'] = strengths_text

    weaknesses_text = _coerce_section_markdown(weaknesses, list_as_bullets=True)
    if weaknesses_text:
        sections['weaknesses'] = weaknesses_text

    issues_text = _coerce_section_markdown(issues, list_as_bullets=True)
    if issues_text:
        sections['key_issues'] = issues_text

    suggestions_text = _coerce_section_markdown(suggestions, list_as_bullets=True)
    if suggestions_text:
        sections['actionable_suggestions'] = suggestions_text

    storyline_text = _coerce_section_markdown(storylines, list_as_bullets=True)
    if storyline_text:
        sections['storyline_options_writing_outlines'] = storyline_text

    return sections


def _extract_required_sections_from_markdown(markdown_text: str) -> dict[str, str]:
    section_buffers: dict[str, list[str]] = {}
    active_section_id: str | None = None
    for raw_line in str(markdown_text or '').splitlines():
        heading_match = _FINAL_REPORT_SECTION_HEADING_PATTERN.match(raw_line)
        if heading_match:
            heading_text = heading_match.group(1)
            active_section_id = _resolve_final_report_section_id(heading_text)
            if active_section_id and active_section_id not in section_buffers:
                section_buffers[active_section_id] = []
            continue
        if active_section_id:
            section_buffers.setdefault(active_section_id, []).append(raw_line)

    extracted: dict[str, str] = {}
    for section_id, lines in section_buffers.items():
        content = '\n'.join(lines).strip()
        if content:
            extracted[section_id] = content
    return extracted


def _build_final_report_markdown_from_sections(sections: dict[str, str]) -> str:
    blocks: list[str] = []
    title_map = _required_final_report_section_titles()
    for section_id in _required_final_report_section_order():
        content = str(sections.get(section_id) or '').strip()
        if not content:
            continue
        heading = title_map.get(section_id, section_id)
        blocks.append(f'## {heading}\n{content}')
    if not blocks:
        return ''
    return '\n\n'.join(blocks).strip()


def _section_descriptor(section_id: str) -> dict[str, str]:
    title_map = _required_final_report_section_titles()
    return {'id': section_id, 'title': title_map.get(section_id, section_id)}


def _section_descriptor_list(section_ids: list[str]) -> list[dict[str, str]]:
    return [_section_descriptor(section_id) for section_id in section_ids]


def _build_final_report_progress_payload(
    *,
    source: str,
    completed_section_ids: list[str],
    missing_section_ids: list[str],
    annotation_count: int,
    paper_search_usage: dict[str, Any],
    required_paper_search_calls: int,
    required_annotation_count: int,
    draft_version: int,
    status: str = 'partial',
    reason: str = 'required_sections_missing',
    message: str = '',
    retry_required: bool = True,
    next_steps: list[str] | None = None,
    current_section_id: str | None = None,
) -> dict[str, Any]:
    next_section_id = missing_section_ids[0] if missing_section_ids else None
    payload: dict[str, Any] = {
        'status': status,
        'reason': reason,
        'message': message,
        'task_completed': False,
        'source': source,
        'draft_version': max(1, int(draft_version or 1)),
        'required_sections': _section_descriptor_list(_required_final_report_section_order()),
        'completed_sections': _section_descriptor_list(completed_section_ids),
        'missing_sections': _section_descriptor_list(missing_section_ids),
        'next_required_section': _section_descriptor(next_section_id) if next_section_id else None,
        'retry_required': bool(retry_required),
        'retry_tool': 'review_final_markdown_write',
        'annotation_count': max(0, int(annotation_count or 0)),
        'required_annotation_count': max(1, int(required_annotation_count or 1)),
        'paper_search_usage': paper_search_usage if isinstance(paper_search_usage, dict) else {},
        'required_paper_search_calls': max(0, int(required_paper_search_calls or 0)),
    }
    if current_section_id:
        payload['current_section'] = _section_descriptor(current_section_id)
    if isinstance(next_steps, list) and next_steps:
        payload['next_steps'] = [str(item).strip() for item in next_steps if str(item).strip()]
    return payload


@dataclass
class ReviewRuntimeContext:
    job_id: str
    job_dir: Path
    page_index: dict[int, list[str]]
    source_markdown: str
    paper_adapter: PaperSearchAdapter
    settings: Settings

    annotations: list[AnnotationItem] = field(default_factory=list)
    final_markdown_text: str | None = None
    final_report_draft_sections: dict[str, str] = field(default_factory=dict)
    final_report_draft_version: int = 0

    tool_counts: dict[str, int] = field(default_factory=dict)
    paper_search_usage: PaperSearchUsage = field(default_factory=PaperSearchUsage)
    paper_search_signatures: set[str] = field(default_factory=set)

    status_updates: list[dict[str, Any]] = field(default_factory=list)

    def record_tool(self, name: str) -> None:
        self.tool_counts[name] = int(self.tool_counts.get(name, 0)) + 1

    @property
    def annotation_count(self) -> int:
        return len(self.annotations)

    def sync_state_usage(self, token_usage: Any | None = None) -> None:
        def apply(job):
            tool_counts = dict(self.tool_counts)
            job.usage.tool.per_tool = tool_counts
            job.usage.tool.total_calls = sum(int(v) for v in tool_counts.values())
            job.usage.tool.distinct_tools = len(tool_counts)
            job.usage.paper_search = self.paper_search_usage
            job.annotation_count = self.annotation_count
            if token_usage is not None:
                job.usage.token.requests = int(getattr(token_usage, 'requests', 0) or 0)
                job.usage.token.input_tokens = int(getattr(token_usage, 'input_tokens', 0) or 0)
                job.usage.token.output_tokens = int(getattr(token_usage, 'output_tokens', 0) or 0)
                job.usage.token.total_tokens = int(getattr(token_usage, 'total_tokens', 0) or 0)

        mutate_job_state(self.job_id, apply)

    def persist_annotations(self, token_usage: Any | None = None) -> None:
        payload = {
            'annotations': [item.model_dump(mode='json') for item in self.annotations],
            'count': len(self.annotations),
        }
        write_json_atomic(annotations_path(self.job_id), payload)
        self.sync_state_usage(token_usage)

    def set_final_markdown(self, markdown: str) -> None:
        final_path = self.job_dir / 'final_report.md'
        write_text_atomic(final_path, markdown)
        self.final_markdown_text = markdown

        def apply(job):
            job.final_report_ready = True
            job.artifacts.final_markdown_path = str(final_path)
            job.annotation_count = self.annotation_count

        mutate_job_state(self.job_id, apply)


def build_review_tools(runtime: ReviewRuntimeContext) -> list[Any]:
    @function_tool(strict_mode=False)
    async def mcp_status_update(
        ctx: RunContextWrapper[ReviewRuntimeContext],
        step: str,
        completed: str | None = None,
        blocked: str | None = None,
        todo: str | None = None,
    ) -> dict[str, Any]:
        rt = ctx.context
        rt.record_tool('mcp_status_update')

        row = {
            'step': str(step or '').strip(),
            'completed': str(completed or '').strip(),
            'blocked': str(blocked or '').strip(),
            'todo': str(todo or '').strip(),
        }
        rt.status_updates.append(row)

        def apply(job):
            job.metadata['last_status_update'] = row
            job.message = f"Agent progress: {row['step'] or 'updating'}"

        mutate_job_state(rt.job_id, apply)
        append_event(rt.job_id, 'agent_status_update', **row)
        rt.sync_state_usage(ctx.usage)
        return {'status': 'ok', 'status_update': row}

    @function_tool(strict_mode=False)
    async def pdf_search(
        ctx: RunContextWrapper[ReviewRuntimeContext],
        query: str,
        top_k: int = 8,
    ) -> dict[str, Any]:
        rt = ctx.context
        rt.record_tool('pdf_search')

        text = str(query or '').strip()
        if not text:
            rt.sync_state_usage(ctx.usage)
            return {'status': 'error', 'reason': 'empty_query', 'message': 'query is required'}

        tokens = [tok for tok in re.split(r'\s+', text.lower()) if tok]
        if not tokens:
            tokens = [text.lower()]

        rows = _flatten_page_index(rt.page_index)
        scored: list[tuple[int, int, int, str]] = []
        for page, line_no, line_text in rows:
            hay = line_text.lower()
            score = sum(hay.count(tok) for tok in tokens)
            if score <= 0 and text.lower() not in hay:
                continue
            if score <= 0:
                score = 1
            scored.append((score, page, line_no, line_text))

        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        hits = [
            {'page': p, 'line': ln, 'score': s, 'text': t}
            for s, p, ln, t in scored[: max(1, min(50, int(top_k or 8)))]
        ]

        rt.sync_state_usage(ctx.usage)
        return {'status': 'ok', 'query': text, 'count': len(hits), 'hits': hits}

    @function_tool(strict_mode=False)
    async def pdf_read_lines(
        ctx: RunContextWrapper[ReviewRuntimeContext],
        page: int,
        start_line: int,
        end_line: int,
    ) -> dict[str, Any]:
        rt = ctx.context
        rt.record_tool('pdf_read_lines')

        lines = rt.page_index.get(int(page))
        if not lines:
            rt.sync_state_usage(ctx.usage)
            return {'status': 'error', 'reason': 'page_not_found', 'message': f'Page {page} not found'}

        start = max(1, int(start_line))
        end = max(start, int(end_line))
        start_idx = start - 1
        end_idx = min(len(lines), end)

        snippet = lines[start_idx:end_idx]
        rt.sync_state_usage(ctx.usage)
        return {
            'status': 'ok',
            'page': int(page),
            'start_line': start,
            'end_line': end_idx,
            'lines': [{'line': start + i, 'text': line} for i, line in enumerate(snippet)],
            'text': '\n'.join(snippet),
        }

    @function_tool(strict_mode=False)
    async def pdf_jump(
        ctx: RunContextWrapper[ReviewRuntimeContext],
        page: int,
    ) -> dict[str, Any]:
        rt = ctx.context
        rt.record_tool('pdf_jump')

        lines = rt.page_index.get(int(page))
        if not lines:
            rt.sync_state_usage(ctx.usage)
            return {'status': 'error', 'reason': 'page_not_found', 'message': f'Page {page} not found'}

        preview = lines[:8]
        rt.sync_state_usage(ctx.usage)
        return {
            'status': 'ok',
            'page': int(page),
            'line_count': len(lines),
            'preview': preview,
        }

    @function_tool(strict_mode=False)
    async def pdf_annotate(
        ctx: RunContextWrapper[ReviewRuntimeContext],
        page: int,
        start_line: int,
        end_line: int,
        comment: str,
        summary: str | None = None,
        object_type: str = 'suggestion',
        severity: str | None = None,
    ) -> dict[str, Any]:
        rt = ctx.context
        rt.record_tool('pdf_annotate')
        required_search_calls = max(0, int(rt.settings.min_paper_search_calls_for_pdf_annotate))
        current_search_calls = max(0, int(rt.paper_search_usage.total_calls))
        paper_search_usage_payload = rt.paper_search_usage.model_dump()

        if current_search_calls < required_search_calls:
            rt.sync_state_usage(ctx.usage)
            return {
                'status': 'error',
                'reason': 'paper_search_calls_not_met',
                'message': (
                    f'Cannot start pdf_annotate yet: paper_search total calls={current_search_calls}, '
                    f'required >= {required_search_calls}.'
                ),
                'next_steps': [
                    f'Run paper_search until total calls reach {required_search_calls}+.',
                    'Then retry pdf_annotate on the same paragraph span.',
                ],
                'retry_required': True,
                'retry_tool': 'pdf_annotate',
                'paper_search_usage': paper_search_usage_payload,
                'required_paper_search_calls': required_search_calls,
            }

        lines = rt.page_index.get(int(page))
        if not lines:
            rt.sync_state_usage(ctx.usage)
            return {'status': 'error', 'reason': 'page_not_found', 'message': f'Page {page} not found'}

        start = max(1, int(start_line))
        end = max(start, int(end_line))
        start_idx = start - 1
        end_idx = min(len(lines), end)
        snippet = lines[start_idx:end_idx]

        text = '\n'.join(snippet).strip()
        if not text:
            rt.sync_state_usage(ctx.usage)
            return {
                'status': 'error',
                'reason': 'empty_span',
                'message': 'Selected span is empty; choose a valid line range.',
            }

        comment_text = str(comment or '').strip()
        if not comment_text:
            rt.sync_state_usage(ctx.usage)
            return {'status': 'error', 'reason': 'comment_required', 'message': 'comment is required'}

        ann = AnnotationItem(
            id=str(uuid4()),
            page=int(page),
            start_line=start,
            end_line=end_idx,
            text=text,
            comment=comment_text,
            summary=str(summary).strip() if summary else None,
            object_type=str(object_type or 'suggestion').strip() or 'suggestion',
            severity=str(severity).strip() if severity else None,
        )
        rt.annotations.append(ann)
        rt.persist_annotations(ctx.usage)

        append_event(
            rt.job_id,
            'annotation_created',
            annotation_id=ann.id,
            page=ann.page,
            start_line=ann.start_line,
            end_line=ann.end_line,
            object_type=ann.object_type,
            severity=ann.severity,
        )

        total_annotations = int(rt.annotation_count)
        required_annotations = max(1, int(rt.settings.min_annotations_for_final))
        progress_hint = _build_annotation_progress_hint(
            total_annotations=total_annotations,
            final_trigger_min=required_annotations,
            recommended_min=_REVIEW_RECOMMENDED_ANNOTATION_MIN,
            recommended_max=_REVIEW_RECOMMENDED_ANNOTATION_MAX,
        )
        can_start_final = total_annotations >= required_annotations
        pdf_annotate_usage = {
            'total_calls': total_annotations,
            'hard_minimum_calls_for_final_report': required_annotations,
            'hard_minimum_met': can_start_final,
            'can_start_final_consolidation': can_start_final,
            'note': (
                'Hard minimum is not sufficient by itself. '
                'Use page-level coverage and quality judgment before final submission.'
            ),
        }
        if can_start_final:
            pdf_annotate_hint = (
                f'pdf_annotate total calls so far: {total_annotations} (hard minimum >= {required_annotations} is met). '
                'Do not decide final submission by count alone; self-check coverage and then decide.'
            )
        else:
            remaining_calls = required_annotations - total_annotations
            pdf_annotate_hint = (
                f'pdf_annotate total calls so far: {total_annotations}. '
                f'Continue step-by-step annotation for at least {remaining_calls} more call(s). '
                f'After reaching the hard minimum, still self-check coverage before final submission.'
            )

        annotation_progress = {
            'total_review_annotations': total_annotations,
            'recommended_total_range': {
                'min': _REVIEW_RECOMMENDED_ANNOTATION_MIN,
                'max': _REVIEW_RECOMMENDED_ANNOTATION_MAX,
            },
            'ready_for_final_report': can_start_final,
            'final_report_trigger_min': required_annotations,
        }
        success_message = (
            'Tool call succeeded: PDF annotation has been saved. '
            f'{progress_hint} {pdf_annotate_hint}'
        )

        return {
            'status': 'ok',
            'success': True,
            'annotation_id': ann.id,
            'annotation_count': total_annotations,
            'message': success_message,
            'created_message': f'Created highlight on page {page}',
            'progress_hint': progress_hint,
            'pdf_annotate_hint': pdf_annotate_hint,
            'annotation_progress': annotation_progress,
            'pdf_annotate_usage': pdf_annotate_usage,
            'next_action': 'self_check_annotation_coverage_then_decide',
            'completion_gate': {
                'recommended_total_annotations': (
                    f'{_REVIEW_RECOMMENDED_ANNOTATION_MIN}-{_REVIEW_RECOMMENDED_ANNOTATION_MAX}'
                ),
                'hard_minimum_final_report_annotations': required_annotations,
                'main_body_annotations_per_page': '1-4',
                'appendix_annotations_per_two_pages': '1',
                'final_tool': 'review_final_markdown_write',
                'ready_for_final_report': can_start_final,
            },
            'object_type': ann.object_type,
            'severity': ann.severity,
            'summary': ann.summary,
            'paper_search_usage': paper_search_usage_payload,
        }

    @function_tool(strict_mode=False)
    async def paper_search(
        ctx: RunContextWrapper[ReviewRuntimeContext],
        query: str | None = None,
        question_list: list[str] | str | None = None,
    ) -> dict[str, Any]:
        rt = ctx.context
        rt.record_tool('paper_search')

        questions = normalize_question_list(question_list)
        query_text = str(query or '').strip()
        if query_text:
            rt.paper_search_signatures.add(_normalize_signature(query_text))
        for item in questions:
            rt.paper_search_signatures.add(_normalize_signature(item))

        try:
            result = await rt.paper_adapter.search(
                query=query_text if query_text else None,
                question_list=questions or None,
            )
        except Exception as exc:
            result = {
                'success': False,
                'reason': 'paper_search_request_failed',
                'error': f'{type(exc).__name__}: {exc}',
                'message': 'Paper search request failed before a valid response was returned.',
                'query': query_text,
                'questions': questions,
                'papers': [],
                'count': 0,
                'question_results': [],
                'next_steps': [
                    'Check paper search BASE_URL/ENDPOINT and API key settings.',
                    'Retry paper_search with the same query once the backend is reachable.',
                ],
                'retry_required': True,
                'retry_tool': 'paper_search',
            }

        rt.paper_search_usage.total_calls += 1
        if bool(result.get('success')):
            rt.paper_search_usage.successful_calls += 1

        paper_count = _count_papers(result)
        if bool(result.get('success')) and paper_count > 0:
            rt.paper_search_usage.effective_calls += 1
            rt.paper_search_usage.papers_found += paper_count

        discovered = result.get('questions')
        if isinstance(discovered, list):
            for q in discovered:
                rt.paper_search_signatures.add(_normalize_signature(str(q)))

        grouped = result.get('question_results')
        if isinstance(grouped, list):
            for row in grouped:
                if not isinstance(row, dict):
                    continue
                q = str(row.get('question') or row.get('query') or '').strip()
                if q:
                    rt.paper_search_signatures.add(_normalize_signature(q))

        rt.paper_search_usage.distinct_queries = len({s for s in rt.paper_search_signatures if s})

        rt.sync_state_usage(ctx.usage)
        append_event(
            rt.job_id,
            'paper_search_called',
            query=query_text,
            questions=questions,
            success=bool(result.get('success')),
            count=paper_count,
            distinct_queries=rt.paper_search_usage.distinct_queries,
            reason=str(result.get('reason') or '').strip() or None,
            message=str(result.get('message') or '').strip() or None,
        )

        result_payload = dict(result)
        usage_payload = rt.paper_search_usage.model_dump()
        required_for_annotate = max(0, int(rt.settings.min_paper_search_calls_for_pdf_annotate))
        total_calls = max(0, int(rt.paper_search_usage.total_calls))
        can_start_pdf_annotate = total_calls >= required_for_annotate
        annotation_gate_hint = _build_annotation_gate_hint(
            total_calls=total_calls,
            required_calls=required_for_annotate,
        )
        result_payload['paper_search_usage'] = usage_payload
        result_payload['required_paper_search_calls_for_pdf_annotate'] = required_for_annotate
        result_payload['can_start_pdf_annotate'] = can_start_pdf_annotate
        result_payload['annotation_gate_hint'] = annotation_gate_hint
        existing_message = str(result_payload.get('message') or '').strip()
        if bool(result_payload.get('success')):
            result_payload['message'] = annotation_gate_hint
        elif not existing_message:
            result_payload['message'] = annotation_gate_hint
        result_payload['next_action'] = (
            'start_pdf_annotate' if can_start_pdf_annotate else 'continue_paper_search'
        )
        result_payload['next_steps'] = (
            [
                'Start paragraph-by-paragraph annotation with `pdf_search -> pdf_read_lines -> pdf_annotate`.',
                (
                    'Before each next annotation, perform detailed reasoning on the target text: '
                    'confirm the issue is real and confirm it has a concrete fix path.'
                ),
                'Create at least 10 section/paragraph annotations; usual target range is 12-25 before final report submission.',
            ]
            if can_start_pdf_annotate
            else [
                f'Run more paper_search calls until total calls reach {required_for_annotate}+.',
                (
                    'After retrieval threshold is met, start step-by-step PDF annotations and '
                    'for each next annotation first verify issue reality and fixability.'
                ),
            ]
        )
        return result_payload

    @function_tool(strict_mode=False)
    async def read_paper(
        ctx: RunContextWrapper[ReviewRuntimeContext],
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        rt = ctx.context
        rt.record_tool('read_paper')

        rows = [row for row in (items or []) if isinstance(row, dict)]
        if not rows:
            rt.sync_state_usage(ctx.usage)
            return {'status': 'error', 'reason': 'empty_items', 'message': 'items is required'}

        result = await rt.paper_adapter.read_papers(items=rows)
        rt.sync_state_usage(ctx.usage)
        append_event(rt.job_id, 'read_paper_called', item_count=len(rows), success=bool(result.get('success')))
        return result

    @function_tool(strict_mode=False)
    async def question_prompt(
        ctx: RunContextWrapper[ReviewRuntimeContext],
        question: str,
        options: list[str] | None = None,
    ) -> dict[str, Any]:
        rt = ctx.context
        rt.record_tool('question_prompt')
        rt.sync_state_usage(ctx.usage)
        return {
            'status': 'not_available',
            'message': 'No interactive question channel is available in this CLI backend mode.',
            'question': str(question or '').strip(),
            'options': [str(x).strip() for x in options or [] if str(x).strip()],
        }

    @function_tool(strict_mode=False)
    async def review_final_markdown_write(
        ctx: RunContextWrapper[ReviewRuntimeContext],
        markdown: str | None = None,
        summary: str | None = None,
        strengths: list[str] | str | None = None,
        weaknesses: list[str] | str | None = None,
        issues: list[str] | str | None = None,
        suggestions: list[str] | str | None = None,
        storylines: list[str] | str | None = None,
        section_id: str | None = None,
        section_title: str | None = None,
        section_content: list[str] | str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        rt = ctx.context
        rt.record_tool('review_final_markdown_write')
        attempt_no = int(rt.tool_counts.get('review_final_markdown_write', 0))
        usage = rt.paper_search_usage
        section_order = _required_final_report_section_order()
        enforce_final_gates = bool(rt.settings.enable_final_gates)
        required_paper_calls = max(0, int(rt.settings.min_paper_search_calls_for_final))
        required_distinct_queries = max(0, int(rt.settings.min_distinct_paper_queries_for_final))
        required_annotations = max(1, int(rt.settings.min_annotations_for_final))
        normalized_source = str(source or '').strip() or 'review_annotation_agent'

        # Hard short-circuit: once final report is persisted, do not allow repeated rewrite loops.
        if rt.final_markdown_text:
            completed_section_ids = [
                section_key
                for section_key in section_order
                if str(rt.final_report_draft_sections.get(section_key) or '').strip()
            ]
            rt.sync_state_usage(ctx.usage)
            return {
                'status': 'ok',
                'task_completed': True,
                'final_report_persisted': True,
                'message': (
                    'Final report is already persisted. '
                    'Stop calling review_final_markdown_write and end this run now.'
                ),
                'annotation_count': rt.annotation_count,
                'paper_search_usage': usage.model_dump(),
                'required_paper_search_calls': required_paper_calls,
                'completed_sections': _section_descriptor_list(completed_section_ids),
                'missing_sections': [],
                'next_required_section': None,
            }

        def _return_final_write_failure(payload: dict[str, Any]) -> dict[str, Any]:
            rt.sync_state_usage(ctx.usage)
            append_event(
                rt.job_id,
                'final_report_write_failed',
                attempt=attempt_no,
                reason=str(payload.get('reason') or '').strip(),
                message=str(payload.get('message') or '').strip(),
                annotation_count=rt.annotation_count,
                paper_search_usage=usage.model_dump(),
                missing_sections=payload.get('missing_sections'),
                language=payload.get('language'),
                english_words=payload.get('english_words'),
                chinese_chars=payload.get('chinese_chars'),
            )
            return payload

        raw_markdown_input = _coerce_markdown_text(markdown)
        incoming_sections = _build_sections_from_legacy_fields(
            summary=summary,
            strengths=strengths,
            weaknesses=weaknesses,
            issues=issues,
            suggestions=suggestions,
            storylines=storylines,
        )
        markdown_sections = _extract_required_sections_from_markdown(raw_markdown_input)
        if markdown_sections:
            incoming_sections.update(markdown_sections)
        elif raw_markdown_input and not incoming_sections:
            # Backward compatible fallback for a single raw markdown chunk.
            incoming_sections['summary'] = raw_markdown_input

        requested_section_id = _resolve_final_report_section_id(section_id or section_title)
        if (section_id or section_title) and not requested_section_id:
            return _return_final_write_failure(
                {
                    'status': 'error',
                    'reason': 'section_id_invalid',
                    'message': (
                        'Unknown section_id/section_title. Use one required section id: '
                        'summary, strengths, weaknesses, key_issues, actionable_suggestions, '
                        'storyline_options_writing_outlines, priority_revision_plan, '
                        'experiment_inventory_research_experiment_plan, novelty_verification_related_work_matrix, '
                        'references, scores.'
                    ),
                    'retry_required': True,
                    'retry_tool': 'review_final_markdown_write',
                }
            )
        if requested_section_id and section_content is None and requested_section_id not in incoming_sections:
            section_name = _required_final_report_section_titles().get(requested_section_id, requested_section_id)
            return _return_final_write_failure(
                {
                    'status': 'error',
                    'reason': 'section_content_required',
                    'message': f"Section '{section_name}' was selected but section_content is empty.",
                    'retry_required': True,
                    'retry_tool': 'review_final_markdown_write',
                    'retry_payload_hint': {
                        'section_id': requested_section_id,
                        'section_content': 'Write this section in markdown.',
                    },
                }
            )
        if requested_section_id and section_content is not None:
            requested_content = _coerce_section_markdown(section_content, list_as_bullets=True).strip()
            requested_content = _strip_leading_section_heading(
                section_id=requested_section_id,
                content=requested_content,
            )
            if not requested_content:
                section_name = _required_final_report_section_titles().get(requested_section_id, requested_section_id)
                return _return_final_write_failure(
                    {
                        'status': 'error',
                        'reason': 'section_content_required',
                        'message': f"Section '{section_name}' has empty content after normalization.",
                        'retry_required': True,
                        'retry_tool': 'review_final_markdown_write',
                    }
                )
            incoming_sections[requested_section_id] = requested_content

        draft_sections = _normalize_final_report_sections(rt.final_report_draft_sections)
        has_new_sections = bool(incoming_sections)
        if has_new_sections:
            draft_sections.update(incoming_sections)

        if not draft_sections:
            return _return_final_write_failure(
                {
                    'status': 'error',
                    'reason': 'section_payload_required',
                    'message': 'No report section content was provided and no draft exists.',
                    'next_steps': [
                        'Submit one section with section_id + section_content.',
                        'Follow required section order until the tool returns status=ok.',
                    ],
                    'retry_required': True,
                    'retry_tool': 'review_final_markdown_write',
                    'retry_payload_hint': {
                        'section_id': 'summary',
                        'section_content': 'Write the summary section in markdown.',
                    },
                }
            )

        rt.final_report_draft_sections = draft_sections
        if has_new_sections:
            rt.final_report_draft_version += 1
        rt.final_report_draft_version = max(1, int(rt.final_report_draft_version or 0))
        draft_version = int(rt.final_report_draft_version)

        completed_section_ids = [
            section_key for section_key in section_order if str(draft_sections.get(section_key) or '').strip()
        ]
        missing_section_ids = [
            section_key for section_key in section_order if section_key not in completed_section_ids
        ]

        current_section_id = requested_section_id
        if not current_section_id and has_new_sections:
            for candidate_section_id in section_order:
                if candidate_section_id in incoming_sections:
                    current_section_id = candidate_section_id
                    break

        if missing_section_ids:
            progress_message = (
                'Section draft saved. '
                f'{len(missing_section_ids)} required section(s) are still missing. '
                f"Next required section: {_required_final_report_section_titles().get(missing_section_ids[0], missing_section_ids[0])}."
            )
            progress_payload = _build_final_report_progress_payload(
                source=normalized_source,
                completed_section_ids=completed_section_ids,
                missing_section_ids=missing_section_ids,
                annotation_count=rt.annotation_count,
                paper_search_usage=usage.model_dump(),
                required_paper_search_calls=required_paper_calls,
                required_annotation_count=required_annotations,
                draft_version=draft_version,
                status='partial',
                reason='required_sections_missing',
                message=progress_message,
                retry_required=True,
                next_steps=[
                    (
                        "Submit the next section with review_final_markdown_write("
                        "section_id='<next>', section_content='<markdown>')."
                    ),
                    (
                        'Missing sections: '
                        + ', '.join(
                            _required_final_report_section_titles().get(section_key, section_key)
                            for section_key in missing_section_ids
                        )
                        + '.'
                    ),
                ],
                current_section_id=current_section_id,
            )
            rt.sync_state_usage(ctx.usage)
            append_event(
                rt.job_id,
                'final_report_draft_saved',
                attempt=attempt_no,
                source=normalized_source,
                completed_sections=completed_section_ids,
                missing_sections=missing_section_ids,
                next_required_section=missing_section_ids[0],
                draft_version=draft_version,
            )
            return progress_payload

        if enforce_final_gates and usage.total_calls < required_paper_calls:
            return _return_final_write_failure(
                _build_final_report_progress_payload(
                    source=normalized_source,
                    completed_section_ids=completed_section_ids,
                    missing_section_ids=missing_section_ids,
                    annotation_count=rt.annotation_count,
                    paper_search_usage=usage.model_dump(),
                    required_paper_search_calls=required_paper_calls,
                    required_annotation_count=required_annotations,
                    draft_version=draft_version,
                    status='error',
                    reason='paper_search_calls_not_met',
                    message=(
                        'Insufficient paper_search usage before final submission: '
                        f'{usage.total_calls} call(s) found, {required_paper_calls}+ required.'
                    ),
                    retry_required=True,
                    next_steps=[
                        f'Run paper_search until total calls reach at least {required_paper_calls}.',
                        'After retrieval and comparison are complete, re-call review_final_markdown_write.',
                    ],
                    current_section_id=current_section_id,
                )
            )

        if enforce_final_gates and usage.distinct_queries < required_distinct_queries:
            return _return_final_write_failure(
                _build_final_report_progress_payload(
                    source=normalized_source,
                    completed_section_ids=completed_section_ids,
                    missing_section_ids=missing_section_ids,
                    annotation_count=rt.annotation_count,
                    paper_search_usage=usage.model_dump(),
                    required_paper_search_calls=required_paper_calls,
                    required_annotation_count=required_annotations,
                    draft_version=draft_version,
                    status='error',
                    reason='paper_search_distinct_queries_not_met',
                    message=(
                        'Insufficient distinct paper_search coverage before final submission: '
                        f'{usage.distinct_queries} distinct query/question(s) found, '
                        f'{required_distinct_queries}+ required.'
                    ),
                    retry_required=True,
                    next_steps=[
                        (
                            'Add non-duplicate paper_search queries/questions until distinct coverage '
                            f'reaches {required_distinct_queries}.'
                        ),
                        (
                            'Then re-call review_final_markdown_write after updating novelty and '
                            'contribution judgment.'
                        ),
                    ],
                    current_section_id=current_section_id,
                )
            )

        if enforce_final_gates and rt.annotation_count < required_annotations:
            return _return_final_write_failure(
                _build_final_report_progress_payload(
                    source=normalized_source,
                    completed_section_ids=completed_section_ids,
                    missing_section_ids=missing_section_ids,
                    annotation_count=rt.annotation_count,
                    paper_search_usage=usage.model_dump(),
                    required_paper_search_calls=required_paper_calls,
                    required_annotation_count=required_annotations,
                    draft_version=draft_version,
                    status='error',
                    reason='annotation_count_not_met',
                    message=(
                        f'PDF annotation count is too low: {rt.annotation_count} found, '
                        f'minimum {required_annotations} required.'
                    ),
                    retry_required=True,
                    next_steps=[
                        f'Add annotations until count reaches at least {required_annotations}.',
                        'Then re-call review_final_markdown_write to finalize the saved section draft.',
                    ],
                    current_section_id=current_section_id,
                )
            )

        markdown_text = _build_final_report_markdown_from_sections(draft_sections)
        if not markdown_text:
            return _return_final_write_failure(
                {
                    'status': 'error',
                    'reason': 'markdown_required',
                    'message': 'Required sections are present but final markdown assembly is empty.',
                    'retry_required': True,
                    'retry_tool': 'review_final_markdown_write',
                }
            )

        validation = validate_final_report(
            markdown=markdown_text,
            min_english_words=rt.settings.min_english_words_for_final,
            min_chinese_chars=rt.settings.min_chinese_chars_for_final,
            force_english_output=rt.settings.force_english_output,
        )
        if not validation.ok and enforce_final_gates:
            return _return_final_write_failure(
                _build_final_report_progress_payload(
                    source=normalized_source,
                    completed_section_ids=completed_section_ids,
                    missing_section_ids=missing_section_ids,
                    annotation_count=rt.annotation_count,
                    paper_search_usage=usage.model_dump(),
                    required_paper_search_calls=required_paper_calls,
                    required_annotation_count=required_annotations,
                    draft_version=draft_version,
                    status='error',
                    reason=str(validation.reason or 'final_report_validation_failed'),
                    message=str(validation.message or 'Final report validation failed.'),
                    retry_required=True,
                    next_steps=[
                        'Update final markdown quality and structure according to tool error.',
                        'Re-call review_final_markdown_write after remediation.',
                    ],
                    current_section_id=current_section_id,
                )
            )
        if not validation.ok and not enforce_final_gates:
            append_event(
                rt.job_id,
                'final_report_validation_skipped',
                reason=validation.reason,
                message=validation.message,
                missing_sections=validation.missing_sections,
                language=validation.language_stats.primary_language,
                english_words=validation.language_stats.english_words,
                chinese_chars=validation.language_stats.chinese_chars,
                final_gates_enforced=False,
            )

        rt.set_final_markdown(markdown_text)
        rt.sync_state_usage(ctx.usage)

        append_event(
            rt.job_id,
            'final_report_persisted',
            source=normalized_source,
            annotation_count=rt.annotation_count,
            paper_search_usage=usage.model_dump(),
            completed_sections=completed_section_ids,
            draft_version=draft_version,
        )

        def apply(job):
            job.message = 'Final report persisted successfully.'
            metadata = dict(job.metadata)
            metadata['final_report_source'] = normalized_source
            metadata['status_updates_count'] = len(rt.status_updates)
            metadata['final_report_sections_completed'] = completed_section_ids
            metadata['final_report_draft_version'] = draft_version
            metadata['final_report_draft'] = {
                'sections': dict(draft_sections),
                'section_order': section_order,
                'completed_sections': completed_section_ids,
                'missing_sections': [],
                'next_required_section': None,
                'status': 'completed',
                'source': normalized_source,
            }
            job.metadata = metadata

        mutate_job_state(rt.job_id, apply)

        return {
            'status': 'ok',
            'task_completed': True,
            'final_report_persisted': True,
            'auto_composed_from_sections': True,
            'message': 'Final report persisted successfully. End execution now.',
            'annotation_count': rt.annotation_count,
            'paper_search_usage': usage.model_dump(),
            'required_paper_search_calls': required_paper_calls,
            'source': normalized_source,
            'draft_version': draft_version,
            'completed_sections': _section_descriptor_list(completed_section_ids),
            'missing_sections': [],
            'next_required_section': None,
            'language': validation.language_stats.primary_language,
            'english_words': validation.language_stats.english_words,
            'chinese_chars': validation.language_stats.chinese_chars,
            'final_gates_enforced': enforce_final_gates,
        }

    return [
        mcp_status_update,
        pdf_search,
        pdf_read_lines,
        pdf_jump,
        pdf_annotate,
        paper_search,
        read_paper,
        question_prompt,
        review_final_markdown_write,
    ]
