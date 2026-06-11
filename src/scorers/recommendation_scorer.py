"""
Deterministic composite scorer for supervisor–student pairs.

Dimensions and weights:
  research_alignment   40%  — topic/concept overlap with student interests
  publication_activity 20%  — recency and volume of recent output
  citation_impact      15%  — log-normalised total citations
  evidence_quality     15%  — publication count + concept diversity
  country_preference   10%  — ranked country match

All inputs come from fields already on Supervisor (populated by previous
pipeline stages). No LLM calls, no randomness — identical inputs always
produce identical scores.
"""

import math
from datetime import datetime
from typing import NamedTuple, TYPE_CHECKING

if TYPE_CHECKING:
    from ..feedback.outcome_learner import OutcomeLearner

from ..models.student import StudentProfile
from ..models.supervisor import Supervisor
from ..models.recommendation import Recommendation, ScoreBreakdown
from ..utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Weights — must sum to 1.0
# ---------------------------------------------------------------------------
_WEIGHTS: dict[str, float] = {
    "research_alignment":   0.40,
    "publication_activity": 0.20,
    "citation_impact":      0.15,
    "evidence_quality":     0.15,
    "country_preference":   0.10,
}

# Publication activity scoring constants
_RECENCY_FULL_SCORE_YEARS = 1    # published ≤ 1 year ago → full recency score
_RECENCY_ZERO_SCORE_YEARS = 8    # published ≥ 8 years ago → 0 recency score
_MAX_RECENT_PUBS_FOR_FULL = 10   # ≥ 10 recent pubs → full activity score

# Citation impact: log-normalise; ln(this value) → 1.0
_CITATION_SATURATION = 5000

# Evidence quality constants
_MAX_PUBS_FOR_FULL_QUALITY = 10
_MAX_CONCEPTS_FOR_FULL_DIVERSITY = 15

# Country preference rank scores
_COUNTRY_RANK_SCORES = {0: 1.0, 1: 0.8}   # index 0 = top choice


