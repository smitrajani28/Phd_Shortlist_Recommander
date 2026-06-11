"""
Shared pytest fixtures for the PhD Shortlist Builder test suite.
"""

import pytest
from src.models.student import StudentProfile, AcademicBackground, ResearchInterest
from src.models.supervisor import Supervisor, Publication
from src.models.recommendation import ScoreBreakdown


@pytest.fixture
def sample_profile() -> StudentProfile:
    """Minimal valid StudentProfile for use in unit tests."""
    return StudentProfile(
        name="Jane Doe",
        background=AcademicBackground(degree="MSc", field="Computer Science", institution="MIT"),
        research_interests=[
            ResearchInterest(topic="natural language processing", weight=0.9),
            ResearchInterest(topic="large language models", weight=0.8),
        ],
        preferred_countries=["US", "CA"],
        preferred_domains=["NLP", "AI"],
    )


@pytest.fixture
def sample_supervisor() -> Supervisor:
    """Minimal valid Supervisor for use in unit tests."""
    return Supervisor(
        name="Dr. Alice Smith",
        institution="Stanford University",
        country="US",
        research_areas=["natural language processing", "machine learning"],
        recent_publications=[
            Publication(title="Attention is All You Need", year=2023, citation_count=500),
        ],
    )


@pytest.fixture
def sample_score_breakdown() -> ScoreBreakdown:
    """Sample ScoreBreakdown with neutral scores."""
    return ScoreBreakdown(
        research_alignment=0.8,
        publication_recency=0.9,
        funding_activity=0.5,
        country_preference=1.0,
        domain_match=0.7,
    )
