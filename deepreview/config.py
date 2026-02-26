from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        case_sensitive=False,
        extra='ignore',
    )

    app_name: str = 'DeepReviewer-2.0 OSS Backend'

    data_dir: Path = Field(default=Path('./data'))

    # OpenAI Agent SDK runtime
    openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices('OPENAI_API_KEY', 'API_KEY', 'LLM_API_KEY'),
    )
    openai_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices('BASE_URL', 'OPENAI_BASE_URL', 'LLM_BASE_URL'),
    )
    openai_use_responses_api: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            'OPENAI_USE_RESPONSES_API',
            'USE_RESPONSES_API',
            'LLM_USE_RESPONSES_API',
        ),
    )
    agent_model: str = 'gpt-5.2'
    agent_temperature: float = 0.2
    agent_max_tokens: int = 4096
    agent_max_turns: int = 1000
    agent_resume_attempts: int = 2
    max_markdown_chars_to_model: int = 120000

    # Submit behavior
    submit_default_wait_seconds: int = 8
    submit_poll_interval_seconds: float = 1.0
    max_pdf_bytes: int = 50 * 1024 * 1024

    # MinerU v4 upload + parse
    mineru_base_url: str = 'https://mineru.net/api/v4'
    mineru_api_token: str | None = None
    mineru_model_version: str = 'vlm'
    mineru_upload_endpoint: str = '/file-urls/batch'
    # Comma-separated endpoint templates. Must include {batch_id}
    mineru_poll_endpoint_templates: str = (
        '/extract-results/batch/{batch_id},'
        '/extract-results/{batch_id},'
        '/extract/task/{batch_id}'
    )
    mineru_poll_interval_seconds: float = 3.0
    mineru_poll_timeout_seconds: int = 900
    # Default strict mode: keep MinerU parity and fail loudly if unavailable.
    mineru_allow_local_fallback: bool = False

    # Optional external paper search/read service
    paper_search_base_url: str | None = None
    paper_search_api_key: str | None = None
    paper_search_endpoint: str = '/pasa/search'
    paper_search_timeout_seconds: int = 120

    paper_read_base_url: str | None = None
    paper_read_api_key: str | None = None
    paper_read_endpoint: str = '/read'
    paper_read_timeout_seconds: int = 180

    # Gates aligned with DeepReviewer finalization logic
    enable_final_gates: bool = False
    min_paper_search_calls_for_pdf_annotate: int = 3
    min_paper_search_calls_for_final: int = 3
    min_distinct_paper_queries_for_final: int = 3
    min_annotations_for_final: int = 10
    min_english_words_for_final: int = 0
    min_chinese_chars_for_final: int = 0
    force_english_output: bool = True
    ui_language: str = 'en'

    # PDF export
    pdf_font_name: str = 'Helvetica'
    pdf_title_font_size: int = 15
    pdf_body_font_size: int = 10
    pdf_page_margin: int = 48

    def mineru_poll_templates(self) -> list[str]:
        templates: list[str] = []
        for item in self.mineru_poll_endpoint_templates.split(','):
            normalized = item.strip()
            if not normalized:
                continue
            templates.append(normalized)
        return templates


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / 'jobs').mkdir(parents=True, exist_ok=True)
    return settings
