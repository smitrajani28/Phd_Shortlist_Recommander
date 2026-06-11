"""
LLM client abstractions for WhyMatchGenerator.

Design:
- LLMClient is a Protocol so any backend can be injected without
  subclassing (structural typing, no base class needed).
- GeminiLLMClient is the default production implementation (free tier).
- OpenAILLMClient is retained as an alternative backend.
- Tests inject a stub via the Protocol.

Provider selection is controlled by LLM_PROVIDER in .env:
  LLM_PROVIDER=gemini   → GeminiLLMClient  (default)
  LLM_PROVIDER=openai   → OpenAILLMClient
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


class GeminiLLMClient:
    """
    LLM client backed by Google Gemini (free tier).

    Default model: gemini-1.5-flash  — free quota, fast, good quality.
    Requires: pip install google-generativeai
    API key:  https://aistudio.google.com/app/apikey  (free, no card needed)
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-1.5-flash",
        temperature: float = 0.0,
    ) -> None:
        self._model_name = model
        self._temperature = temperature
        self._model = self._build_client(api_key, model)

    def generate(self, prompt: str) -> str:
        response = self._model.generate_content(
            prompt,
            generation_config={"temperature": self._temperature},
        )
        return response.text.strip() if response.text else ""

    @staticmethod
    def _build_client(api_key: str, model: str):
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            return genai.GenerativeModel(model)
        except ImportError as exc:
            raise ImportError(
                "google-generativeai is required for GeminiLLMClient. "
                "Install it with: pip install google-generativeai"
            ) from exc


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
                "openai package is required for OpenAILLMClient. "
                "Install it with: pip install openai"
            ) from exc
