"""
Thin shared HTTP client for the OpenAlex REST API.

Centralises:
  - User-Agent / polite-pool header
  - Exponential-backoff retry
  - In-process URL cache (dict, lives for one process lifetime)

Both OpenAlexRetriever and EvidenceCollector import this instead of
duplicating the retry/cache logic.
"""

import time
from typing import Any

import httpx

from ..utils.logger import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.openalex.org"

# Module-level cache shared across all callers in the same process
_cache: dict[str, Any] = {}


class OpenAlexClient:
    """
    Stateless HTTP client for OpenAlex.

    Args:
        email:       Added to User-Agent for the polite pool.
        timeout:     Per-request timeout in seconds.
        max_retries: Maximum retry attempts (exponential back-off: 1s, 2s, 4s…).
    """

    def __init__(
        self,
        email: str | None = None,
        timeout: int = 10,
        max_retries: int = 3,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self._headers = {
            "User-Agent": f"PhDShortlistBuilder/1.0 (mailto:{email or 'anonymous'})"
        }

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        """
        HTTP GET against the OpenAlex API.

        Args:
            path:   API path, e.g. "/authors/A123" or "/works".
            params: Optional query-string parameters.

        Returns:
            Parsed JSON response dict.

        Raises:
            RuntimeError: After all retries are exhausted.
        """
        url = self._build_url(path, params or {})

        if url in _cache:
            logger.debug("Cache hit: %s", url)
            return _cache[url]

        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.debug("GET %s (attempt %d)", url, attempt)
                response = httpx.get(url, headers=self._headers, timeout=self.timeout)
                response.raise_for_status()
                data: dict = response.json()
                _cache[url] = data
                return data
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_exc = exc
                wait = 2 ** (attempt - 1)
                logger.warning("OpenAlex request failed (%s). Retrying in %ds…", exc, wait)
                time.sleep(wait)

        raise RuntimeError(
            f"OpenAlex request failed after {self.max_retries} attempts: {url}"
        ) from last_exc

    def _build_url(self, path: str, params: dict) -> str:
        if not params:
            return f"{BASE_URL}{path}"
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{BASE_URL}{path}?{query}"


def clear_cache() -> None:
    """Flush the in-process cache. Useful in tests to isolate runs."""
    _cache.clear()
