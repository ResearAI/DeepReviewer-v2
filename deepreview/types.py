from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, Enum):
    queued = 'queued'
    pdf_uploading_to_mineru = 'pdf_uploading_to_mineru'
    pdf_parsing = 'pdf_parsing'
    agent_running = 'agent_running'
    final_report_persisting = 'final_report_persisting'
    pdf_exporting = 'pdf_exporting'
    completed = 'completed'
    failed = 'failed'


class TokenUsage(BaseModel):
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class ToolUsage(BaseModel):
    total_calls: int = 0
    distinct_tools: int = 0
    per_tool: dict[str, int] = Field(default_factory=dict)


class PaperSearchUsage(BaseModel):
    total_calls: int = 0
    successful_calls: int = 0
    effective_calls: int = 0
    papers_found: int = 0
    distinct_queries: int = 0


class UsageSnapshot(BaseModel):
    token: TokenUsage = Field(default_factory=TokenUsage)
    tool: ToolUsage = Field(default_factory=ToolUsage)
    paper_search: PaperSearchUsage = Field(default_factory=PaperSearchUsage)


class AnnotationItem(BaseModel):
    id: str
    page: int
    start_line: int
    end_line: int
    text: str
    comment: str
    summary: str | None = None
    object_type: str = 'suggestion'
    severity: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class JobArtifacts(BaseModel):
    source_pdf_path: str | None = None
    mineru_markdown_path: str | None = None
    mineru_content_list_path: str | None = None
    annotations_path: str | None = None
    final_markdown_path: str | None = None
    report_pdf_path: str | None = None
    prompt_snapshot_path: str | None = None


class JobState(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    title: str
    source_pdf_name: str

    status: JobStatus = JobStatus.queued
    message: str = 'Job queued.'
    error: str | None = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    usage: UsageSnapshot = Field(default_factory=UsageSnapshot)
    annotation_count: int = 0
    final_report_ready: bool = False
    pdf_ready: bool = False

    artifacts: JobArtifacts = Field(default_factory=JobArtifacts)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SubmitPayload(BaseModel):
    job_id: UUID
    status: JobStatus
    message: str
    completed: bool
    usage: UsageSnapshot
    result: dict[str, Any] | None = None


class StatusPayload(BaseModel):
    job_id: UUID
    status: JobStatus
    message: str
    error: str | None
    annotation_count: int
    final_report_ready: bool
    pdf_ready: bool
    usage: UsageSnapshot
    created_at: datetime
    updated_at: datetime
    artifacts: JobArtifacts
