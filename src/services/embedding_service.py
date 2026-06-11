"""
Embedding service for semantic similarity computation.

Design:
  - Lazy model load: the sentence-transformers model is not imported until
    the first call to encode(). This keeps startup fast when embeddings
    are disabled.
  - Singleton pattern via module-level _instance: the heavy model object
    is loaded exactly once per process regardless of how many validators
    share an EmbeddingService.
  - Hash-keyed cache: embeddings are stored by hash(text) so identical
    text blobs (e.g. the same supervisor queried twice) are never re-encoded.
"""

from __future__ import annotations

import hashlib
from typing import Optional

from ..utils.logger import get_logger

logger = get_logger(__name__)

# Module-level singleton — shared by all callers in the same process
_instance: Optional["EmbeddingService"] = None

# Embedding cache: sha256(text) → list[float]
_embedding_cache: dict[str, list[float]] = {}


def get_embedding_service(model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> "EmbeddingService":
    """
    Return the process-wide EmbeddingService singleton.
    Creates and loads the model on first call.

    Args:
        model_name: HuggingFace model identifier.

    Returns:
        Loaded EmbeddingService instance.

    Raises:
        ImportError: If sentence-transformers is not installed.
        RuntimeError: If the model fails to load.
    """
    global _instance
    if _instance is None:
        _instance = EmbeddingService(model_name)
        _instance._load()
    return _instance


def clear_embedding_cache() -> None:
    """Flush the embedding cache. Used in tests to isolate runs."""
    _embedding_cache.clear()


def reset_singleton() -> None:
    """Reset the singleton (used in tests only)."""
    global _instance
    _instance = None


class EmbeddingService:
    """
    Wraps a sentence-transformers model to produce dense text embeddings
    and compute cosine similarity.

    Do not instantiate directly — use get_embedding_service() to get
    the singleton.

    Args:
        model_name: sentence-transformers model identifier.
    """

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        self.model_name = model_name
        self._model = None   # loaded lazily via _load()

    def _load(self) -> None:
        """Load the sentence-transformers model. Called once by get_embedding_service()."""
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            logger.info("Loading embedding model: %s", self.model_name)
            self._model = SentenceTransformer(self.model_name)
            logger.info("Embedding model loaded successfully")
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers is required for embedding-based domain validation. "
                "Install it with: pip install sentence-transformers"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"Failed to load embedding model '{self.model_name}': {exc}") from exc

    def encode(self, texts: list[str]) -> list[list[float]]:
        """
        Encode a list of text strings into dense embedding vectors.

        Results are cached by sha256(text) to avoid re-encoding identical
        text across multiple validator calls.

        Args:
            texts: List of text strings to encode.

        Returns:
            List of float vectors, one per input text.
        """
        if self._model is None:
            raise RuntimeError("EmbeddingService model is not loaded. Call _load() first.")

        results: list[list[float]] = []
        to_encode: list[tuple[int, str]] = []   # (original index, text)

        for i, text in enumerate(texts):
            key = _text_hash(text)
            if key in _embedding_cache:
                results.append(_embedding_cache[key])
            else:
                results.append([])          # placeholder
                to_encode.append((i, text))

        if to_encode:
            raw_texts = [t for _, t in to_encode]
            embeddings = self._model.encode(raw_texts, convert_to_numpy=True)
            for (idx, text), vec in zip(to_encode, embeddings):
                key = _text_hash(text)
                # Handle both numpy arrays (production) and plain lists (tests/mocks)
                vec_list = vec.tolist() if hasattr(vec, "tolist") else list(vec)
                _embedding_cache[key] = vec_list
                results[idx] = vec_list

        return results

    def similarity(self, text1: str, text2: str) -> float:
        """
        Compute the cosine similarity between two text strings.

        Args:
            text1: First text.
            text2: Second text.

        Returns:
            Cosine similarity in [0, 1] (clipped; raw cosine can be negative).
        """
        vecs = self.encode([text1, text2])
        return float(_cosine_similarity(vecs[0], vecs[1]))


# ------------------------------------------------------------------ #
# Pure math helpers                                                    #
# ------------------------------------------------------------------ #

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length float vectors, clipped to [0, 1]."""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    raw = dot / (norm_a * norm_b)
    return max(0.0, min(1.0, raw))


def _text_hash(text: str) -> str:
    """Return a stable sha256 hex digest for use as cache key."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
