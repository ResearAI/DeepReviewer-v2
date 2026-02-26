from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from .config import get_settings


def jobs_root() -> Path:
    root = get_settings().data_dir / 'jobs'
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_job_id(job_id: UUID | str) -> str:
    if isinstance(job_id, UUID):
        return str(job_id)
    token = str(job_id or '').strip()
    if not token:
        raise ValueError('job_id is required')
    try:
        return str(UUID(token))
    except Exception as exc:
        raise ValueError(f'invalid job_id: {job_id}') from exc


def job_dir(job_id: UUID | str) -> Path:
    path = jobs_root() / _safe_job_id(job_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path(job_id: UUID | str) -> Path:
    return job_dir(job_id) / 'job.json'


def events_path(job_id: UUID | str) -> Path:
    return job_dir(job_id) / 'events.jsonl'


def annotations_path(job_id: UUID | str) -> Path:
    return job_dir(job_id) / 'annotations.json'


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(content, encoding='utf-8')
    tmp.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))


def append_event(job_id: UUID | str, event: str, **extra: Any) -> None:
    now = datetime.now(timezone.utc).isoformat()
    row = {
        'ts': now,
        'event': event,
        **extra,
    }
    events_file = events_path(job_id)
    events_file.parent.mkdir(parents=True, exist_ok=True)
    with events_file.open('a', encoding='utf-8') as f:
        f.write(json.dumps(row, ensure_ascii=False) + '\n')
