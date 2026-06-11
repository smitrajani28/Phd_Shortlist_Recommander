"""
Data models for student profiles.
"""

from pydantic import BaseModel, Field
from typing import Optional


class ResearchInterest(BaseModel):
    """Represents a single extracted research interest."""

    topic: str = Field(..., description="Research topic or keyword")
    weight: float = Field(default=1.0, ge=0.0, le=1.0, description="Relevance weight")


class AcademicBackground(BaseModel):
    """Academic history of the student."""

    degree: str = Field(..., description="Highest degree obtained or in progress")
    field: str = Field(..., description="Field of study")
    institution: str = Field(..., description="Institution name")
    gpa: Optional[float] = Field(default=None, ge=0.0, le=4.0)


class StudentProfile(BaseModel):
    """
    Canonical model for a student applying to PhD programs.
    Populated by StudentParser from raw input JSON.
    """

    name: str
    email: Optional[str] = None
    background: AcademicBackground
    research_interests: list[ResearchInterest] = Field(default_factory=list)
    preferred_countries: list[str] = Field(default_factory=list)
    preferred_domains: list[str] = Field(default_factory=list)
    statement_of_purpose: Optional[str] = None
    # Deduplicated flat topic list produced by StudentParser.extract_topics()
    extracted_topics: list[str] = Field(default_factory=list)
