from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import httpx
from transformers import AutoTokenizer


class VLLMAgent:
    """A PASA Agent that calls a vLLM OpenAI server via HTTP.

    It matches the minimal interface expected by `pasa.paper_agent.PaperAgent`:
      - infer(prompt) -> str
      - batch_infer(prompts) -> list[str]
      - infer_score(prompts) -> list[float]
    """

    def __init__(
        self,
        *,
        base_url: str,
        model_name: str,
        tokenizer_path: Optional[str],
        use_chat_template: bool,
        request_timeout: float = 300.0,
        logprobs: int = 20,
        api_key: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.use_chat_template = use_chat_template
        self.logprobs = logprobs

        headers: Dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        self._client = httpx.Client(timeout=request_timeout, headers=headers, trust_env=False)

        self._tokenizer = None
        if self.use_chat_template:
            if not tokenizer_path:
                raise ValueError("tokenizer_path is required when use_chat_template=True")
            self._tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, padding_side="left")

    # --------------------------------------------------------------------- #
    # Health / readiness
    # --------------------------------------------------------------------- #

    def list_models(self) -> List[str]:
        resp = self._client.get(f"{self.base_url}/models")
        resp.raise_for_status()
        data = resp.json()
        models = data.get("data") or []
        ids: List[str] = []
        for m in models:
            if isinstance(m, dict) and m.get("id"):
                ids.append(str(m["id"]))
        return ids

    def ensure_ready(self) -> None:
        ids = self.list_models()
        if self.model_name not in ids:
            raise RuntimeError(
                f"vLLM at {self.base_url} does not serve model {self.model_name!r}. "
                f"Available: {ids}"
            )

    def is_ready(self) -> bool:
        try:
            self.ensure_ready()
            return True
        except Exception:
            return False

    # --------------------------------------------------------------------- #
    # Prompt formatting
    # --------------------------------------------------------------------- #

    def _format_prompt(self, prompt: str) -> str:
        if not self.use_chat_template:
            return prompt
        if self._tokenizer is None:
            raise RuntimeError("Tokenizer not initialized")

        return self._tokenizer.apply_chat_template(
            [{"content": prompt.strip(), "role": "user"}],
            tokenize=False,
            max_length=992,
            add_generation_prompt=True,
        )

    # --------------------------------------------------------------------- #
    # OpenAI-compatible calls (/v1/completions)
    # --------------------------------------------------------------------- #

    def _post_completions(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/completions"
        resp = self._client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _ordered_choice_texts(data: Dict[str, Any]) -> List[str]:
        choices = data.get("choices") or []
        if not isinstance(choices, list):
            return []
        # vLLM returns index for each prompt; keep stable order.
        choices_sorted = sorted(choices, key=lambda c: int(c.get("index", 0)) if isinstance(c, dict) else 0)
        texts: List[str] = []
        for c in choices_sorted:
            if isinstance(c, dict):
                texts.append(str(c.get("text", "")))
        return texts

    def infer(self, prompt: str, sample: bool = False) -> str:
        formatted = self._format_prompt(prompt)
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "prompt": formatted,
            "max_tokens": 512,
            "temperature": 2.0 if sample else 0.0,
            "top_p": 0.8 if sample else 1.0,
        }
        data = self._post_completions(payload)
        texts = self._ordered_choice_texts(data)
        return texts[0] if texts else ""

    def batch_infer(self, prompts: List[str], batch_size: int = 8, sample: bool = False) -> List[str]:
        if not prompts:
            return []

        results: List[str] = []
        for i in range(0, len(prompts), batch_size):
            chunk = prompts[i : i + batch_size]
            formatted = [self._format_prompt(p) for p in chunk]
            payload: Dict[str, Any] = {
                "model": self.model_name,
                "prompt": formatted,
                "max_tokens": 512,
                "temperature": 2.0 if sample else 0.0,
                "top_p": 0.8 if sample else 1.0,
            }
            data = self._post_completions(payload)
            results.extend(self._ordered_choice_texts(data))
        return results

    def infer_score(self, prompts: List[str]) -> List[float]:
        """Return P(next_token == 'True') for each prompt.

        This mirrors the original transformers implementation which takes the
        probability of the 'True' token at the first generation step.
        """
        if not prompts:
            return []

        scores: List[float] = []
        # keep chunks reasonably sized
        chunk_size = 32
        for i in range(0, len(prompts), chunk_size):
            chunk = prompts[i : i + chunk_size]
            payload: Dict[str, Any] = {
                "model": self.model_name,
                "prompt": chunk,
                "max_tokens": 1,
                "temperature": 0.0,
                "top_p": 1.0,
                "logprobs": int(self.logprobs),
            }
            data = self._post_completions(payload)

            choices = data.get("choices") or []
            if not isinstance(choices, list):
                scores.extend([0.0] * len(chunk))
                continue

            # Keep results ordered by `index`
            choices_sorted = sorted(
                choices, key=lambda c: int(c.get("index", 0)) if isinstance(c, dict) else 0
            )
            for c in choices_sorted:
                if not isinstance(c, dict):
                    scores.append(0.0)
                    continue
                lp = c.get("logprobs") or {}
                top_logprobs = lp.get("top_logprobs") or []
                if not top_logprobs or not isinstance(top_logprobs, list):
                    scores.append(0.0)
                    continue

                token_map = top_logprobs[0]
                if not isinstance(token_map, dict):
                    scores.append(0.0)
                    continue

                true_logprob: Optional[float] = None
                for tok, logp in token_map.items():
                    if not isinstance(tok, str) or not isinstance(logp, (int, float)):
                        continue
                    if tok.strip() != "True":
                        continue
                    logp_f = float(logp)
                    true_logprob = logp_f if true_logprob is None else max(true_logprob, logp_f)

                scores.append(math.exp(true_logprob) if true_logprob is not None else 0.0)

        return scores

    def close(self) -> None:
        self._client.close()
