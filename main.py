from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from deepreview.config import get_settings
from deepreview.runner import run_job
from deepreview.state import ensure_artifact_paths, load_job_state, save_job_state
from deepreview.storage import append_event, job_dir
from deepreview.types import JobState, JobStatus


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _status_snapshot(job: JobState) -> dict:
    return {
        'job_id': str(job.id),
        'status': job.status.value,
        'message': job.message,
        'error': job.error,
        'annotation_count': job.annotation_count,
        'final_report_ready': job.final_report_ready,
        'pdf_ready': job.pdf_ready,
        'usage': job.usage.model_dump(mode='json'),
        'created_at': job.created_at.isoformat(),
        'updated_at': job.updated_at.isoformat(),
        'artifacts': job.artifacts.model_dump(mode='json'),
        'metadata': job.metadata,
    }


def _submit_response(job: JobState, completed: bool) -> dict:
    payload: dict = {
        'job_id': str(job.id),
        'status': job.status.value,
        'message': job.message,
        'completed': completed,
        'usage': job.usage.model_dump(mode='json'),
        'metadata': job.metadata,
    }
    if completed:
        payload['result'] = {
            'final_markdown_path': job.artifacts.final_markdown_path,
            'report_pdf_path': job.artifacts.report_pdf_path,
        }
    return payload


def _create_job(pdf_path: Path, title: str | None) -> JobState:
    job = JobState(
        title=(title or pdf_path.stem).strip() or pdf_path.stem,
        source_pdf_name=pdf_path.name,
    )
    save_job_state(job)

    artifacts = ensure_artifact_paths(job.id)
    source_pdf_path = Path(artifacts['source_pdf'])
    source_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(pdf_path), str(source_pdf_path))

    def apply(state: JobState) -> None:
        state.artifacts.source_pdf_path = str(source_pdf_path)

    # mutate via save -> load -> apply
    loaded = load_job_state(job.id)
    if loaded is None:
        raise RuntimeError(f'failed to reload job after create: {job.id}')
    apply(loaded)
    save_job_state(loaded)

    append_event(job.id, 'created', source_pdf=str(source_pdf_path), title=job.title)
    return loaded


def _spawn_worker(job_id: str) -> int:
    here = Path(__file__).resolve()
    root = here.parent
    logs_dir = job_dir(job_id)
    stdout_path = logs_dir / 'worker.stdout.log'
    stderr_path = logs_dir / 'worker.stderr.log'

    stdout_f = stdout_path.open('ab')
    stderr_f = stderr_path.open('ab')

    try:
        process = subprocess.Popen(
            [sys.executable, str(here), '_run-job', '--job-id', str(job_id)],
            cwd=str(root),
            start_new_session=True,
            stdout=stdout_f,
            stderr=stderr_f,
        )
    finally:
        stdout_f.close()
        stderr_f.close()

    append_event(job_id, 'worker_spawned', pid=process.pid)
    return process.pid


def cmd_submit(args: argparse.Namespace) -> int:
    settings = get_settings()
    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.exists() or not pdf_path.is_file():
        _print_json({'status': 'error', 'message': f'PDF not found: {pdf_path}'})
        return 2
    file_size = int(pdf_path.stat().st_size)
    if file_size <= 0:
        _print_json({'status': 'error', 'message': f'PDF is empty: {pdf_path}'})
        return 2
    if file_size > int(settings.max_pdf_bytes):
        _print_json(
            {
                'status': 'error',
                'message': (
                    f'PDF too large: {file_size} bytes, '
                    f'max allowed {int(settings.max_pdf_bytes)} bytes'
                ),
            }
        )
        return 2

    job = _create_job(pdf_path, args.title)
    _spawn_worker(str(job.id))

    wait_seconds = args.wait_seconds
    if wait_seconds is None:
        wait_seconds = settings.submit_default_wait_seconds
    wait_seconds = max(0, int(wait_seconds))

    deadline = time.time() + wait_seconds
    poll_interval = max(0.3, float(settings.submit_poll_interval_seconds))

    latest = job
    while time.time() <= deadline:
        current = load_job_state(job.id)
        if current is not None:
            latest = current
        if latest.status in {JobStatus.completed, JobStatus.failed}:
            break
        if wait_seconds == 0:
            break
        time.sleep(poll_interval)

    completed = latest.status == JobStatus.completed
    _print_json(_submit_response(latest, completed=completed))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    job = load_job_state(args.job_id)
    if job is None:
        _print_json({'status': 'error', 'message': f'Job not found: {args.job_id}'})
        return 2

    _print_json(_status_snapshot(job))
    return 0


