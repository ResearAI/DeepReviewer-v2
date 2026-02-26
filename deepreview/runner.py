from __future__ import annotations

import asyncio
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agents import Agent, ModelSettings, OpenAIProvider, RunConfig, Runner
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI
from openai.types.shared import Reasoning

from deepreview.adapters.markdown_parser import build_page_index
from deepreview.adapters.mineru import MineruAdapter, MineruConfig
from deepreview.adapters.paper_search import PaperReadConfig, PaperSearchAdapter, PaperSearchConfig
from deepreview.config import get_settings
from deepreview.prompts.review_agent_prompt import build_review_agent_system_prompt
from deepreview.report.review_report_pdf import build_review_report_pdf
from deepreview.report.source_annotations import build_source_annotations_for_export
from deepreview.state import ensure_artifact_paths, fail_job, load_job_state, mutate_job_state, set_status
from deepreview.storage import append_event, read_json, write_json_atomic, write_text_atomic
from deepreview.tools.review_tools import ReviewRuntimeContext, build_review_tools
from deepreview.types import AnnotationItem, JobStatus


def _resolved_api_key() -> str:
    settings = get_settings()
    return str(settings.openai_api_key or 'EMPTY')


def _build_mineru_adapter() -> MineruAdapter:
    settings = get_settings()
    return MineruAdapter(
        MineruConfig(
            base_url=settings.mineru_base_url,
            api_token=settings.mineru_api_token,
            model_version=settings.mineru_model_version,
            upload_endpoint=settings.mineru_upload_endpoint,
            poll_endpoint_templates=settings.mineru_poll_templates(),
            poll_interval_seconds=settings.mineru_poll_interval_seconds,
            poll_timeout_seconds=settings.mineru_poll_timeout_seconds,
            allow_local_fallback=settings.mineru_allow_local_fallback,
        )
    )


def _build_paper_adapter() -> PaperSearchAdapter:
    settings = get_settings()
    return PaperSearchAdapter(
        search_cfg=PaperSearchConfig(
            base_url=settings.paper_search_base_url,
            api_key=settings.paper_search_api_key,
            endpoint=settings.paper_search_endpoint,
            timeout_seconds=settings.paper_search_timeout_seconds,
        ),
        read_cfg=PaperReadConfig(
            base_url=settings.paper_read_base_url,
            api_key=settings.paper_read_api_key,
            endpoint=settings.paper_read_endpoint,
            timeout_seconds=settings.paper_read_timeout_seconds,
        ),
    )


def _build_run_config() -> RunConfig:
    settings = get_settings()
    provider = OpenAIProvider(
        api_key=_resolved_api_key(),
        base_url=settings.openai_base_url,
        use_responses=settings.openai_use_responses_api,
    )
    return RunConfig(model_provider=provider)


def _build_agent_model() -> OpenAIChatCompletionsModel | OpenAIResponsesModel:
    settings = get_settings()
    client = AsyncOpenAI(
        api_key=_resolved_api_key(),
        base_url=settings.openai_base_url,
    )
    if settings.openai_use_responses_api:
        return OpenAIResponsesModel(
            model=settings.agent_model,
            openai_client=client,
        )
    return OpenAIChatCompletionsModel(
        model=settings.agent_model,
        openai_client=client,
    )


def _build_agent_model_settings(*, tool_choice: str | None = None) -> ModelSettings:
    settings = get_settings()
    model_name = str(settings.agent_model or '').strip().lower()
    use_xhigh_reasoning = model_name in {'gpt-5.3', 'gpt-5.2'}

    return ModelSettings(
        temperature=settings.agent_temperature,
        max_tokens=settings.agent_max_tokens,
        tool_choice=tool_choice,
        reasoning=Reasoning(effort='xhigh') if use_xhigh_reasoning else None,
    )


def _sync_token_usage(job_id: str, usage: Any) -> None:
    requests = int(getattr(usage, 'requests', 0) or 0)
    input_tokens = int(getattr(usage, 'input_tokens', 0) or 0)
    output_tokens = int(getattr(usage, 'output_tokens', 0) or 0)
    total_tokens = int(getattr(usage, 'total_tokens', 0) or 0)

    def apply(job):
        job.usage.token.requests = requests
        job.usage.token.input_tokens = input_tokens
        job.usage.token.output_tokens = output_tokens
        job.usage.token.total_tokens = total_tokens

    mutate_job_state(job_id, apply)


