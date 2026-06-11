"""
Tests for the full validation layer:
  CountryValidator, DomainValidator, EvidenceValidator,
  ValidationResult model, and pipeline validation counts.
"""

import pytest
from src.models.student import StudentProfile, AcademicBackground, ResearchInterest
from src.models.supervisor import Supervisor, Publication
from src.models.validation import ValidationResult
from src.validators.country_validator import CountryValidator, _normalise
from src.validators.domain_validator import DomainValidator, _tokenise_many
from src.validators.evidence_validator import EvidenceValidator


# ------------------------------------------------------------------ #
# Shared factories                                                     #
# ------------------------------------------------------------------ #

def _profile(
    countries: list[str] | None = None,
    domains: list[str] | None = None,
    topics: list[str] | None = None,
) -> StudentProfile:
    return StudentProfile(
        name="Jane Doe",
        background=AcademicBackground(degree="MSc", field="CS", institution="MIT"),
        preferred_countries=countries or [],
        preferred_domains=domains or [],
        extracted_topics=topics or [],
    )


def _supervisor(
    country: str = "US",
    research_areas: list[str] | None = None,
    research_concepts: list[str] | None = None,
    recent_pub_count: int = 5,
    total_citations: int = 200,
    latest_year: int = 2024,
    evidence_collected: bool = True,
) -> Supervisor:
    return Supervisor(
        name="Dr. Test",
        institution="Test University",
        country=country,
        research_areas=research_areas or [],
        research_concepts=research_concepts or [],
        recent_publication_count=recent_pub_count,
        total_citations=total_citations,
        latest_publication_year=latest_year,
        evidence_collected=evidence_collected,
    )


# ====================================================================
# ValidationResult model
# ====================================================================

