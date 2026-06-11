"""
Embedding-based domain validator that uses semantic similarity to reduce
wrong-domain contamination caused by token overlap on surface forms.

Problem motivating this validator:
  A supervisor studying "DNA barcoding" in plant biology shares many tokens
  with a student interested in "genomic sequencing" in bioinformatics.
  Token overlap says they match; embeddings say they do not.

Scoring:
  keyword_score   — token overlap (from DomainValidator helpers)
  concept_score   — token overlap on research_concepts
  embedding_score — cosine similarity of dense text blobs
  final_score     = 0.30 * keyword + 0.30 * concept + 0.40 * embedding
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from ..models.student import StudentProfile
from ..models.supervisor import Supervisor
from ..models.validation import ValidationResult
from ..utils.logger import get_logger

if TYPE_CHECKING:
    from ..services.embedding_service import EmbeddingService

logger = get_logger(__name__)

# Hybrid weights (must sum to 1.0)
_W_KEYWORD   = 0.30
_W_CONCEPT   = 0.30
_W_EMBEDDING = 0.40

# Re-use token helpers from DomainValidator (DRY)
from ..validators.domain_validator import _tokenise_many, _tokenise


class EmbeddingDomainValidator:
    """
    Computes a three-component domain similarity score:
      keyword_score   (token overlap, student topics vs supervisor areas)
      concept_score   (token overlap, student topics vs supervisor concepts)
      embedding_score (cosine similarity of dense text blobs)

    Returns a ValidationResult with score = final_score and all
    component scores stored for logging/debugging.

    Args:
        embedding_service: Loaded EmbeddingService instance.
        min_score:         Minimum final_score to pass.
    """

    def __init__(
        self,
        embedding_service: "EmbeddingService",
        min_score: float = 0.55,
    ) -> None:
        self._emb = embedding_service
        self.min_score = min_score

    def validate(
        self,
        supervisor: Supervisor,
        student: StudentProfile,
    ) -> ValidationResult:
        """
        Compute and return a ValidationResult for this supervisor–student pair.

        Logs keyword / concept / embedding / final scores at INFO level.
        Attaches the result to supervisor.domain_validation.

        Args:
            supervisor: Enriched supervisor (evidence already collected).
            student:    Parsed student profile.

        Returns:
            ValidationResult with passed=True/False and score=final_score.
        """
        student_blob = _build_student_blob(student)
        supervisor_blob = _build_supervisor_blob(supervisor)

        # Token-overlap components
        student_tokens = _tokenise_many(student.extracted_topics + student.preferred_domains)
        kw_score, kw_matches  = _token_overlap(student_tokens, _tokenise_many(supervisor.research_areas))
        con_score, con_matches = _token_overlap(student_tokens, _tokenise_many(supervisor.research_concepts))

        # Embedding component
        if student_blob and supervisor_blob:
            emb_score = round(self._emb.similarity(student_blob, supervisor_blob), 4)
        else:
            emb_score = 0.0

        final = round(
            _W_KEYWORD * kw_score + _W_CONCEPT * con_score + _W_EMBEDDING * emb_score, 4
        )

        logger.info(
            "Domain similarity — Supervisor: %s | Keyword: %.2f | "
            "Concept: %.2f | Embedding: %.2f | Final: %.2f",
            supervisor.name, kw_score, con_score, emb_score, final,
        )

        passed = final >= self.min_score
        matched = list(dict.fromkeys(kw_matches + con_matches))
        reason = None if passed else (
            f"embedding_domain_score {final:.2f} below threshold {self.min_score} "
            f"(keyword={kw_score:.2f}, concept={con_score:.2f}, embedding={emb_score:.2f})"
        )

        if not passed:
            logger.info(
                "Rejected: %s (%s) — %s", supervisor.name, supervisor.institution, reason
            )

        result = ValidationResult(
            passed=passed,
            score=final,
            source="embedding_similarity",
            reason=reason,
            matched_concepts=matched,
        )
        supervisor.domain_validation = result
        return result


# ------------------------------------------------------------------ #
# Text-blob builders                                                   #
# ------------------------------------------------------------------ #

def _build_student_blob(student: StudentProfile) -> str:
    """
    Combine all student evidence into a single text blob for embedding.

    Sources (in priority order):
      extracted_topics, preferred_domains, statement_of_purpose snippet
    """
    parts = list(student.extracted_topics) + list(student.preferred_domains)
    if student.statement_of_purpose:
        parts.append(student.statement_of_purpose[:200])
    return " ".join(p for p in parts if p).strip()


def _build_supervisor_blob(supervisor: Supervisor) -> str:
    """
    Combine all supervisor evidence into a single text blob for embedding.

    Sources:
      research_concepts, research_areas,
      publication titles (top 5), publication concept labels
    """
    parts: list[str] = []
    parts.extend(supervisor.research_concepts)
    parts.extend(supervisor.research_areas)

    top_pubs = sorted(supervisor.publications, key=lambda p: p.citation_count, reverse=True)[:5]
    for pub in top_pubs:
        if pub.title and not pub.title.startswith("Works in"):
            parts.append(pub.title)
        parts.extend(pub.concepts)

    return " ".join(p for p in parts if p).strip()


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _token_overlap(
    student_tokens: set[str], sup_tokens: set[str]
) -> tuple[float, list[str]]:
    """Recall-oriented overlap: |intersection| / |student_tokens|."""
    if not student_tokens or not sup_tokens:
        return 0.0, []
    matches = student_tokens & sup_tokens
    score = min(len(matches) / len(student_tokens), 1.0)
    return round(score, 4), sorted(matches)