def cmd_result(args: argparse.Namespace) -> int:
    job = load_job_state(args.job_id)
    if job is None:
        _print_json({'status': 'error', 'message': f'Job not found: {args.job_id}'})
        return 2

    if job.status != JobStatus.completed:
        _print_json(
            {
                'status': 'not_ready',
                'job_id': str(job.id),
                'current_status': job.status.value,
                'message': job.message,
                'usage': job.usage.model_dump(mode='json'),
            }
        )
        return 0

    md_path = Path(job.artifacts.final_markdown_path or '')
    pdf_path = Path(job.artifacts.report_pdf_path or '')

    if args.format == 'md':
        if not md_path.exists():
            _print_json({'status': 'error', 'message': f'Markdown report missing: {md_path}'})
            return 2
        print(md_path.read_text(encoding='utf-8'))
        return 0

    if args.format == 'pdf':
        _print_json(
            {
                'job_id': str(job.id),
                'report_pdf_path': str(pdf_path) if pdf_path.exists() else None,
                'final_markdown_path': str(md_path) if md_path.exists() else None,
            }
        )
        return 0

    markdown = md_path.read_text(encoding='utf-8') if md_path.exists() else ''
    _print_json(
        {
            'job_id': str(job.id),
            'final_markdown_path': str(md_path) if md_path.exists() else None,
            'report_pdf_path': str(pdf_path) if pdf_path.exists() else None,
            'final_markdown': markdown,
        }
    )
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    interval = max(0.5, float(args.interval))
    timeout_seconds = max(0, int(args.timeout)) if args.timeout is not None else None
    deadline = time.time() + timeout_seconds if timeout_seconds is not None else None

    last_status = None
    while True:
        job = load_job_state(args.job_id)
        if job is None:
            _print_json({'status': 'error', 'message': f'Job not found: {args.job_id}'})
            return 2

        if last_status != job.status.value:
            _print_json(_status_snapshot(job))
            last_status = job.status.value

        if job.status in {JobStatus.completed, JobStatus.failed}:
            return 0

        if deadline is not None and time.time() > deadline:
            _print_json({'status': 'timeout', 'job_id': str(job.id), 'current_status': job.status.value})
            return 0

        time.sleep(interval)


def cmd_run_job(args: argparse.Namespace) -> int:
    run_job(str(args.job_id))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='DeepReviewer-2.0 minimal backend CLI')
    sub = parser.add_subparsers(dest='command', required=True)

    submit = sub.add_parser('submit', help='Submit a PDF review job')
    submit.add_argument('--pdf', required=True, help='Path to PDF file')
    submit.add_argument('--title', required=False, help='Optional title override')
    submit.add_argument('--wait-seconds', type=int, required=False, help='Wait window before returning')
    submit.set_defaults(func=cmd_submit)

    status = sub.add_parser('status', help='Get job status')
    status.add_argument('--job-id', required=True, help='Job ID')
    status.set_defaults(func=cmd_status)

    result = sub.add_parser('result', help='Fetch completed result')
    result.add_argument('--job-id', required=True, help='Job ID')
    result.add_argument('--format', choices=['md', 'pdf', 'all'], default='all')
    result.set_defaults(func=cmd_result)

    watch = sub.add_parser('watch', help='Watch job until completion')
    watch.add_argument('--job-id', required=True, help='Job ID')
    watch.add_argument('--interval', type=float, default=2.0)
    watch.add_argument('--timeout', type=int, required=False)
    watch.set_defaults(func=cmd_watch)

    run_job_cmd = sub.add_parser('_run-job', help=argparse.SUPPRESS)
    run_job_cmd.add_argument('--job-id', required=True)
    run_job_cmd.set_defaults(func=cmd_run_job)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == '__main__':
    raise SystemExit(main())
