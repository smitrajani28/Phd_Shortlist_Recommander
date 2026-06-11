"""
Application configuration loaded from environment variables / .env file.
Uses pydantic-settings so every value is validated at startup.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """
    All runtime configuration for the PhD Shortlist Builder.

    Values are read from environment variables (case-insensitive) or a .env file.
    Sensitive keys (API keys) must never be hard-coded; use .env.example as reference.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM
    llm_provider: str = Field(default="gemini", description="LLM backend: 'gemini' or 'openai'")
    gemini_api_key: str = Field(default="", description="Google Gemini API key")
    gemini_model: str = Field(default="gemini-1.5-flash")
    openai_api_key: str = Field(default="", description="OpenAI API key for why_match generation")
    openai_model: str = Field(default="gpt-4o-mini")

    # External APIs
    semantic_scholar_api_key: str = Field(default="", description="Semantic Scholar API key")
    openalex_email: str = Field(default="", description="Email for OpenAlex polite pool")

    # Pipeline behaviour
    max_recommendations: int = Field(default=100, ge=50, le=200)
    min_recent_papers: int = Field(default=2, ge=1)
    recency_years: int = Field(default=5, ge=1)
    retrieval_top_n: int = Field(default=5, ge=1, description="Supervisors to return from retrieval slice")

    # HTTP / retry
    request_timeout: int = Field(default=10)
    max_retries: int = Field(default=3)

    # PI validation
    pi_min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    pi_use_network: bool = Field(default=True, description="Set False to disable HTTP calls in PIValidator")
    pi_fallback_min_h_index: int = Field(default=10, ge=0, description="Min h-index for OpenAlex fallback acceptance")
    pi_fallback_min_works: int = Field(default=20, ge=0, description="Min works_count for OpenAlex fallback acceptance")
    pi_fallback_min_citations: int = Field(default=200, ge=0, description="Min cited_by_count for OpenAlex fallback acceptance")

    # Evidence collection
    evidence_max_works: int = Field(default=10, ge=1, le=50, description="Max publications to fetch per supervisor")
    evidence_recency_years: int = Field(default=5, ge=1, description="Window for recent_publication_count")

    # Validation thresholds
    domain_min_score: float = Field(default=0.35, ge=0.0, le=1.0, description="Minimum hybrid domain score")
    evidence_min_recent_publications: int = Field(default=3, ge=0)
    evidence_min_total_citations: int = Field(default=50, ge=0)
    evidence_max_publication_gap: int = Field(default=5, ge=1, description="Reject if latest paper older than N years")

    # Embedding-based domain validation
    embedding_domain_enabled: bool = Field(default=False, description="Enable embedding-based domain validation")
    embedding_domain_threshold: float = Field(default=0.55, ge=0.0, le=1.0, description="Min cosine similarity to pass")
    embedding_model_name: str = Field(default="sentence-transformers/all-MiniLM-L6-v2")

    # Scoring thresholds
    score_reach_threshold: float = Field(default=0.70, ge=0.0, le=1.0, description="overall_score >= this → reach")
    score_safety_threshold: float = Field(default=0.45, ge=0.0, le=1.0, description="overall_score < this → safety")

    # Why-match generation
    why_match_top_n: int = Field(default=50, ge=1, description="Only top-N recommendations get LLM calls")
    why_match_max_words: int = Field(default=120, ge=20, description="Hard cap on why_match output length")

    # Feedback loop / outcome learning
    feedback_weight: float = Field(default=0.15, ge=0.0, le=1.0, description="Weight of historical success_score in adjusted ranking")
    wrong_person_threshold: float = Field(default=0.20, ge=0.0, le=1.0, description="wrong_person_rate above this triggers penalty")
    outcome_weights: dict[str, int] = Field(
        default_factory=lambda: {
            "ADMIT": 5, "INTERVIEW": 3, "POSITIVE_REPLY": 2,
            "REJECT": -2, "NO_REPLY": -1, "BOUNCE": -3,
            "OUT_OF_OFFICE": 0, "NOT_RECRUITING": -5, "WRONG_PERSON": -10,
        },
        description="Per-outcome integer weights for success_score calculation",
    )

    # Scraper
    faculty_urls: list[str] = Field(default_factory=list)

    # Logging
    log_level: str = Field(default="INFO")
    log_file: str | None = Field(default=None)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return a cached Settings singleton.
    Use this everywhere instead of instantiating Settings() directly.
    """
    return Settings()
