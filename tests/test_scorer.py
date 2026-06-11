"""
Tests for RecommendationScorer.

All tests are deterministic — no network calls, no randomness.
"""

import math
import pytest
from datetime import datetime

from src.models.student import StudentProfile, AcademicBackground, ResearchInterest
from src.models.supervisor import Supervisor
from src.models.validation import ValidationResult
from src.models.recommendation import ScoreBreakdown, Recommendation
from src.scorers.recommendation_scorer import (
    RecommendationScorer,
    _overlap,
    _tokenise_many,
    _CITATION_SATURATION,
    _RECENCY_ZERO_SCORE_YEARS,
)


# ------------------------------------------------------------------ #
# Shared factories                                                     #
# ------------------------------------------------------------------ #

CURRENT_YEAR = datetime.now().year


def _profile(
    topics: list[str] | None = None,
    countries: list[str] | None = None,
    domains: list[str] | None = None,
) -> StudentProfile:
    return StudentProfile(
        name="Jane Doe",
        background=AcademicBackground(degree="MSc", field="CS", institution="MIT"),
        extracted_topics=topics if topics is not None else ["natural language processing", "machine learning", "transformers"],
        preferred_countries=countries if countries is not None else ["US", "CA"],
        preferred_domains=domains if domains is not None else ["NLP", "AI"],
    )


def _supervisor(
    country: str = "US",
    research_areas: list[str] | None = None,
    research_concepts: list[str] | None = None,
    recent_pub_count: int = 6,
    total_citations: int = 1000,
    latest_year: int | None = None,
    publications_count: int = 8,
    evidence_collected: bool = True,
    domain_score: float | None = None,
) -> Supervisor:
    latest_year = latest_year if latest_year is not None else CURRENT_YEAR - 1
    s = Supervisor(
        name="Dr. Test",
        institution="MIT",
        country=country,
        research_areas=research_areas or ["natural language processing", "machine learning"],
        research_concepts=research_concepts or ["Natural language processing", "Transformers"],
        recent_publication_count=recent_pub_count,
        total_citations=total_citations,
        latest_publication_year=latest_year,
        evidence_collected=evidence_collected,
        publications=[],  # count is mocked via publications_count below
    )
    # Inject a dummy publications list of the desired length for quality scoring
    from src.models.supervisor import Publication
    s.publications = [
        Publication(title=f"Paper {i}", year=CURRENT_YEAR - 1, citation_count=10)
        for i in range(publications_count)
    ]
    if domain_score is not None:
        s.domain_validation = ValidationResult(
            passed=True, score=domain_score, source="domain_hybrid"
        )
    return s


def _scorer(**kwargs) -> RecommendationScorer:
    return RecommendationScorer(**kwargs)


# ====================================================================
# Dimension A: Research Alignment
# ====================================================================

class TestResearchAlignment:
    def test_perfect_overlap_scores_high(self):
        scorer = _scorer()
        # domains=[] so only topics contribute to student_tokens
        profile = _profile(topics=["machine learning"], domains=[])
        s = _supervisor(research_areas=["machine learning"], research_concepts=["Machine learning"])
        score = scorer._research_alignment(s, profile)
        assert score > 0.5

    def test_no_overlap_scores_zero(self):
        scorer = _scorer()
        profile = _profile(topics=["quantum computing", "photonics"])
        s = _supervisor(research_areas=["biology"], research_concepts=["Ecology"])
        score = scorer._research_alignment(s, profile)
        assert score == 0.0

    def test_reuses_domain_validation_score(self):
        scorer = _scorer()
        profile = _profile()
        s = _supervisor(domain_score=0.8)
        score = scorer._research_alignment(s, profile)
        # domain_score * 1.5 capped at 1.0 → 1.0
        assert score == 1.0

    def test_domain_score_scaled_correctly(self):
        scorer = _scorer()
        profile = _profile()
        s = _supervisor(domain_score=0.4)
        score = scorer._research_alignment(s, profile)
        assert abs(score - min(0.4 * 1.5, 1.0)) < 1e-4

    def test_no_student_topics_returns_neutral(self):
        scorer = _scorer()
        profile = _profile(topics=[], domains=[])
        s = _supervisor()
        score = scorer._research_alignment(s, profile)
        assert score == 0.5

    def test_concept_overlap_outweighs_keyword(self):
        scorer = _scorer()
        profile = _profile(topics=["natural", "language", "processing"])
        s_keyword = _supervisor(research_areas=["natural language processing"], research_concepts=[])
        s_concept = _supervisor(research_areas=[], research_concepts=["natural language processing"])
        kw_score  = scorer._research_alignment(s_keyword, profile)
        con_score = scorer._research_alignment(s_concept, profile)
        assert con_score >= kw_score


