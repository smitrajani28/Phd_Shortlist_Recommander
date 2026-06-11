"""
Unit tests for PIValidator and FacultyProfileResolver helpers.

All tests run with use_network=False so no HTTP calls are made.
Network-dependent paths are tested via a mock FacultyProfileResolver.
"""

import pytest
from unittest.mock import MagicMock

from src.models.supervisor import Supervisor, Publication
from src.validators.pi_validator import PIValidator, _is_eligible, _is_clearly_ineligible
from src.validators.faculty_profile_resolver import (
    FacultyProfileResolver,
    ResolverResult,
    _extract_title_from_text,
)


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _make_supervisor(name: str = "Test User", job_title: str | None = None) -> Supervisor:
    return Supervisor(
        name=name,
        institution="Test University",
        country="US",
        job_title=job_title,
        research_areas=["machine learning"],
    )


def _offline_validator(mock_result: ResolverResult | None = None) -> PIValidator:
    """Return a PIValidator with network disabled (and optionally a mock resolver)."""
    resolver = MagicMock(spec=FacultyProfileResolver)
    if mock_result is not None:
        resolver.resolve.return_value = mock_result
    return PIValidator(resolver=resolver, use_network=False)


def _network_validator(mock_result: ResolverResult) -> PIValidator:
    """Return a PIValidator that uses a mock resolver (no real HTTP)."""
    resolver = MagicMock(spec=FacultyProfileResolver)
    resolver.resolve.return_value = mock_result
    return PIValidator(resolver=resolver, use_network=True, min_confidence=0.6)


# ------------------------------------------------------------------ #
# Title-string helper tests                                            #
# ------------------------------------------------------------------ #

class TestTitleHelpers:
    @pytest.mark.parametrize("title", [
        "Professor", "assistant professor", "Associate Professor",
        "Principal Investigator", "Faculty", "Research Scientist",
        "Lecturer", "Senior Lecturer", "Reader", "Chair",
    ])
    def test_eligible_titles_recognised(self, title: str):
        assert _is_eligible(title.lower())

    @pytest.mark.parametrize("title", [
        "PhD Student", "Doctoral Student", "Graduate Student",
        "Master's Student", "Undergraduate", "Postdoctoral Researcher",
        "Postdoc", "Post-Doctoral Fellow", "Visiting Student",
        "Research Intern",
    ])
    def test_ineligible_titles_recognised(self, title: str):
        assert _is_clearly_ineligible(title.lower())

    def test_ineligible_overrides_eligible(self):
        # "postdoctoral research scientist" contains both — ineligible wins
        text = "postdoctoral research scientist"
        assert _is_clearly_ineligible(text)


# ------------------------------------------------------------------ #
# Requirement 1: Professor accepted                                    #
# ------------------------------------------------------------------ #

class TestProfessorAccepted:
    def test_full_professor_accepted(self):
        s = _make_supervisor(job_title="Professor")
        validator = _offline_validator()
        assert validator.validate(s) is True

    def test_pi_metadata_set_on_accept(self):
        s = _make_supervisor(job_title="Professor of Computer Science")
        _offline_validator().validate(s)
        assert s.pi_metadata is not None
        assert s.pi_metadata.pi_verified is True
        assert s.pi_metadata.confidence > 0.0
        assert s.pi_metadata.job_title == "Professor of Computer Science"


# ------------------------------------------------------------------ #
# Requirement 2: Assistant Professor accepted                          #
# ------------------------------------------------------------------ #

class TestAssistantProfessorAccepted:
    def test_assistant_professor_accepted(self):
        s = _make_supervisor(job_title="Assistant Professor")
        assert _offline_validator().validate(s) is True

    def test_associate_professor_accepted(self):
        s = _make_supervisor(job_title="Associate Professor")
        assert _offline_validator().validate(s) is True

    def test_verification_source_is_openalex_hint(self):
        s = _make_supervisor(job_title="Assistant Professor")
        _offline_validator().validate(s)
        assert s.pi_metadata.verification_source == "openalex_hint"


# ------------------------------------------------------------------ #
# Requirement 3: PhD Student rejected                                  #
# ------------------------------------------------------------------ #

class TestPhDStudentRejected:
    def test_phd_student_rejected(self):
        s = _make_supervisor(job_title="PhD Student")
        assert _offline_validator().validate(s) is False

    def test_doctoral_candidate_rejected(self):
        s = _make_supervisor(job_title="Doctoral Candidate")
        assert _offline_validator().validate(s) is False

    def test_graduate_student_rejected(self):
        s = _make_supervisor(job_title="Graduate Student, MIT")
        assert _offline_validator().validate(s) is False

    def test_rejection_reason_recorded(self):
        s = _make_supervisor(job_title="PhD Student")
        _offline_validator().validate(s)
        assert s.pi_metadata.pi_verified is False
        assert "phd student" in s.pi_metadata.rejection_reason.lower()


# ------------------------------------------------------------------ #
# Requirement 4: Postdoc rejected                                      #
# ------------------------------------------------------------------ #

