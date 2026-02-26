from __future__ import annotations

from dataclasses import dataclass

from openai import AsyncOpenAI


@dataclass
class BasicLLMConfig:
    base_url: str | None
    api_key: str | None
    model: str
    timeout_seconds: int


class BasicLLMClient:
    """Minimal async OpenAI client helper for optional non-agent calls."""

    def __init__(self, cfg: BasicLLMConfig):
        self.cfg = cfg
        self._client: AsyncOpenAI | None = None

    @property
    def configured(self) -> bool:
        return bool(self.cfg.api_key)

    def client(self) -> AsyncOpenAI:
        if not self.configured:
            raise RuntimeError('LLM client is not configured')
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.cfg.api_key,
                base_url=self.cfg.base_url,
                timeout=max(30, int(self.cfg.timeout_seconds)),
            )
        return self._client
