from __future__ import annotations

import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from .storage import append_event, job_dir, read_json, state_path, write_json_atomic
from .types import JobState, JobStatus


_STATE_LOCK = threading.RLock()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def save_job_state(job: JobState) -> JobState:
    with _STATE_LOCK:
        job.updated_at = now_utc()
        write_json_atomic(state_path(job.id), job.model_dump(mode='json'))
    return job


def load_job_state(job_id: UUID | str) -> JobState | None:
    try:
        path = state_path(job_id)
    except ValueError:
        return None
    if not path.exists():
        return None
    with _STATE_LOCK:
        payload = read_json(path)
    return JobState.model_validate(payload)


def update_job_state(job_id: UUID | str, **fields: Any) -> JobState:
    with _STATE_LOCK:
        existing = load_job_state(job_id)
        if existing is None:
            raise FileNotFoundError(f'Job not found: {job_id}')
        for key, value in fields.items():
            setattr(existing, key, value)
        existing.updated_at = now_utc()
        write_json_atomic(state_path(job_id), existing.model_dump(mode='json'))
    return existing


def mutate_job_state(job_id: UUID | str, fn: Callable[[JobState], None]) -> JobState:
    with _STATE_LOCK:
        existing = load_job_state(job_id)
        if existing is None:
            raise FileNotFoundError(f'Job not found: {job_id}')
        fn(existing)
        existing.updated_at = now_utc()
        write_json_atomic(state_path(job_id), existing.model_dump(mode='json'))
    return existing


def set_status(job_id: UUID | str, status: JobStatus, message: str, *, event: str | None = None) -> JobState:
    job = update_job_state(job_id, status=status, message=message)
    append_event(job_id, event or 'status', status=status.value, message=message)
    return job


def fail_job(job_id: UUID | str, *, message: str, error: str) -> JobState:
    job = update_job_state(job_id, status=JobStatus.failed, message=message, error=error)
    append_event(job_id, 'failed', message=message, error=error)
    return job


def ensure_artifact_paths(job_id: UUID | str) -> dict[str, Path]:
    root = job_dir(job_id)
    return {
        'source_pdf': root / 'source.pdf',
        'mineru_markdown': root / 'mineru_full.md',
        'mineru_content_list': root / 'mineru_content_list.json',
        'annotations': root / 'annotations.json',
        'final_markdown': root / 'final_report.md',
        'report_pdf': root / 'final_report.pdf',
        'prompt_snapshot': root / 'agent_prompt.txt',
        'raw_result': root / 'mineru_result_raw.json',
    }


def reset_job_dir(job_id: UUID | str) -> None:
    root = job_dir(job_id)
    for child in root.iterdir():
        if child.name == 'job.json':
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except Exception:
                pass
