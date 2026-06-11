"""
Data models for PhD supervisors.
"""

from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum
from .validation import ValidationResult


class FundingStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    UNKNOWN = "unknown"


class Publication(BaseModel):
    """Represents a single academic publication."""

    title: str
    year: int
    venue: Optional[str] = None
    citation_count: int = 0
    # Legacy field kept for backwards compatibility with retriever output
    url: Optional[str] = None
    # Evidence-collection fields (populated by EvidenceCollector)
    doi_url: Optional[str] = None
    openalex_url: Optional[str] = None
    concepts: list[str] = Field(default_factory=list)


class Grant(BaseModel):
    """Represents a research grant held by a supervisor."""

    title: str
    funder: str
    year: int
    amount: Optional[float] = None


class PIMetadata(BaseModel):
    """
    Validation metadata produced by PIValidator.
    Attached to every Supervisor that passes or fails PI validation.
    """

    pi_verified: bool = False
    verification_source: str = ""
    verification_method: str = ""   # "faculty_page" | "openalex_fallback" | "title_hint"
    job_title: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rejection_reason: Optional[str] = None


class Supervisor(BaseModel):
    """
    Canonical model for a potential PhD supervisor.
    Populated by retrievers and enriched by validators/evidence collectors.
    """

    # Core identity
    name: str
    institution: str
    department: Optional[str] = None
    country: str
    email: Optional[str] = None
    profile_url: Optional[str] = None

    # Research profile (from retriever; overwritten/enriched by EvidenceCollector)
    research_areas: list[str] = Field(default_factory=list)
    recent_publications: list[Publication] = Field(default_factory=list)
    grants: list[Grant] = Field(default_factory=list)

    # Funding / status
    is_accepting_students: bool = True
    funding_status: FundingStatus = FundingStatus.UNKNOWN

    # Bibliometrics (from retriever author record)
    h_index: Optional[int] = None
    works_count: Optional[int] = None
    cited_by_count: Optional[int] = None

    # External IDs
    openalex_id: Optional[str] = None
    semantic_scholar_id: Optional[str] = None

    # PI validation
    job_title: Optional[str] = None
    pi_metadata: Optional[PIMetadata] = None

    # ------------------------------------------------------------------ #
    # Evidence-collection fields (populated by EvidenceCollector)         #
    # ------------------------------------------------------------------ #
    publications: list[Publication] = Field(
        default_factory=list,
        description="Top-10 richest publications fetched by EvidenceCollector",
    )
    total_citations: int = Field(
        default=0,
        description="Sum of citation_count across all fetched publications",
    )
    recent_publication_count: int = Field(
        default=0,
        description="Number of publications within the recency window",
    )
    latest_publication_year: Optional[int] = Field(
        default=None,
        description="Year of the most recent publication found",
    )
    research_concepts: list[str] = Field(
        default_factory=list,
        description="Deduplicated concept labels extracted from publications",
    )
    evidence_collected: bool = Field(
        default=False,
        description="True once EvidenceCollector.enrich() has run successfully",
    )

    # ------------------------------------------------------------------ #
    # Per-gate validation results (attached by each validator)            #
    # ------------------------------------------------------------------ #
    country_validation: Optional["ValidationResult"] = None
    domain_validation: Optional["ValidationResult"] = None
    evidence_validation: Optional["ValidationResult"] = None