class TestValidationResult:
    def test_defaults(self):
        r = ValidationResult(passed=True)
        assert r.score == 1.0
        assert r.reason is None
        assert r.matched_concepts == []

    def test_rejection_stores_reason(self):
        r = ValidationResult(passed=False, score=0.0, reason="no match")
        assert r.passed is False
        assert r.reason == "no match"

    def test_score_bounds_enforced(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ValidationResult(passed=True, score=1.5)


# ====================================================================
# CountryValidator
# ====================================================================

class TestCountryNormalise:
    @pytest.mark.parametrize("raw,expected", [
        ("US", "US"), ("us", "US"), ("GB", "GB"),
        ("United Kingdom", "GB"), ("uk", "GB"),
        ("USA", "US"), ("united states", "US"),
        ("Germany", "DE"), ("Canada", "CA"),
        ("u.k.", "GB"), ("u.s.", "US"),
    ])
    def test_normalise(self, raw: str, expected: str):
        assert _normalise(raw) == expected

    def test_unknown_code_returned_uppercase(self):
        assert _normalise("zz") == "ZZ"


class TestCountryValidator:
    def test_passes_when_no_preference(self):
        v = CountryValidator(_profile(countries=[]))
        s = _supervisor(country="DE")
        assert v.validate(s) is True
        assert s.country_validation.passed is True
        assert s.country_validation.source == "no_preference"

    def test_passes_exact_match(self):
        v = CountryValidator(_profile(countries=["US", "CA"]))
        assert v.validate(_supervisor(country="US")) is True

    def test_passes_after_normalisation(self):
        # Profile stores "GB", supervisor has "United Kingdom"
        v = CountryValidator(_profile(countries=["GB"]))
        assert v.validate(_supervisor(country="United Kingdom")) is True

    def test_rejects_non_preferred_country(self):
        v = CountryValidator(_profile(countries=["US", "CA"]))
        s = _supervisor(country="DE")
        assert v.validate(s) is False

    def test_rejection_attaches_metadata(self):
        v = CountryValidator(_profile(countries=["US"]))
        s = _supervisor(country="FR")
        v.validate(s)
        assert s.country_validation.passed is False
        assert "FR" in s.country_validation.reason
        assert "US" in s.country_validation.reason

    def test_rejects_empty_country(self):
        v = CountryValidator(_profile(countries=["US"]))
        s = _supervisor(country="")
        assert v.validate(s) is False

    def test_case_insensitive_profile_country(self):
        v = CountryValidator(_profile(countries=["us", "ca"]))
        assert v.validate(_supervisor(country="US")) is True


# ====================================================================
# DomainValidator
# ====================================================================

class TestDomainValidator:
    def test_passes_when_no_student_topics(self):
        v = DomainValidator(_profile(domains=[], topics=[]))
        s = _supervisor()
        assert v.validate(s) is True
        assert s.domain_validation.source == "no_preference"

    def test_passes_with_strong_keyword_overlap(self):
        v = DomainValidator(
            _profile(topics=["natural language processing", "machine learning"]),
            min_score=0.2,
        )
        s = _supervisor(research_areas=["natural language processing", "deep learning"])
        assert v.validate(s) is True

    def test_passes_with_strong_concept_overlap(self):
        v = DomainValidator(
            _profile(topics=["natural language processing", "transformers"]),
            min_score=0.2,
        )
        s = _supervisor(
            research_areas=[],
            research_concepts=["Natural language processing", "Transformers", "NLP"],
        )
        assert v.validate(s) is True

    def test_rejects_below_threshold(self):
        v = DomainValidator(
            _profile(topics=["quantum computing", "photonics"]),
            min_score=0.35,
        )
        s = _supervisor(
            research_areas=["biology", "chemistry"],
            research_concepts=["Ecology"],
        )
        assert v.validate(s) is False

    def test_rejection_attaches_metadata(self):
        v = DomainValidator(_profile(topics=["nlp"]), min_score=0.99)
        s = _supervisor(research_areas=["biology"])
        v.validate(s)
        assert s.domain_validation.passed is False
        assert s.domain_validation.score < 0.99
        assert "domain_score" in s.domain_validation.reason

    def test_matched_concepts_populated(self):
        v = DomainValidator(
            _profile(topics=["machine learning", "nlp"]),
            min_score=0.0,
        )
        s = _supervisor(research_areas=["machine learning", "computer vision"])
        v.validate(s)
        assert "machine" in s.domain_validation.matched_concepts or \
               "learning" in s.domain_validation.matched_concepts

    def test_score_stored_on_supervisor(self):
        v = DomainValidator(_profile(topics=["nlp"]), min_score=0.0)
        s = _supervisor(research_areas=["nlp research"])
        v.validate(s)
        assert 0.0 <= s.domain_validation.score <= 1.0

    def test_hybrid_weights_concept_over_keyword(self):
        """Concept overlap (weight 0.6) should dominate keyword overlap (0.4)."""
        topics = ["natural", "language", "processing"]
        v = DomainValidator(_profile(topics=topics), min_score=0.0)
        s_keyword = _supervisor(research_areas=["natural language processing"], research_concepts=[])
        s_concept = _supervisor(research_areas=[], research_concepts=["natural language processing"])
        v.validate(s_keyword)
        v.validate(s_concept)
        # concept score should be >= keyword score due to higher weight
        assert s_concept.domain_validation.score >= s_keyword.domain_validation.score


# ====================================================================
# EvidenceValidator
# ====================================================================

class TestEvidenceValidator:
    def test_rejects_when_evidence_not_collected(self):
        v = EvidenceValidator()
        s = _supervisor(evidence_collected=False)
        assert v.validate(s) is False
        assert "evidence not collected" in s.evidence_validation.reason

    def test_passes_all_thresholds(self):
        v = EvidenceValidator(
            min_recent_publications=3,
            min_total_citations=50,
            max_publication_gap=5,
        )
        s = _supervisor(recent_pub_count=5, total_citations=200, latest_year=2024)
        assert v.validate(s) is True
        assert s.evidence_validation.passed is True

    def test_rejects_insufficient_recent_publications(self):
        v = EvidenceValidator(min_recent_publications=5)
        s = _supervisor(recent_pub_count=2, total_citations=200, latest_year=2024)
        assert v.validate(s) is False
        assert "recent_publications" in s.evidence_validation.reason

    def test_rejects_low_citations(self):
        v = EvidenceValidator(min_total_citations=100)
        s = _supervisor(recent_pub_count=5, total_citations=30, latest_year=2024)
        assert v.validate(s) is False
        assert "total_citations" in s.evidence_validation.reason

    def test_rejects_stale_latest_publication(self):
        from datetime import datetime
        old_year = datetime.now().year - 10
        v = EvidenceValidator(max_publication_gap=5)
        s = _supervisor(recent_pub_count=5, total_citations=200, latest_year=old_year)
        assert v.validate(s) is False
        assert "latest_publication_year" in s.evidence_validation.reason

    def test_all_three_failures_in_reason(self):
        from datetime import datetime
        old_year = datetime.now().year - 10
        v = EvidenceValidator(
            min_recent_publications=10,
            min_total_citations=500,
            max_publication_gap=3,
        )
        s = _supervisor(recent_pub_count=1, total_citations=5, latest_year=old_year)
        v.validate(s)
        reason = s.evidence_validation.reason
        assert "recent_publications" in reason
        assert "total_citations" in reason
        assert "latest_publication_year" in reason

    def test_score_is_normalised(self):
        v = EvidenceValidator()
        s = _supervisor(recent_pub_count=10, total_citations=2000)
        v.validate(s)
        assert 0.0 <= s.evidence_validation.score <= 1.0

    def test_metadata_attached_on_pass(self):
        v = EvidenceValidator(min_recent_publications=1, min_total_citations=1)
        s = _supervisor(recent_pub_count=2, total_citations=10, latest_year=2024)
        v.validate(s)
        assert s.evidence_validation is not None
        assert s.evidence_validation.source == "evidence_thresholds"


# ====================================================================
# Pipeline validation counts (unit-level, no HTTP)
# ====================================================================

class TestPipelineValidationCounts:
    """
    Verify that _validate_with_counts() correctly tallies rejections
    at each gate using a minimal mock pipeline.
    """

    def _build_pipeline(self):
        """Build a pipeline with all network calls disabled."""
        from src.utils.config import Settings
        from src.pipelines.shortlist_pipeline import ShortlistPipeline
        from unittest.mock import MagicMock, patch

        settings = Settings(
            pi_use_network=False,
            pi_min_confidence=0.6,
            domain_min_score=0.0,      # permissive for count tests
            evidence_min_recent_publications=0,
            evidence_min_total_citations=0,
            evidence_max_publication_gap=50,
        )
        pipeline = ShortlistPipeline(settings=settings)
        # Disable evidence collector network calls
        pipeline.evidence_collector.enrich_all = lambda supervisors: supervisors
        return pipeline

    def test_all_pass_with_permissive_settings(self):
        pipeline = self._build_pipeline()
        profile = _profile(countries=[], domains=[], topics=[])
        supervisors = [
            _supervisor(country="US"),
            _supervisor(country="GB"),
        ]
        # Give supervisors a PI-eligible title so PI gate passes
        for s in supervisors:
            s.job_title = "Professor"

        _, counts = pipeline._validate_with_counts(supervisors, profile)
        assert counts.pi_passed == 2
        assert counts.country_passed == 2
        assert counts.domain_passed == 2
        assert counts.evidence_passed == 2

    def test_country_gate_rejects_correctly(self):
        pipeline = self._build_pipeline()
        profile = _profile(countries=["US"])
        supervisors = [
            _supervisor(country="US"),   # passes
            _supervisor(country="DE"),   # rejected by country
        ]
        for s in supervisors:
            s.job_title = "Professor"
            s.evidence_collected = True

        _, counts = pipeline._validate_with_counts(supervisors, profile)
        assert counts.pi_passed == 2
        assert counts.country_passed == 1

    def test_output_dict_has_all_count_keys(self):
        """run_retrieval output shape contains all required count fields."""
        from pathlib import Path
        from unittest.mock import patch, MagicMock

        pipeline = self._build_pipeline()

        mock_profile = _profile(countries=[], topics=["nlp"])
        mock_profile.name = "Test Student"
        mock_supervisor = _supervisor(country="US")
        mock_supervisor.job_title = "Professor"
        mock_supervisor.evidence_collected = True

        with patch.object(pipeline.parser, "parse_file", return_value=mock_profile), \
             patch.object(pipeline.openalex, "retrieve", return_value=[mock_supervisor]):
            result = pipeline.run_retrieval(Path("dummy.json"))

        assert "retrieved_count" in result
        assert "pi_validated_count" in result
        assert "country_validated_count" in result
        assert "domain_validated_count" in result
        assert "evidence_validated_count" in result
        assert "recommendation_count" in result
        assert "recommendations" in result