# ====================================================================
# Dimension B: Publication Activity
# ====================================================================

class TestPublicationActivity:
    def test_recent_paper_scores_high(self):
        scorer = _scorer()
        s = _supervisor(latest_year=CURRENT_YEAR, recent_pub_count=10)
        assert scorer._publication_activity(s) == 1.0

    def test_old_paper_scores_zero_recency(self):
        scorer = _scorer()
        s = _supervisor(latest_year=CURRENT_YEAR - _RECENCY_ZERO_SCORE_YEARS, recent_pub_count=0)
        assert scorer._publication_activity(s) == 0.0

    def test_linear_decay_midpoint(self):
        scorer = _scorer()
        # Age = midpoint between full and zero → recency ≈ 0.5
        mid_age = (_RECENCY_ZERO_SCORE_YEARS + 1) // 2  # ~4-5 years
        s = _supervisor(latest_year=CURRENT_YEAR - mid_age, recent_pub_count=0)
        recency = scorer._recency_score(s.latest_publication_year)
        assert 0.0 < recency < 1.0

    def test_volume_capped_at_one(self):
        scorer = _scorer()
        s = _supervisor(recent_pub_count=100, latest_year=CURRENT_YEAR)
        assert scorer._publication_activity(s) <= 1.0

    def test_none_latest_year_returns_low(self):
        scorer = _scorer()
        s = _supervisor(latest_year=None, recent_pub_count=0)
        s.latest_publication_year = None
        assert scorer._publication_activity(s) == 0.0


# ====================================================================
# Dimension C: Citation Impact
# ====================================================================

class TestCitationImpact:
    def test_zero_citations_scores_zero(self):
        scorer = _scorer()
        s = _supervisor(total_citations=0)
        assert scorer._citation_impact(s) == 0.0

    def test_saturation_point_scores_one(self):
        scorer = _scorer()
        s = _supervisor(total_citations=_CITATION_SATURATION)
        assert scorer._citation_impact(s) == 1.0

    def test_beyond_saturation_capped_at_one(self):
        scorer = _scorer()
        s = _supervisor(total_citations=_CITATION_SATURATION * 10)
        assert scorer._citation_impact(s) == 1.0

    def test_log_normalisation_is_sublinear(self):
        """Doubling citations should not double the score (log compression)."""
        scorer = _scorer()
        s1 = _supervisor(total_citations=100)
        s2 = _supervisor(total_citations=200)
        score1 = scorer._citation_impact(s1)
        score2 = scorer._citation_impact(s2)
        assert score2 < score1 * 2  # sublinear

    def test_moderate_citations_in_range(self):
        scorer = _scorer()
        s = _supervisor(total_citations=500)
        score = scorer._citation_impact(s)
        assert 0.0 < score < 1.0


# ====================================================================
# Dimension D: Evidence Quality
# ====================================================================