def _coerce_dict_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _load_content_list(path: Path | None) -> list[dict[str, Any]] | None:
    if path is None or not path.exists():
        return None
    try:
        payload = read_json(path)
    except Exception:
        return None

    if isinstance(payload, dict):
        rows = payload.get('content_list')
        extracted = _coerce_dict_rows(rows)
        return extracted or None
    extracted = _coerce_dict_rows(payload)
    return extracted or None


def _load_annotations_payload(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    try:
        payload = read_json(path)
    except Exception:
        return []

    if isinstance(payload, dict):
        return _coerce_dict_rows(payload.get('annotations'))
    return _coerce_dict_rows(payload)


def _token_usage_payload_from_state(state: Any) -> dict[str, int]:
    usage = getattr(state, 'usage', None)
    token = getattr(usage, 'token', None)
    return {
        'requests': int(getattr(token, 'requests', 0) or 0),
        'input_tokens': int(getattr(token, 'input_tokens', 0) or 0),
        'output_tokens': int(getattr(token, 'output_tokens', 0) or 0),
        'total_tokens': int(getattr(token, 'total_tokens', 0) or 0),
    }


def _render_report_pdf(
    *,
    job_id: str,
    job_title: str,
    source_pdf_name: str,
    final_md_path: Path,
    source_pdf_path: Path,
    report_pdf_path: Path,
    annotations: list[AnnotationItem] | list[dict[str, Any]],
    content_list: list[dict[str, Any]] | None,
    token_usage: dict[str, int],
    agent_model: str,
) -> dict[str, int]:
    final_report_markdown = final_md_path.read_text(encoding='utf-8')
    source_pdf_bytes = source_pdf_path.read_bytes() if source_pdf_path.exists() else None
    source_annotations = build_source_annotations_for_export(
        annotations=annotations,
        content_list=content_list,
    )

    report_pdf_bytes = build_review_report_pdf(
        workspace_title=job_title,
        source_pdf_name=source_pdf_name,
        run_id=job_id,
        status='completed',
        decision=None,
        estimated_cost=0,
        actual_cost=None,
        exported_at=datetime.now(timezone.utc),
        meta_review={},
        reviewers=[],
        raw_output=None,
        final_report_markdown=final_report_markdown,
        source_pdf_bytes=source_pdf_bytes,
        source_annotations=source_annotations,
        review_display_id=None,
        owner_email=None,
        token_usage=token_usage,
        agent_model=agent_model,
    )
    report_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    report_pdf_path.write_bytes(report_pdf_bytes)

    export_stats = {
        'source_annotations_input_count': int(len(annotations)),
        'source_annotations_exported_count': int(len(source_annotations)),
        'content_list_count': int(len(content_list or [])),
        'report_pdf_size_bytes': int(len(report_pdf_bytes)),
    }
    append_event(job_id, 'pdf_export_rendered', **export_stats)
    return export_stats


def _complete_with_existing_final_report(job_id: str, *, warning: str) -> bool:
    state = load_job_state(job_id)
    if state is None:
        return False

    artifacts = ensure_artifact_paths(job_id)
    final_md_path = Path(state.artifacts.final_markdown_path or artifacts['final_markdown'])
    if not final_md_path.exists():
        return False

    metadata = state.metadata if isinstance(state.metadata, dict) else {}
    has_persist_marker = bool(
        state.final_report_ready
        or str(state.artifacts.final_markdown_path or '').strip()
        or str(metadata.get('final_report_source') or '').strip()
    )
    if not has_persist_marker:
        append_event(
            job_id,
            'completed_recovery_skipped',
            warning=warning,
            reason='final_markdown_exists_without_persist_marker',
        )
        return False

    report_pdf_path = Path(state.artifacts.report_pdf_path or artifacts['report_pdf'])
    pdf_error: str | None = None
    if not report_pdf_path.exists():
        try:
            source_pdf_path = Path(state.artifacts.source_pdf_path or artifacts['source_pdf'])
            annotations_path = Path(state.artifacts.annotations_path or artifacts['annotations'])
            content_list_path = Path(state.artifacts.mineru_content_list_path or artifacts['mineru_content_list'])
            annotations = _load_annotations_payload(annotations_path)
            content_list = _load_content_list(content_list_path)
            _render_report_pdf(
                job_id=job_id,
                job_title=state.title,
                source_pdf_name=state.source_pdf_name,
                final_md_path=final_md_path,
                source_pdf_path=source_pdf_path,
                report_pdf_path=report_pdf_path,
                annotations=annotations,
                content_list=content_list,
                token_usage=_token_usage_payload_from_state(state),
                agent_model=str(get_settings().agent_model or '').strip(),
            )
        except Exception as exc:
            pdf_error = f'{type(exc).__name__}: {exc}'

    def apply_completed(state_obj):
        state_obj.status = JobStatus.completed
        state_obj.final_report_ready = True
        state_obj.pdf_ready = report_pdf_path.exists()
        state_obj.artifacts.final_markdown_path = str(final_md_path)
        state_obj.artifacts.report_pdf_path = str(report_pdf_path) if report_pdf_path.exists() else None
        state_obj.error = pdf_error
        state_obj.message = (
            'Review pipeline completed via recovery after post-write exception.'
            if pdf_error is None
            else 'Final report persisted, but PDF export failed during recovery.'
        )
        metadata = dict(state_obj.metadata)
        metadata['post_exception_recovery'] = True
        metadata['post_exception_warning'] = warning
        if pdf_error:
            metadata['pdf_export_recovery_error'] = pdf_error
        state_obj.metadata = metadata

    mutate_job_state(job_id, apply_completed)
    append_event(
        job_id,
        'completed_recovered',
        warning=warning,
        pdf_ready=report_pdf_path.exists(),
        pdf_error=pdf_error,
    )
    return True


async def run_job_async(job_id: str) -> None:
    settings = get_settings()
    job = load_job_state(job_id)
    if job is None:
        raise FileNotFoundError(f'Job not found: {job_id}')

    api_mode = 'responses' if settings.openai_use_responses_api else 'chat_completions'
    append_event(
        job_id,
        'llm_api_mode_selected',
        api_mode=api_mode,
        model=str(settings.agent_model or '').strip(),
    )

    def apply_llm_mode(state):
        metadata = dict(state.metadata)
        metadata['llm_api_mode'] = api_mode
        state.metadata = metadata

    mutate_job_state(job_id, apply_llm_mode)

    artifacts = ensure_artifact_paths(job_id)
    source_pdf = Path(artifacts['source_pdf'])
    if not source_pdf.exists():
        raise RuntimeError(f'Source PDF missing: {source_pdf}')
    file_size = int(source_pdf.stat().st_size)
    if file_size <= 0:
        raise RuntimeError('Source PDF is empty.')
    if file_size > int(settings.max_pdf_bytes):
        raise RuntimeError(
            f'Source PDF too large: {file_size} bytes, max allowed {int(settings.max_pdf_bytes)} bytes.'
        )

    set_status(job_id, JobStatus.pdf_uploading_to_mineru, 'Submitting PDF to MinerU and uploading file...')
    set_status(job_id, JobStatus.pdf_parsing, 'Polling MinerU parse result and assembling markdown...')

    mineru = _build_mineru_adapter()
    parse_result = await mineru.parse_pdf(pdf_path=source_pdf, data_id=job_id)

    write_text_atomic(Path(artifacts['mineru_markdown']), parse_result.markdown)
    if parse_result.content_list is not None:
        write_json_atomic(Path(artifacts['mineru_content_list']), {'content_list': parse_result.content_list})
    if parse_result.raw_result is not None:
        write_json_atomic(Path(artifacts['raw_result']), parse_result.raw_result)

    def apply_parsed(state):
        state.artifacts.mineru_markdown_path = str(artifacts['mineru_markdown'])
        state.artifacts.mineru_content_list_path = (
            str(artifacts['mineru_content_list']) if Path(artifacts['mineru_content_list']).exists() else None
        )
        state.artifacts.annotations_path = str(artifacts['annotations'])
        state.metadata['markdown_provider'] = parse_result.provider
        state.metadata['mineru_batch_id'] = parse_result.batch_id
        state.metadata['parse_warning'] = parse_result.warning

    mutate_job_state(job_id, apply_parsed)
    if parse_result.warning:
        append_event(job_id, 'markdown_parse_warning', warning=parse_result.warning, provider=parse_result.provider)

    page_index = build_page_index(parse_result.markdown, parse_result.content_list)

    set_status(job_id, JobStatus.agent_running, 'Running review agent with tool loop...')

    prompt = build_review_agent_system_prompt(
        source_file_id=job_id,
        source_file_name=job.source_pdf_name,
        ui_language=settings.ui_language,
        paper_markdown=parse_result.markdown,
        use_meta_review=False,
    )
    write_text_atomic(Path(artifacts['prompt_snapshot']), prompt)

    def apply_prompt(state):
        state.artifacts.prompt_snapshot_path = str(artifacts['prompt_snapshot'])

    mutate_job_state(job_id, apply_prompt)

    runtime = ReviewRuntimeContext(
        job_id=job_id,
        job_dir=Path(artifacts['source_pdf']).parent,
        page_index=page_index,
        source_markdown=parse_result.markdown,
        paper_adapter=_build_paper_adapter(),
        settings=settings,
    )

    tools = build_review_tools(runtime)
    agent_model = _build_agent_model()
    agent = Agent(
        name='DeepReviewer2Agent',
        instructions=prompt,
        tools=tools,
        model=agent_model,
        model_settings=_build_agent_model_settings(),
    )

    requested_attempts = int(settings.agent_resume_attempts)
    max_attempts = max(1, min(2, requested_attempts))
    if requested_attempts != max_attempts:
        append_event(
            job_id,
            'agent_resume_attempts_capped',
            requested=requested_attempts,
            applied=max_attempts,
            reason='hard_cap_2',
        )
    run_config = _build_run_config()
    # Use the exact same full review prompt as user input (parity requirement).
    next_input: str | list[Any] = prompt
    usage_totals = {
        'requests': 0,
        'input_tokens': 0,
        'output_tokens': 0,
        'total_tokens': 0,
    }

    def _consume_run_result(run_result: Any, *, output_tag: str) -> str:
        usage = run_result.context_wrapper.usage
        usage_totals['requests'] += int(getattr(usage, 'requests', 0) or 0)
        usage_totals['input_tokens'] += int(getattr(usage, 'input_tokens', 0) or 0)
        usage_totals['output_tokens'] += int(getattr(usage, 'output_tokens', 0) or 0)
        usage_totals['total_tokens'] += int(getattr(usage, 'total_tokens', 0) or 0)
        usage_payload = SimpleNamespace(**usage_totals)
        _sync_token_usage(job_id, usage_payload)
        runtime.sync_state_usage(usage_payload)

        final_output_text = str(run_result.final_output or '').strip()
        if final_output_text:
            write_text_atomic(Path(runtime.job_dir / 'agent_final_output.txt'), final_output_text)
            write_text_atomic(
                Path(runtime.job_dir / f'agent_final_output_{output_tag}.txt'),
                final_output_text,
            )
        return final_output_text

    for attempt in range(1, max_attempts + 1):
        if runtime.final_markdown_text:
            append_event(
                job_id,
                'agent_run_skipped_after_final_write',
                attempt=attempt,
                reason='final_report_already_persisted',
            )
            break

        run_task = asyncio.create_task(
            Runner.run(
                agent,
                input=next_input,
                context=runtime,
                max_turns=max(20, settings.agent_max_turns),
                run_config=run_config,
            )
        )
        run_result = None
        while True:
            done, _ = await asyncio.wait({run_task}, timeout=0.5)
            if run_task in done:
                try:
                    run_result = run_task.result()
                except Exception as exc:
                    if runtime.final_markdown_text:
                        append_event(
                            job_id,
                            'agent_run_post_final_exception_ignored',
                            attempt=attempt,
                            error=f'{type(exc).__name__}: {exc}',
                            reason='final_report_already_persisted',
                        )
                        run_result = None
                        break
                    raise
                break
            if runtime.final_markdown_text:
                run_task.cancel()
                append_event(
                    job_id,
                    'agent_run_cancelled_after_final_write',
                    attempt=attempt,
                    reason='final_report_already_persisted',
                )
                try:
                    await run_task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    append_event(
                        job_id,
                        'agent_run_cancel_post_final_exception_ignored',
                        attempt=attempt,
                        error=f'{type(exc).__name__}: {exc}',
                        reason='final_report_already_persisted',
                    )
                break

        if run_result is None and runtime.final_markdown_text:
            append_event(
                job_id,
                'agent_run_terminated_after_final_write',
                attempt=attempt,
                reason='final_report_already_persisted',
            )
            break

        _consume_run_result(run_result, output_tag=f'attempt_{attempt}')

        if runtime.final_markdown_text:
            break

        append_event(
            job_id,
            'agent_run_incomplete',
            attempt=attempt,
            max_attempts=max_attempts,
            reason='no_final_report_persisted',
        )

        if attempt >= max_attempts:
            append_event(
                job_id,
                'agent_forced_final_write_start',
                attempt=attempt,
                reason='max_attempt_reached_without_final_write',
            )
            forced_input = [
                *run_result.to_input_list(),
                {
                    'role': 'user',
                    'content': (
                        'MANDATORY ACTION NOW: Call review_final_markdown_write in section mode immediately. '
                        'Submit exactly one required section per call using '
                        'review_final_markdown_write(section_id=<required_section_id>, section_content=<section_markdown>). '
                        'After each call, inspect completed_sections/missing_sections/next_required_section and '
                        'submit the next required section right away until status=ok. '
                        'Do not output plain-text final report. If the tool returns retry_required/error, '
                        'follow message/next_steps and retry review_final_markdown_write.'
                    ),
                },
            ]
            forced_choices = ['review_final_markdown_write', 'required']
            for forced_choice in forced_choices:
                if runtime.final_markdown_text:
                    append_event(
                        job_id,
                        'agent_forced_final_write_skipped_after_success',
                        attempt=attempt,
                        tool_choice=forced_choice,
                        reason='final_report_already_persisted',
                    )
                    break
                try:
                    forced_agent = Agent(
                        name='DeepReviewer2AgentFinalWriteEnforcer',
                        instructions=prompt,
                        tools=tools,
                        model=agent_model,
                        model_settings=_build_agent_model_settings(tool_choice=forced_choice),
                    )
                    forced_result = await Runner.run(
                        forced_agent,
                        input=forced_input,
                        context=runtime,
                        max_turns=12,
                        run_config=run_config,
                    )
                except Exception as exc:
                    if runtime.final_markdown_text:
                        append_event(
                            job_id,
                            'agent_forced_final_write_post_success_exception_ignored',
                            attempt=attempt,
                            tool_choice=forced_choice,
                            error=f'{type(exc).__name__}: {exc}',
                            reason='final_report_already_persisted',
                        )
                        break
                    append_event(
                        job_id,
                        'agent_forced_final_write_error',
                        attempt=attempt,
                        tool_choice=forced_choice,
                        error=f'{type(exc).__name__}: {exc}',
                    )
                    continue

                forced_output_text = _consume_run_result(
                    forced_result,
                    output_tag=f'attempt_{attempt}_forced_final_write',
                )
                append_event(
                    job_id,
                    'agent_forced_final_write_result',
                    attempt=attempt,
                    tool_choice=forced_choice,
                    final_output_chars=len(forced_output_text),
                    final_write_persisted=bool(runtime.final_markdown_text),
                )
                if runtime.final_markdown_text:
                    break
                forced_input = [
                    *forced_result.to_input_list(),
                    {
                        'role': 'user',
                        'content': (
                            'The final report is still not persisted. Continue section-mode submission now: '
                            'call review_final_markdown_write with section_id + section_content for the next required section.'
                        ),
                    },
                ]

            break

        set_status(
            job_id,
            JobStatus.agent_running,
            (
                'Agent ended without final report write. '
                f'Resuming review runtime (attempt {attempt + 1}/{max_attempts})...'
            ),
        )
        usage = runtime.paper_search_usage
        continuation_instruction = (
            'Resume the same review job from current state. '
            'Do not restart Phase 1 planning unless a hard gate is still unmet.\n'
            f'Current state: annotations={runtime.annotation_count}, '
            f'paper_search_total_calls={usage.total_calls}, '
            f'distinct_queries={usage.distinct_queries}, '
            f'effective_paper_search_calls={usage.effective_calls}.\n'
            'If gates are met, go directly to final report assembly in section mode and call '
            'review_final_markdown_write(section_id=<required_section_id>, section_content=<section_markdown>) '
            'as soon as possible.\n'
            'Mandatory: your next substantive action must be a section-mode tool call '
            '`review_final_markdown_write(...)`; plain chat markdown is invalid.\n'
            'If a gate is unmet or the write tool returns an error, follow message/next_steps exactly, '
            'perform minimal remediation, then retry review_final_markdown_write.\n'
            'Never end this run without a successful review_final_markdown_write.'
        )
        next_input = [
            *run_result.to_input_list(),
            {
                'role': 'user',
                'content': continuation_instruction,
            },
        ]

    if not runtime.final_markdown_text:
        raise RuntimeError(
            'Agent finished without successful review_final_markdown_write. '
            'Final report gate was not satisfied.'
        )

    set_status(job_id, JobStatus.pdf_exporting, 'Rendering final markdown report into PDF...')

    final_md_path = Path(artifacts['final_markdown'])
    report_pdf_path = Path(artifacts['report_pdf'])
    if not final_md_path.exists():
        raise RuntimeError(f'Final markdown not found: {final_md_path}')

    state_token_usage = _token_usage_payload_from_state(load_job_state(job_id))
    token_usage_for_pdf = {
        'requests': max(int(usage_totals.get('requests', 0)), int(state_token_usage.get('requests', 0))),
        'input_tokens': max(int(usage_totals.get('input_tokens', 0)), int(state_token_usage.get('input_tokens', 0))),
        'output_tokens': max(int(usage_totals.get('output_tokens', 0)), int(state_token_usage.get('output_tokens', 0))),
        'total_tokens': max(int(usage_totals.get('total_tokens', 0)), int(state_token_usage.get('total_tokens', 0))),
    }
    if token_usage_for_pdf['total_tokens'] <= 0:
        token_usage_for_pdf['total_tokens'] = (
            int(token_usage_for_pdf['input_tokens']) + int(token_usage_for_pdf['output_tokens'])
        )

    _render_report_pdf(
        job_id=job_id,
        job_title=job.title,
        source_pdf_name=job.source_pdf_name,
        final_md_path=final_md_path,
        source_pdf_path=source_pdf,
        report_pdf_path=report_pdf_path,
        annotations=list(runtime.annotations),
        content_list=parse_result.content_list,
        token_usage=token_usage_for_pdf,
        agent_model=str(settings.agent_model or '').strip(),
    )

    def apply_completed(state):
        state.status = JobStatus.completed
        state.message = 'Review pipeline completed.'
        state.error = None
        state.final_report_ready = True
        state.pdf_ready = report_pdf_path.exists()
        state.artifacts.final_markdown_path = str(final_md_path)
        state.artifacts.report_pdf_path = str(report_pdf_path)

    mutate_job_state(job_id, apply_completed)
    append_event(job_id, 'completed', report_pdf_path=str(report_pdf_path))


def run_job(job_id: str) -> None:
    try:
        asyncio.run(run_job_async(job_id))
    except Exception as exc:
        detail = ''.join(traceback.format_exception_only(type(exc), exc)).strip()
        stack = traceback.format_exc()
        append_event(job_id, 'pipeline_exception', error=detail, stack=stack)
        if _complete_with_existing_final_report(job_id, warning=detail):
            return
        fail_job(
            job_id,
            message='Review pipeline failed.',
            error=detail,
        )