class RecommendationScorer:
    """
    Scores each validated supervisor against a student profile and
    returns a ranked, tier-labelled list of Recommendation objects.

    Args:
        reach_threshold:  Overall score ≥ this → "reach"   (default top 20%)
        safety_threshold: Overall score < this → "safety"  (default bottom 30%)
        weights:          Override dimension weights (must sum to 1.0).
    """

    def __init__(
        self,
        reach_threshold: float = 0.70,
        safety_threshold: float = 0.45,
        weights: dict[str, float] | None = None,
        outcome_learner: "OutcomeLearner | None" = None,
        feedback_weight: float = 0.15,
    ) -> None:
        self.reach_threshold = reach_threshold
        self.safety_threshold = safety_threshold
        self.weights = weights or _WEIGHTS
        self.outcome_learner = outcome_learner
        self.feedback_weight = feedback_weight
        self._current_year = datetime.now().year

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def score(self, supervisor: Supervisor, profile: StudentProfile) -> ScoreBreakdown:
        """
        Compute the full score breakdown for one supervisor–student pair.

        Args:
            supervisor: Enriched, validated supervisor.
            profile:    Parsed student profile.

        Returns:
            ScoreBreakdown with overall_score and all 5 sub-scores.
        """
        ra  = self._research_alignment(supervisor, profile)
        pa  = self._publication_activity(supervisor)
        ci  = self._citation_impact(supervisor)
        eq  = self._evidence_quality(supervisor)
        cp  = self._country_preference(supervisor, profile)

        overall = round(
            self.weights["research_alignment"]   * ra +
            self.weights["publication_activity"] * pa +
            self.weights["citation_impact"]      * ci +
            self.weights["evidence_quality"]     * eq +
            self.weights["country_preference"]   * cp,
            4,
        )

        return ScoreBreakdown(
            overall_score=overall,
            research_alignment=round(ra, 4),
            publication_activity=round(pa, 4),
            citation_impact=round(ci, 4),
            evidence_quality=round(eq, 4),
            country_preference=round(cp, 4),
        )

    def score_all(
        self,
        supervisors: list[Supervisor],
        profile: StudentProfile,
    ) -> list[Recommendation]:
        """
        Score every supervisor, sort descending by overall_score, assign
        tiers and ranks, and return Recommendation objects.

        Args:
            supervisors: All validation-passing supervisors.
            profile:     Student profile.

        Returns:
            Sorted list of Recommendation objects (rank 1 = best match).
        """
        breakdowns: list[tuple[Supervisor, ScoreBreakdown]] = []
        for s in supervisors:
            bd = self.score(s, profile)
            logger.info("Scored %s = %.4f", s.name, bd.overall_score)
            breakdowns.append((s, bd))

        # Apply historical feedback adjustment if learner is available
        adjusted: list[tuple[Supervisor, ScoreBreakdown, float]] = []
        for s, bd in breakdowns:
            final = self._apply_feedback_adjustment(s, bd.overall_score)
            adjusted.append((s, bd, final))

        # Sort descending by adjusted score (name as stable tiebreak)
        adjusted.sort(key=lambda x: (-x[2], x[0].name))

        if adjusted:
            logger.info("Top recommendation: %s (%.4f)", adjusted[0][0].name, adjusted[0][2])

        recommendations: list[Recommendation] = []
        for rank, (supervisor, bd, adj_score) in enumerate(adjusted, start=1):
            tier = self._assign_tier(adj_score)
            stats = (
                self.outcome_learner.get_supervisor_stats(supervisor.openalex_id or "")
                if self.outcome_learner and supervisor.openalex_id
                else None
            )
            recommendations.append(Recommendation(
                rank=rank,
                supervisor=supervisor,
                score=adj_score,
                score_breakdown=bd,
                tier=tier,
                historical_success_score=stats.success_score if stats else None,
                historical_email_count=stats.total_emails if stats else None,
            ))

        return recommendations

    # ------------------------------------------------------------------ #
    # Dimension A: Research Alignment (40%)                               #
    # ------------------------------------------------------------------ #

    def _research_alignment(self, supervisor: Supervisor, profile: StudentProfile) -> float:
        """
        Hybrid keyword + concept overlap between student topics and
        supervisor's research_areas + research_concepts.

        If domain_validation is already attached (by DomainValidator),
        reuse its score (avoids duplicating tokenisation logic).
        Otherwise compute from scratch.

        Returns float in [0, 1].
        """
        # Fast path: reuse DomainValidator score if available
        if supervisor.domain_validation and supervisor.domain_validation.passed:
            return min(supervisor.domain_validation.score * 1.5, 1.0)

        if not profile.extracted_topics:
            return 0.5  # no student topics → neutral

        student_tokens = _tokenise_many(profile.extracted_topics + profile.preferred_domains)
        if not student_tokens:
            return 0.5

        kw_tokens   = _tokenise_many(supervisor.research_areas)
        con_tokens  = _tokenise_many(supervisor.research_concepts)

        kw_score  = _overlap(student_tokens, kw_tokens)
        con_score = _overlap(student_tokens, con_tokens)

        return round(0.4 * kw_score + 0.6 * con_score, 4)

    # ------------------------------------------------------------------ #
    # Dimension B: Publication Activity (20%)                             #
    # ------------------------------------------------------------------ #

    def _publication_activity(self, supervisor: Supervisor) -> float:
        """
        Combines recency of latest publication with volume of recent output.
        Both sub-scores are averaged equally.

        Returns float in [0, 1].
        """
        recency = self._recency_score(supervisor.latest_publication_year)
        volume  = min(supervisor.recent_publication_count / _MAX_RECENT_PUBS_FOR_FULL, 1.0)
        return round((recency + volume) / 2, 4)

    def _recency_score(self, latest_year: int | None) -> float:
        """Linear decay: 1.0 if ≤ 1 year old, 0.0 if ≥ 8 years old."""
        if not latest_year:
            return 0.0
        age = self._current_year - latest_year
        if age <= _RECENCY_FULL_SCORE_YEARS:
            return 1.0
        if age >= _RECENCY_ZERO_SCORE_YEARS:
            return 0.0
        window = _RECENCY_ZERO_SCORE_YEARS - _RECENCY_FULL_SCORE_YEARS
        return round(1.0 - (age - _RECENCY_FULL_SCORE_YEARS) / window, 4)

    # ------------------------------------------------------------------ #
    # Dimension C: Citation Impact (15%)                                  #
    # ------------------------------------------------------------------ #

    def _citation_impact(self, supervisor: Supervisor) -> float:
        """
        Log-normalise total_citations against a saturation point to prevent
        highly-cited professors from overwhelming everyone else.

        score = ln(1 + citations) / ln(1 + saturation)

        Returns float in [0, 1].
        """
        if supervisor.total_citations <= 0:
            return 0.0
        score = math.log1p(supervisor.total_citations) / math.log1p(_CITATION_SATURATION)
        return round(min(score, 1.0), 4)

    # ------------------------------------------------------------------ #
    # Dimension D: Evidence Quality (15%)                                 #
    # ------------------------------------------------------------------ #

    def _evidence_quality(self, supervisor: Supervisor) -> float:
        """
        Measures how richly the supervisor is evidenced:
          - publication count (depth of record)
          - concept diversity (breadth of research coverage)
        Both sub-scores averaged equally.

        Returns float in [0, 1].
        """
        if not supervisor.evidence_collected:
            return 0.0

        pub_score = min(len(supervisor.publications) / _MAX_PUBS_FOR_FULL_QUALITY, 1.0)
        div_score = min(len(supervisor.research_concepts) / _MAX_CONCEPTS_FOR_FULL_DIVERSITY, 1.0)
        return round((pub_score + div_score) / 2, 4)

    # ------------------------------------------------------------------ #
    # Dimension E: Country Preference (10%)                               #
    # ------------------------------------------------------------------ #

    def _country_preference(self, supervisor: Supervisor, profile: StudentProfile) -> float:
        """
        Ranked preference score:
          Top choice country     → 1.0
          Second choice country  → 0.8
          Any other preferred    → 0.6
          No preference set      → 1.0 (neutral)
          Not in preferred list  → 0.0

        Uses the normalised country already on country_validation if present.
        """
        from ..validators.country_validator import _normalise

        preferred = profile.preferred_countries
        if not preferred:
            return 1.0

        sup_country = _normalise(supervisor.country or "")
        for idx, raw in enumerate(preferred):
            if _normalise(raw) == sup_country:
                return _COUNTRY_RANK_SCORES.get(idx, 0.6)
        return 0.0

    # ------------------------------------------------------------------ #
    # Feedback adjustment                                                  #
    # ------------------------------------------------------------------ #

    def _apply_feedback_adjustment(self, supervisor: Supervisor, base_score: float) -> float:
        """
        Blend the base score with the supervisor's historical success_score.

        adjusted = (1 - feedback_weight) * base_score
                 + feedback_weight       * success_score

        If no historical data exists for this supervisor, returns base_score
        unchanged so the pipeline behaves exactly as before.

        Args:
            supervisor: Supervisor (must have openalex_id for lookup).
            base_score: The raw composite score from the 5 dimensions.

        Returns:
            Adjusted float in [0, 1].
        """
        if self.outcome_learner is None or not supervisor.openalex_id:
            return base_score

        success_score = self.outcome_learner.get_supervisor_score(supervisor.openalex_id)
        if success_score is None:
            return base_score   # no history → no adjustment

        adjusted = round(
            (1 - self.feedback_weight) * base_score
            + self.feedback_weight * success_score,
            4,
        )
        logger.info(
            "Feedback adjustment for %s: base=%.4f success=%.4f adjusted=%.4f",
            supervisor.name, base_score, success_score, adjusted,
        )
        return adjusted

    # ------------------------------------------------------------------ #
    # Tier assignment                                                      #
    # ------------------------------------------------------------------ #

    def _assign_tier(self, overall_score: float) -> str:
        """
        Assign a qualitative tier based on absolute score thresholds.
        Thresholds are configurable at construction time.

          score ≥ reach_threshold   → "reach"
          score < safety_threshold  → "safety"
          otherwise                 → "target"
        """
        if overall_score >= self.reach_threshold:
            return "reach"
        if overall_score < self.safety_threshold:
            return "safety"
        return "target"


# ---------------------------------------------------------------------------
# Module-level pure helpers (importable by tests)
# ---------------------------------------------------------------------------

def _tokenise(text: str) -> set[str]:
    """Lowercase and split a phrase into word tokens."""
    return set(text.lower().split())


def _tokenise_many(phrases: list[str]) -> set[str]:
    """Union of tokens from a list of phrases."""
    tokens: set[str] = set()
    for p in phrases:
        tokens |= _tokenise(p)
    return tokens


def _overlap(a: set[str], b: set[str]) -> float:
    """Recall-oriented overlap: |a ∩ b| / |a|. Returns 0.0 if a is empty."""
    if not a:
        return 0.0
    return min(len(a & b) / len(a), 1.0)