class TestEvidenceQuality:
    def test_no_evidence_collected_scores_zero(self):
        scorer = _scorer()
        s = _supervisor(evidence_collected=False)
        assert scorer._evidence_quality(s) == 0.0

    def test_full_publications_and_concepts_scores_high(self):
        scorer = _scorer()
        from src.models.supervisor import Publication
        s = _supervisor(publications_count=10, evidence_collected=True)
        s.research_concepts = [f"Concept {i}" for i in range(15)]
        assert scorer._evidence_quality(s) == 1.0

    def test_partial_evidence_in_range(self):
        scorer = _scorer()
        s = _supervisor(publications_count=5)
        s.research_concepts = ["NLP", "ML", "AI"]
        score = scorer._evidence_quality(s)
        assert 0.0 < score < 1.0

    def test_score_increases_with_more_publications(self):
        scorer = _scorer()
        s_few = _supervisor(publications_count=2)
        s_many = _supervisor(publications_count=8)
        assert scorer._evidence_quality(s_many) > scorer._evidence_quality(s_few)


# ====================================================================
# Dimension E: Country Preference
# ====================================================================

class TestCountryPreference:
    def test_top_choice_country_scores_one(self):
        scorer = _scorer()
        s = _supervisor(country="US")
        assert scorer._country_preference(s, _profile(countries=["US", "CA"])) == 1.0

    def test_second_choice_scores_point_eight(self):
        scorer = _scorer()
        s = _supervisor(country="CA")
        assert scorer._country_preference(s, _profile(countries=["US", "CA"])) == 0.8

    def test_non_preferred_scores_zero(self):
        scorer = _scorer()
        s = _supervisor(country="DE")
        assert scorer._country_preference(s, _profile(countries=["US", "CA"])) == 0.0

    def test_no_preference_scores_one(self):
        scorer = _scorer()
        s = _supervisor(country="AU")
        assert scorer._country_preference(s, _profile(countries=[])) == 1.0

    def test_normalised_country_matching(self):
        scorer = _scorer()
        s = _supervisor(country="United States")
        assert scorer._country_preference(s, _profile(countries=["US"])) == 1.0


# ====================================================================
# Full score() method
# ====================================================================

class TestScoreMethod:
    def test_returns_score_breakdown(self):
        scorer = _scorer()
        bd = scorer.score(_supervisor(), _profile())
        assert isinstance(bd, ScoreBreakdown)

    def test_overall_score_in_range(self):
        scorer = _scorer()
        bd = scorer.score(_supervisor(), _profile())
        assert 0.0 <= bd.overall_score <= 1.0

    def test_overall_score_is_weighted_sum(self):
        scorer = _scorer()
        s = _supervisor()
        p = _profile()
        bd = scorer.score(s, p)
        expected = round(
            0.40 * bd.research_alignment +
            0.20 * bd.publication_activity +
            0.15 * bd.citation_impact +
            0.15 * bd.evidence_quality +
            0.10 * bd.country_preference,
            4,
        )
        assert abs(bd.overall_score - expected) < 1e-4

    def test_deterministic_same_inputs(self):
        """Identical inputs must produce identical scores across calls."""
        scorer = _scorer()
        s = _supervisor()
        p = _profile()
        bd1 = scorer.score(s, p)
        bd2 = scorer.score(s, p)
        assert bd1.overall_score == bd2.overall_score
        assert bd1 == bd2

    def test_custom_weights_applied(self):
        weights = {
            "research_alignment": 1.0,
            "publication_activity": 0.0,
            "citation_impact": 0.0,
            "evidence_quality": 0.0,
            "country_preference": 0.0,
        }
        scorer = _scorer(weights=weights)
        s = _supervisor(domain_score=0.9)
        bd = scorer.score(s, _profile())
        assert abs(bd.overall_score - bd.research_alignment) < 1e-4


# ====================================================================
# score_all(): ordering and Recommendation generation
# ====================================================================