class TestPostdocRejected:
    def test_postdoctoral_researcher_rejected(self):
        s = _make_supervisor(job_title="Postdoctoral Researcher")
        assert _offline_validator().validate(s) is False

    def test_postdoc_shortform_rejected(self):
        s = _make_supervisor(job_title="Postdoc, Stanford University")
        assert _offline_validator().validate(s) is False

    def test_post_doctoral_fellow_rejected(self):
        s = _make_supervisor(job_title="Post-Doctoral Fellow")
        assert _offline_validator().validate(s) is False


# ------------------------------------------------------------------ #
# Requirement 5: Unknown title rejected (conservative policy)          #
# ------------------------------------------------------------------ #

class TestUnknownTitleRejected:
    def test_no_title_network_disabled_rejected(self):
        s = _make_supervisor(job_title=None)
        validator = _offline_validator()        # use_network=False
        assert validator.validate(s) is False

    def test_unrecognised_title_rejected(self):
        s = _make_supervisor(job_title="Lab Manager")
        assert _offline_validator().validate(s) is False

    def test_rejection_reason_set_for_unknown(self):
        s = _make_supervisor(job_title=None)
        _offline_validator().validate(s)
        assert s.pi_metadata.rejection_reason is not None
        assert s.pi_metadata.pi_verified is False


# ------------------------------------------------------------------ #
# Network resolver path (mocked)                                       #
# ------------------------------------------------------------------ #

class TestNetworkResolution:
    def test_high_confidence_professor_result_accepted(self):
        s = _make_supervisor(job_title=None)
        validator = _network_validator(
            ResolverResult(title="assistant professor", faculty_page="https://cs.mit.edu/~test",
                           confidence=0.95, source="faculty_page_scrape")
        )
        assert validator.validate(s) is True

    def test_low_confidence_result_rejected(self):
        s = _make_supervisor(job_title=None)
        validator = _network_validator(
            ResolverResult(title="professor", faculty_page="", confidence=0.3, source="google_snippet")
        )
        assert validator.validate(s) is False

    def test_resolver_finds_postdoc_rejected(self):
        s = _make_supervisor(job_title=None)
        validator = _network_validator(
            ResolverResult(title="postdoctoral researcher", faculty_page="",
                           confidence=0.9, source="faculty_page_scrape")
        )
        assert validator.validate(s) is False

    def test_metadata_populated_from_resolver(self):
        s = _make_supervisor(job_title=None)
        result = ResolverResult(
            title="associate professor",
            faculty_page="https://eecs.mit.edu/~test",
            confidence=0.92,
            source="faculty_page_scrape",
        )
        _network_validator(result).validate(s)
        assert s.pi_metadata.pi_verified is True
        assert s.pi_metadata.verification_source == "faculty_page_scrape"
        assert s.pi_metadata.job_title == "associate professor"
        assert s.pi_metadata.confidence == 0.92


# ------------------------------------------------------------------ #
# _extract_title_from_text unit tests                                  #
# ------------------------------------------------------------------ #

class TestExtractTitleFromText:
    def test_returns_professor_from_text(self):
        title, conf = _extract_title_from_text(
            "dr. alice smith is an assistant professor at stanford university", "alice smith"
        )
        assert title == "assistant professor"
        assert conf >= 0.75

    def test_ineligible_returns_zero_confidence(self):
        title, conf = _extract_title_from_text(
            "bob jones is a phd student at mit", "bob jones"
        )
        assert conf == 0.0

    def test_no_match_returns_empty(self):
        title, conf = _extract_title_from_text("no academic title here", "alice")
        assert title == ""
        assert conf == 0.0

    def test_name_present_boosts_confidence(self):
        _, conf_with = _extract_title_from_text("alice is a professor", "alice")
        _, conf_without = _extract_title_from_text("someone is a professor", "alice")
        assert conf_with > conf_without


# ------------------------------------------------------------------ #
# Other validators (still stubbed — keep existing skips)              #
# ------------------------------------------------------------------ #

class TestCountryValidator:
    def test_passes_when_no_preference_set(self, sample_profile, sample_supervisor):
        from src.validators.country_validator import CountryValidator
        sample_profile.preferred_countries = []
        CountryValidator(sample_profile)
        pytest.skip("CountryValidator.validate not yet implemented")

    def test_fails_for_non_preferred_country(self, sample_profile, sample_supervisor):
        from src.validators.country_validator import CountryValidator
        sample_supervisor.country = "DE"
        CountryValidator(sample_profile)
        pytest.skip("CountryValidator.validate not yet implemented")


class TestEvidenceValidator:
    def test_fails_with_no_publications(self, sample_supervisor):
        from src.validators.evidence_validator import EvidenceValidator
        sample_supervisor.recent_publications = []
        EvidenceValidator(min_recent_publications=2)
        pytest.skip("EvidenceValidator.validate not yet implemented via legacy fixture")

    def test_passes_with_sufficient_recent_papers(self, sample_supervisor):
        from src.validators.evidence_validator import EvidenceValidator
        sample_supervisor.recent_publications = [
            Publication(title="Paper A", year=2024, citation_count=10),
            Publication(title="Paper B", year=2023, citation_count=5),
        ]
        EvidenceValidator(min_recent_publications=2)
        pytest.skip("EvidenceValidator.validate not yet implemented via legacy fixture")
