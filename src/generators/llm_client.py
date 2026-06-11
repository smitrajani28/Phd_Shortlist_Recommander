"""
LLM client abstractions for WhyMatchGenerator.

Design:
- LLMClient is a Protocol so any backend can be injected without
  subclassing (structural typing, no base class needed).
- OpenAILLMClient is the production implementation.
- Tests inject a MockLLMClient that accepts a callable stub.
"""

from typing import Protocol, runtime_checkable
from ..utils.logger import get_logger

logger = get_logger(__name__)


@runtime_checkable
class LLMClient(Protocol):
    """
    Minimal interface every LLM backend must satisfy.
    Accepts a prompt string, returns a completion string.
    """

    def generate(self, prompt: str) -> str:
        """
        Send `prompt` to the LLM and return the text completion.

        Args:
            prompt: Fully-formatted prompt string.

        Returns:
            Generated text (may be empty string on failure).

        Raises:
            Any exception — callers must catch and fall back.
        """
        ...


class OpenAILLMClient:
    """
    Production LLM client backed by the OpenAI Chat Completions API.

    Args:
        api_key:     OpenAI API key (never hard-code; read from Settings).
        model:       Model identifier, e.g. "gpt-4o-mini".
        max_tokens:  Hard cap on completion length.
        temperature: 0.0 for maximum determinism.
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
        """
        Call the OpenAI Chat Completions API and return the message content.

        Raises:
            openai.OpenAIError: On API or network failure.
        """
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )
        return response.choices[0].message.content or ""

    @staticmethod
    def _build_client(api_key: str):
        """Lazily import openai so the rest of the app works without it installed."""
        try:
            import openai
            return openai.OpenAI(api_key=api_key)
        except ImportError as exc:
            raise ImportError(
                "openai package is required for OpenAILLMClient. "
                "Install it with: pip install openai"
            ) from exc
