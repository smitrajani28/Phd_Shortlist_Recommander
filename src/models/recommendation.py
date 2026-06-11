"""
Data models for final recommendations.
"""

from typing import Optional
from pydantic import BaseModel, Field
from .supervisor import Supervisor


class ScoreBreakdown(BaseModel):
    """
    Per-dimension score components for a single supervisor–student pair.
    All sub-scores are in [0, 1]. overall_score is the weighted composite.
    """

    overall_score: float = Field(ge=0.0, le=1.0)
    research_alignment: float = Field(ge=0.0, le=1.0, description="40% weight")
    publication_activity: float = Field(ge=0.0, le=1.0, description="20% weight")
    citation_impact: float = Field(ge=0.0, le=1.0, description="15% weight")
    evidence_quality: float = Field(ge=0.0, le=1.0, description="15% weight")
    country_preference: float = Field(ge=0.0, le=1.0, description="10% weight")


class LinkedProgram(BaseModel):
    """A PhD program or open position linked to a supervisor's institution."""
    program_name: str
    institution: str
    url: str
    status: str = Field(default="unknown", description="open | unknown")


class Recommendation(BaseModel):
    """
    A single ranked recommendation entry in the final shortlist.
    why_match is populated by WhyMatchGenerator (Stage 6); left empty until then.
    """

    rank: int = Field(..., ge=1)
    supervisor: Supervisor
    score: float = Field(..., ge=0.0, le=1.0, description="Composite match score")
    score_breakdown: ScoreBreakdown
    tier: str = Field(default="target", description="reach | target | safety")
    why_match: str = Field(default="", description="LLM-generated explanation (Stage 6)")
    program_url: Optional[str] = None
    # Program linking (Stage 8 enrichment)
    linked_programs: list[LinkedProgram] = Field(
        default_factory=list,
        description="PhD program pages linked to supervisor's institution",
    )
    # Contact email (Stage 8 enrichment)
    contact_email: Optional[str] = Field(default=None, description="Extracted institutional email")
    email_source: Optional[str] = Field(default=None, description="faculty_page | profile_url | not_found")
    # Feedback loop fields (populated when OutcomeLearner data is available)
    historical_success_score: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Supervisor success_score from historical outcomes",
    )
    historical_email_count: Optional[int] = Field(
        default=None,
        description="Total historical emails sent to this supervisor",
    )


class ShortlistOutput(BaseModel):
    """
    Top-level output schema exported as the final JSON artifact.
    Matches the structure described in schema.md.
    """

    student_name: str
    student_id: str = Field(default="", description="Slugified student name used in filename")
    total_candidates_evaluated: int
    recommendation_count: int = Field(default=0, description="Number of recommendations in this file")
    pipeline_version: str = Field(default="1.0.0")
    generated_at: str = Field(..., description="ISO 8601 UTC timestamp of generation")
    recommendations: list[Recommendation] = Field(default_factory=list)
