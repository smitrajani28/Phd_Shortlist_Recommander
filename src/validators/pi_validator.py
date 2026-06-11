"""
Validates that a supervisor is an active Principal Investigator (PI)
who can independently supervise PhD students.

Policy: minimise contamination over maximising recall.
When confidence is below threshold, the supervisor is rejected.
"""

from ..models.supervisor import Supervisor, PIMetadata
from ..utils.logger import get_logger
from .base_validator import BaseValidator
from .faculty_profile_resolver import (
    FacultyProfileResolver,
    ResolverResult,
    _ELIGIBLE_SUBSTRINGS,
    _INELIGIBLE_SUBSTRINGS,
)

logger = get_logger(__name__)


class PIValidator(BaseValidator):
    """
    Two-stage PI check:

    Stage A — Fast title check (no network):
        If supervisor.job_title is already populated (e.g. from OpenAlex),
        match it against the eligible/ineligible lists immediately.

    Stage B — Network resolution (FacultyProfileResolver):
        If no title is present, or Stage A is inconclusive,
        query Google + institution page to resolve the title.
        Reject if confidence < min_confidence (conservative default).

    The resulting PIMetadata is attached to supervisor.pi_metadata
    regardless of whether the supervisor passes or fails, so downstream
    stages can explain rejections without re-querying.
    """

    def __init__(
        self,
        resolver: FacultyProfileResolver | None = None,
        min_confidence: float = 0.6,
        use_network: bool = True,
    ) -> None:
        """
        Args:
            resolver: Injected FacultyProfileResolver (created if None).
            min_confidence: Minimum resolver confidence to accept a supervisor.
            use_network: Set False in tests to skip HTTP calls.
        """
        self.resolver = resolver or FacultyProfileResolver(min_confidence=min_confidence)
        self.min_confidence = min_confidence
        self.use_network = use_network

    def validate(self, supervisor: Supervisor) -> bool:
        """
        Return True if the supervisor is a verified PI; False otherwise.

        Attaches PIMetadata to supervisor.pi_metadata as a side-effect.
        Logs the rejection reason when returning False.

        Args:
            supervisor: Supervisor to validate.

        Returns:
            True if PI-eligible with sufficient confidence.
        """
        # Stage A: fast title check from existing data
        if supervisor.job_title:
            metadata = self._check_title(supervisor.job_title, source="openalex_hint")
            supervisor.pi_metadata = metadata
            if metadata.pi_verified:
                return True
            # Title is known — ineligible or unrecognised, no need to go further
            self._log_rejection(supervisor, metadata.rejection_reason or "ineligible title")
            return False

        # Stage B: network resolution
        if self.use_network:
            area = supervisor.research_areas[0] if supervisor.research_areas else ""
            result: ResolverResult = self.resolver.resolve(
                name=supervisor.name,
                institution=supervisor.institution,
                research_area=area,
            )
            metadata = self._result_to_metadata(result)
            supervisor.pi_metadata = metadata

            if metadata.pi_verified:
                return True

            reason = metadata.rejection_reason or "unable to verify faculty position"
            self._log_rejection(supervisor, reason)
            return False

        # No title, network disabled → reject conservatively
        metadata = PIMetadata(
            pi_verified=False,
            rejection_reason="no title available and network resolution disabled",
            confidence=0.0,
        )
        supervisor.pi_metadata = metadata
        self._log_rejection(supervisor, metadata.rejection_reason)
        return False

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _check_title(self, title: str, source: str) -> PIMetadata:
        """
        Evaluate a known title string against eligible/ineligible lists.

        Returns a PIMetadata reflecting the verdict.
        """
        lower = title.lower()

        if _is_clearly_ineligible(lower):
            matched = next(s for s in _INELIGIBLE_SUBSTRINGS if s in lower)
            return PIMetadata(
                pi_verified=False,
                verification_source=source,
                job_title=title,
                confidence=1.0,
                rejection_reason=matched,
            )

        if _is_eligible(lower):
            matched = next(s for s in _ELIGIBLE_SUBSTRINGS if s in lower)
            return PIMetadata(
                pi_verified=True,
                verification_source=source,
                job_title=title,
                confidence=0.95,  # high but not 1.0 — title strings can be stale
            )

        # Title present but unrecognised — treat as inconclusive
        return PIMetadata(
            pi_verified=False,
            verification_source=source,
            job_title=title,
            confidence=0.3,
            rejection_reason=f"unrecognised title: {title}",
        )

    def _result_to_metadata(self, result: ResolverResult) -> PIMetadata:
        """Convert a ResolverResult into PIMetadata."""
        lower_title = result.title.lower()

        if result.confidence < self.min_confidence:
            return PIMetadata(
                pi_verified=False,
                verification_source=result.source,
                job_title=result.title,
                confidence=result.confidence,
                rejection_reason=(
                    result.title if _is_clearly_ineligible(lower_title)
                    else "unable to verify faculty position"
                ),
            )

        if _is_clearly_ineligible(lower_title):
            matched = next((s for s in _INELIGIBLE_SUBSTRINGS if s in lower_title), result.title)
            return PIMetadata(
                pi_verified=False,
                verification_source=result.source,
                job_title=result.title,
                confidence=result.confidence,
                rejection_reason=matched,
            )

        if _is_eligible(lower_title):
            return PIMetadata(
                pi_verified=True,
                verification_source=result.source,
                job_title=result.title,
                confidence=result.confidence,
            )

        return PIMetadata(
            pi_verified=False,
            verification_source=result.source,
            job_title=result.title,
            confidence=result.confidence,
            rejection_reason="unable to verify faculty position",
        )

    @staticmethod
    def _log_rejection(supervisor: Supervisor, reason: str) -> None:
        logger.info("Rejected: %s (%s) — Reason: %s", supervisor.name, supervisor.institution, reason)


# ------------------------------------------------------------------ #
# Module-level helpers shared with tests                              #
# ------------------------------------------------------------------ #

def _is_eligible(text: str) -> bool:
    return any(t in text for t in _ELIGIBLE_SUBSTRINGS)


def _is_clearly_ineligible(text: str) -> bool:
    return any(t in text for t in _INELIGIBLE_SUBSTRINGS)