class TestScoreAll:
    def _make_supervisors(self) -> list[Supervisor]:
        return [
            _supervisor(country="US", total_citations=3000, recent_pub_count=8,
                        domain_score=0.9, latest_year=CURRENT_YEAR),
            _supervisor(country="CA", total_citations=200, recent_pub_count=4,
                        domain_score=0.5, latest_year=CURRENT_YEAR - 2),
            _supervisor(country="AU", total_citations=50, recent_pub_count=2,
                        domain_score=0.2, latest_year=CURRENT_YEAR - 5),
        ]

    def test_returns_recommendation_objects(self):
        scorer = _scorer()
        recs = scorer.score_all(self._make_supervisors(), _profile())
        assert all(isinstance(r, Recommendation) for r in recs)

    def test_sorted_descending_by_score(self):
        scorer = _scorer()
        recs = scorer.score_all(self._make_supervisors(), _profile())
        scores = [r.score for r in recs]
        assert scores == sorted(scores, reverse=True)

    def test_ranks_are_sequential(self):
        scorer = _scorer()
        recs = scorer.score_all(self._make_supervisors(), _profile())
        assert [r.rank for r in recs] == list(range(1, len(recs) + 1))

    def test_empty_list_returns_empty(self):
        assert _scorer().score_all([], _profile()) == []

    def test_score_attached_to_recommendation(self):
        scorer = _scorer()
        recs = scorer.score_all(self._make_supervisors(), _profile())
        for r in recs:
            assert r.score == r.score_breakdown.overall_score


# ====================================================================
# Tier assignment
# ====================================================================

class TestTierAssignment:
    def test_high_score_is_reach(self):
        scorer = _scorer(reach_threshold=0.70, safety_threshold=0.45)
        assert scorer._assign_tier(0.85) == "reach"
        assert scorer._assign_tier(0.70) == "reach"  # boundary inclusive

    def test_low_score_is_safety(self):
        scorer = _scorer(reach_threshold=0.70, safety_threshold=0.45)
        assert scorer._assign_tier(0.30) == "safety"
        assert scorer._assign_tier(0.44) == "safety"  # just below threshold

    def test_mid_score_is_target(self):
        scorer = _scorer(reach_threshold=0.70, safety_threshold=0.45)
        assert scorer._assign_tier(0.60) == "target"
        assert scorer._assign_tier(0.45) == "target"  # boundary inclusive

    def test_tiers_attached_to_recommendations(self):
        scorer = _scorer(reach_threshold=0.70, safety_threshold=0.45)
        supervisors = [
            _supervisor(total_citations=4000, domain_score=0.95, recent_pub_count=10,
                        latest_year=CURRENT_YEAR, publications_count=10),  # high score → reach
            _supervisor(total_citations=10, domain_score=0.0, recent_pub_count=0,
                        latest_year=CURRENT_YEAR - 7, publications_count=0,
                        evidence_collected=False),  # low score → safety
        ]
        recs = scorer.score_all(supervisors, _profile(countries=[]))
        tiers = {r.tier for r in recs}
        assert "reach" in tiers or "target" in tiers  # at least one non-safety

    def test_configurable_thresholds(self):
        scorer = _scorer(reach_threshold=0.90, safety_threshold=0.10)
        # With very tight reach threshold, a 0.80 score becomes target
        assert scorer._assign_tier(0.80) == "target"


# ====================================================================
# Helper function tests
# ====================================================================

class TestHelpers:
    def test_overlap_empty_a(self):
        assert _overlap(set(), {"a", "b"}) == 0.0

    def test_overlap_full(self):
        assert _overlap({"a", "b"}, {"a", "b", "c"}) == 1.0

    def test_overlap_partial(self):
        result = _overlap({"a", "b", "c"}, {"a"})
        assert abs(result - 1 / 3) < 1e-6

    def test_tokenise_many_deduplicates(self):
        tokens = _tokenise_many(["machine learning", "machine vision"])
        assert "machine" in tokens
        assert len([t for t in tokens if t == "machine"]) == 1
