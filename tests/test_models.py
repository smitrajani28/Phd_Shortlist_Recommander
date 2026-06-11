"""Unit tests for Pydantic data models."""

import pytest
from pydantic import ValidationError
from src.models.student import StudentProfile, AcademicBackground, ResearchInterest
from src.models.supervisor import Supervisor
from src.models.recommendation import Recommendation, ShortlistOutput, ScoreBreakdown


def _breakdown(**overrides) -> ScoreBreakdown:
    defaults = dict(
        overall_score=0.8,
        research_alignment=0.8,
        publication_activity=0.9,
        citation_impact=0.7,
        evidence_quality=0.8,
        country_preference=1.0,
    )
    return ScoreBreakdown(**{**defaults, **overrides})


class TestStudentProfile:
    def test_valid_profile_is_created(self, sample_profile):
        assert sample_profile.name == "Jane Doe"

    def test_research_interest_weight_bounds(self):
        with pytest.raises(ValidationError):
            ResearchInterest(topic="NLP", weight=1.5)

    def test_gpa_bounds(self):
        with pytest.raises(ValidationError):
            AcademicBackground(degree="BSc", field="CS", institution="MIT", gpa=5.0)


class TestSupervisor:
    def test_supervisor_accepts_empty_publications(self):
        s = Supervisor(name="Dr. X", institution="MIT", country="US")
        assert s.recent_publications == []


class TestRecommendation:
    def test_rank_must_be_positive(self, sample_supervisor):
        with pytest.raises(ValidationError):
            Recommendation(
                rank=0,
                supervisor=sample_supervisor,
                score=0.8,
                score_breakdown=_breakdown(),
            )

    def test_score_bounds(self, sample_supervisor):
        with pytest.raises(ValidationError):
            Recommendation(
                rank=1,
                supervisor=sample_supervisor,
                score=1.5,
                score_breakdown=_breakdown(),
            )

    def test_score_breakdown_fields_present(self):
        bd = _breakdown()
        assert hasattr(bd, "overall_score")
        assert hasattr(bd, "research_alignment")
        assert hasattr(bd, "publication_activity")
        assert hasattr(bd, "citation_impact")
        assert hasattr(bd, "evidence_quality")
        assert hasattr(bd, "country_preference")
