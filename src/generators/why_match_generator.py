"""
Generates personalised 2–3 sentence explanations for each recommendation.

Flow per recommendation:
  1. Build a structured prompt from student + supervisor evidence.
  2. Call LLMClient.generate(prompt).
  3. Validate output: must be ≤ max_words and non-empty.
  4. On any failure → deterministic fallback (no LLM, no empty string).
  5. Write result to recommendation.why_match.

Cost control: only the top `top_n` recommendations receive LLM calls.
Lower-ranked recommendations receive the deterministic fallback instead.
"""

from pathlib import Path
from typing import Optional

from ..models.student import StudentProfile
from ..models.supervisor import Supervisor
from ..models.recommendation import Recommendation
from ..generators.llm_client import LLMClient
from ..utils.logger import get_logger

logger = get_logger(__name__)

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "why_match_prompt.txt"
_TEMPLATE: Optional[str] = None   # loaded once on first use


def _load_template() -> str:
    global _TEMPLATE
    if _TEMPLATE is None:
        _TEMPLATE = _TEMPLATE_PATH.read_text(encoding="utf-8")
    return _TEMPLATE


class WhyMatchGenerator:
    """
    Writes a personalised why_match string onto each Recommendation.

    Args:
        llm_client:  Any object satisfying the LLMClient Protocol.
                     Pass None to run in fallback-only mode (no API calls).
        top_n:       Maximum number of recommendations to send to the LLM.
                     Recommendations beyond this rank receive the fallback.
        max_words:   Hard cap on output length (words). Outputs exceeding
                     this are replaced by the fallback silently.
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        top_n: int = 50,
        max_words: int = 120,
    ) -> None:
        self.llm_client = llm_client
        self.top_n = top_n
        self.max_words = max_words

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def generate(self, student: StudentProfile, supervisor: Supervisor) -> str:
        """
        Generate a why_match explanation for one student–supervisor pair.

        Tries the LLM first; falls back to the deterministic template on
        any failure or if no LLM client is configured.

        Args:
            student:    Parsed student profile.
            supervisor: Enriched, validated supervisor.

        Returns:
            Non-empty explanation string (guaranteed).
        """
        if self.llm_client is not None:
            try:
                prompt = self._build_prompt(student, supervisor)
                raw = self.llm_client.generate(prompt)
                cleaned = raw.strip()
                if cleaned and _word_count(cleaned) <= self.max_words:
                    logger.info("Generated why_match for %s", supervisor.name)
                    return cleaned
                logger.warning(
                    "LLM output for %s exceeded %d words or was empty — using fallback",
                    supervisor.name, self.max_words,
                )
            except Exception as exc:
                logger.warning(
                    "LLM call failed for %s (%s) — using fallback", supervisor.name, exc
                )

        text = self._fallback(student, supervisor)
        logger.info("Fallback why_match used for %s", supervisor.name)
        return text

    def generate_batch(
        self,
        student: StudentProfile,
        recommendations: list[Recommendation],
    ) -> list[Recommendation]:
        """
        Populate why_match on every Recommendation in the list.

        Only the top `self.top_n` recommendations (by existing rank) receive
        LLM calls. The rest receive the deterministic fallback.
        Scoring and ranking are not modified.

        Args:
            student:         Parsed student profile.
            recommendations: Already-ranked Recommendation list (rank 1 = best).

        Returns:
            The same list with why_match populated on every entry.
        """
        for rec in recommendations:
            use_llm = self.llm_client is not None and rec.rank <= self.top_n
            if use_llm:
                rec.why_match = self.generate(student, rec.supervisor)
            else:
                rec.why_match = self._fallback(student, rec.supervisor)
                logger.info(
                    "Fallback why_match used for %s (rank %d > top_n %d)",
                    rec.supervisor.name, rec.rank, self.top_n,
                )
        return recommendations

    # ------------------------------------------------------------------ #
    # Prompt construction                                                  #
    # ------------------------------------------------------------------ #

    def _build_prompt(self, student: StudentProfile, supervisor: Supervisor) -> str:
        """
        Populate the template with evidence from student and supervisor.

        Student fields injected:
          - extracted_topics (top 5)
          - preferred_domains (as proxy for skills if no dedicated field)
          - statement_of_purpose snippet (first 120 chars)

        Supervisor fields injected:
          - research_concepts (top 6)
          - publications (top 3 by citation_count, with year)
          - institution
        """
        template = _load_template()

        student_interests = "; ".join(student.extracted_topics[:5]) or "not specified"
        student_skills    = "; ".join(student.preferred_domains[:5]) or "not specified"
        student_projects  = (
            (student.statement_of_purpose or "")[:120].strip() or "not specified"
        )

        sup_concepts = "; ".join(supervisor.research_concepts[:6]) or (
            "; ".join(supervisor.research_areas[:6]) or "not specified"
        )

        top_pubs = sorted(
            supervisor.publications, key=lambda p: p.citation_count, reverse=True
        )[:3]
        pub_lines = "\n".join(
            f'  - "{p.title}" ({p.year}, {p.citation_count} citations)'
            for p in top_pubs
        ) or "  - no publications available"

        return template.format(
            student_name=student.name,
            student_interests=student_interests,
            student_skills=student_skills,
            student_projects=student_projects,
            supervisor_name=supervisor.name,
            supervisor_institution=supervisor.institution,
            supervisor_concepts=sup_concepts,
            supervisor_publications=pub_lines,
        )

    # ------------------------------------------------------------------ #
    # Deterministic fallback                                               #
    # ------------------------------------------------------------------ #

    def _fallback(self, student: StudentProfile, supervisor: Supervisor) -> str:
        """
        Build a deterministic explanation without any LLM call.

        Uses:
          - Top matched concept (overlap between student topics and supervisor concepts)
          - Top publication title (highest citation count)
          - Student's first research interest

        Always returns a non-empty string.
        """
        # Find overlapping concept
        student_tokens = set(" ".join(student.extracted_topics).lower().split())
        matched_concept = next(
            (c for c in supervisor.research_concepts
             if any(tok in c.lower() for tok in student_tokens)),
            supervisor.research_concepts[0] if supervisor.research_concepts
            else (supervisor.research_areas[0] if supervisor.research_areas else ""),
        )

        # Top publication
        top_pub = max(supervisor.publications, key=lambda p: p.citation_count, default=None)
        pub_ref = (
            f'"{top_pub.title}" ({top_pub.year})'
            if top_pub and top_pub.title and not top_pub.title.startswith("Works in")
            else ""
        )

        # Student anchor
        student_anchor = (
            student.extracted_topics[0]
            if student.extracted_topics
            else student.preferred_domains[0] if student.preferred_domains
            else "your research interests"
        )

        # Assemble
        parts: list[str] = []
        if matched_concept:
            parts.append(
                f"{supervisor.name} at {supervisor.institution} works on "
                f"{matched_concept}, which directly relates to your interest in "
                f"{student_anchor}."
            )
        else:
            parts.append(
                f"{supervisor.name} at {supervisor.institution} conducts research "
                f"aligned with your interest in {student_anchor}."
            )

        if pub_ref:
            parts.append(
                f"Their publication {pub_ref} demonstrates active work in this area."
            )

        return " ".join(parts)


# ------------------------------------------------------------------ #
# Pure helpers                                                         #
# ------------------------------------------------------------------ #

def _word_count(text: str) -> int:
    """Count whitespace-separated words in a string."""
    return len(text.split())
