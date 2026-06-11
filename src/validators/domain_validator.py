"""
Validates that a supervisor's research domain overlaps meaningfully
with the student's extracted topics and preferred domains.

Three operating modes (controlled by the `mode` parameter):

  keyword_only   — original token-overlap scoring (0.4*keyword + 0.6*concept)
                   No embeddings. Fast, no ML dependency.

  embedding_only — pure embedding cosine similarity via EmbeddingDomainValidator.
                   Requires sentence-transformers.

  hybrid (default) — three-component score:
                   0.30 * keyword + 0.30 * concept + 0.40 * embedding
                   Automatically degrades to keyword_only if the embedding
                   service is unavailable (graceful fallback).

All modes:
  - Accept a no-preference student (no topics / no domains) → pass all.
  - Attach ValidationResult to supervisor.domain_validation.
  - Log rejection reasons.
  - Preserve the existing validate(supervisor) -> bool signature.
"""

from __future__ import annotations

from typing import Optional, Literal, TYPE_CHECKING

from ..models.student import StudentProfile
from ..models.supervisor import Supervisor
from ..models.validation import ValidationResult
from ..utils.logger import get_logger

if TYPE_CHECKING:
    from ..services.embedding_service import EmbeddingService

logger = get_logger(__name__)

# Default weights for keyword_only mode (unchanged from original)
_KEYWORD_WEIGHT = 0.4
_CONCEPT_WEIGHT = 0.6

DomainMode = Literal["keyword_only", "embedding_only", "hybrid"]


class DomainValidator:
    """
    Computes a domain-match score and rejects supervisors below `min_score`.

    Args:
        profile:           Student profile.
        min_score:         Minimum score to pass (default 0.35).
        mode:              "keyword_only" | "embedding_only" | "hybrid".
        embedding_service: Required when mode is "hybrid" or "embedding_only".
                           If None in those modes, falls back to keyword_only.
        embedding_min_score: Threshold forwarded to EmbeddingDomainValidator
                             (default same as min_score).
    """

    def __init__(
        self,
        profile: StudentProfile,
        min_score: float = 0.35,
        mode: DomainMode = "keyword_only",
        embedding_service: Optional["EmbeddingService"] = None,
        embedding_min_score: Optional[float] = None,
    ) -> None:
        self.min_score = min_score
        self.mode = mode
        self._student_tokens: set[str] = _tokenise_many(
            profile.extracted_topics + profile.preferred_domains
        )
        self._profile = profile

        # Build EmbeddingDomainValidator if applicable
        self._emb_validator = None
        if mode in ("hybrid", "embedding_only") and embedding_service is not None:
            from ..validators.embedding_domain_validator import EmbeddingDomainValidator
            self._emb_validator = EmbeddingDomainValidator(
                embedding_service=embedding_service,
                min_score=embedding_min_score if embedding_min_score is not None else min_score,
            )
        elif mode in ("hybrid", "embedding_only") and embedding_service is None:
            logger.warning(
                "DomainValidator mode='%s' requested but no embedding_service provided. "
                "Falling back to keyword_only.", mode,
            )

    def validate(self, supervisor: Supervisor) -> bool:
        """
        Returns True if the domain score meets the threshold.
        Attaches ValidationResult to supervisor.domain_validation.
        """
        # No student topics → pass all (no preference)
        if not self._student_tokens:
            supervisor.domain_validation = ValidationResult(
                passed=True, score=1.0, source="no_preference"
            )
            return True

        # Route to the appropriate scoring path
        if self.mode == "embedding_only" and self._emb_validator is not None:
            result = self._emb_validator.validate(supervisor, self._profile)
            return result.passed

        if self.mode == "hybrid" and self._emb_validator is not None:
            result = self._emb_validator.validate(supervisor, self._profile)
            return result.passed

        # keyword_only (or fallback from missing embedding_service)
        return self._keyword_validate(supervisor)

    # ------------------------------------------------------------------ #
    # Keyword-only path (original implementation, preserved exactly)      #
    # ------------------------------------------------------------------ #

    def _keyword_validate(self, supervisor: Supervisor) -> bool:
        """Original token-overlap scoring, unchanged."""
        keyword_score, kw_matches = self._keyword_score(supervisor)
        concept_score, con_matches = self._concept_score(supervisor)
        hybrid = round(_KEYWORD_WEIGHT * keyword_score + _CONCEPT_WEIGHT * concept_score, 4)
        matched = list(dict.fromkeys(kw_matches + con_matches))

        passed = hybrid >= self.min_score
        reason = None if passed else (
            f"domain_score {hybrid:.2f} below threshold {self.min_score} "
            f"(keyword={keyword_score:.2f}, concept={concept_score:.2f})"
        )

        if not passed:
            logger.info(
                "Rejected: %s (%s) — %s", supervisor.name, supervisor.institution, reason
            )

        supervisor.domain_validation = ValidationResult(
            passed=passed,
            score=hybrid,
            source="domain_hybrid",
            reason=reason,
            matched_concepts=matched,
        )
        return passed

    def _keyword_score(self, supervisor: Supervisor) -> tuple[float, list[str]]:
        sup_tokens = _tokenise_many(supervisor.research_areas)
        if not sup_tokens:
            return 0.0, []
        matches = self._student_tokens & sup_tokens
        score = len(matches) / len(self._student_tokens)
        return min(score, 1.0), sorted(matches)

    def _concept_score(self, supervisor: Supervisor) -> tuple[float, list[str]]:
        sup_tokens = _tokenise_many(supervisor.research_concepts)
        if not sup_tokens:
            return 0.0, []
        matches = self._student_tokens & sup_tokens
        score = len(matches) / len(self._student_tokens)
        return min(score, 1.0), sorted(matches)


# ------------------------------------------------------------------ #
# Helpers (module-level so they can be imported by EmbeddingDomainValidator)
# ------------------------------------------------------------------ #

def _tokenise(text: str) -> set[str]:
    """Lowercase and split a phrase into individual word tokens."""
    return set(text.lower().split())


def _tokenise_many(phrases: list[str]) -> set[str]:
    """Union of tokens from a list of phrases."""
    tokens: set[str] = set()
    for p in phrases:
        tokens |= _tokenise(p)
    return tokens
