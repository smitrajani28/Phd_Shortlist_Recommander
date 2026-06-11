"""
Reusable validation result model shared across all validator stages.
"""

from typing import Optional
from pydantic import BaseModel, Field


class ValidationResult(BaseModel):
    """
    Lightweight record of a single validator's decision.

    Attached to the Supervisor after each gate so downstream stages
    and the final output can explain every accept/reject decision.
    """

    passed: bool
    score: float = Field(default=1.0, ge=0.0, le=1.0)
    source: str = ""        # e.g. "country_exact_match", "domain_hybrid", "evidence_thresholds"
    reason: Optional[str] = None      # populated only on rejection
    matched_concepts: list[str] = Field(default_factory=list)  # used by DomainValidator
