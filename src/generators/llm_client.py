"""
LLM client abstractions for WhyMatchGenerator.

Design:
- LLMClient is a Protocol so any backend can be injected without
  subclassing (structural typing, no base class needed).
- OpenAILLMClient is the production implementation (GPT-4o-mini).
- No API key configured → WhyMatchGenerator uses its deterministic fallback.
"""

from typing import Protocol, runtime_checkable
from ..utils.logger import get_logger

logger = get_logger(__name__)


@runtime_checkable
class LLMClient(Protocol):
    """Minimal interface every LLM backend must satisfy."""

    def generate(self, prompt: str) -> str:
        """Send `prompt` to the LLM and return the text completion."""
        ...


class OpenAILLMClient:
    """
    LLM client backed by the OpenAI Chat Completions API.
    Requires: pip install openai
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        max_tokens: int = 180,
        temperature: float = 0.0,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._client = self._build_client(api_key)

    def generate(self, prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )
        return response.choices[0].message.content or ""

    @staticmethod
    def _build_client(api_key: str):
        try:
            import openai
            return openai.OpenAI(api_key=api_key)
        except ImportError as exc:
            raise ImportError(
                "openai package is required. Install it with: pip install openai"
            ) from exc
