"""
Validates that a supervisor is an active Principal Investigator (PI)
who can independently supervise PhD students.

Policy: minimise contamination over maximising recall.
When confidence is below threshold, the supervisor is rejected.
"""

from ..models.supervisor import Supervisor, PIMetadata
from ..utils.logger import get_logger
from ..utils.config import get_settings
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
    Three-stage PI check:

    Stage A — Fast title check (no network):
        If supervisor.job_title is already populated, match against
        eligible/ineligible lists immediately.

    Stage B — Network resolution (FacultyProfileResolver):
        Query Google + institution page to resolve the title.

    Stage C — OpenAlex fallback (no network):
        If stages A/B fail, accept high-impact researchers whose
        h_index / works_count / cited_by_count exceed configured thresholds,
        provided they have a known institution and research areas.
        Rejects are still rejected if the title is clearly ineligible.
    """

    def __init__(
        self,
        resolver: FacultyProfileResolver | None = None,
        min_confidence: float = 0.6,
        use_network: bool = True,
        fallback_min_h_index: int | None = None,
        fallback_min_works: int | None = None,
        fallback_min_citations: int | None = None,
    ) -> None:
        settings = get_settings()
        self.resolver = resolver or FacultyProfileResolver(min_confidence=min_confidence)
        self.min_confidence = min_confidence
        self.use_network = use_network
        self.fallback_min_h_index = fallback_min_h_index if fallback_min_h_index is not None else settings.pi_fallback_min_h_index
        self.fallback_min_works = fallback_min_works if fallback_min_works is not None else settings.pi_fallback_min_works
        self.fallback_min_citations = fallback_min_citations if fallback_min_citations is not None else settings.pi_fallback_min_citations

    def validate(self, supervisor: Supervisor) -> bool:
        # Stage A: fast title check
        if supervisor.job_title:
            metadata = self._check_title(supervisor.job_title, source="openalex_hint")
            metadata.verification_method = "title_hint"
            supervisor.pi_metadata = metadata
            if metadata.pi_verified:
                logger.info("Accepted: %s — Method: title_hint", supervisor.name)
                return True
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
            metadata.verification_method = "faculty_page"

            # If network found a clearly ineligible title — reject immediately, skip fallback
            if not metadata.pi_verified and result.title and _is_clearly_ineligible(result.title.lower()):
                supervisor.pi_metadata = metadata
                self._log_rejection(supervisor, metadata.rejection_reason or "ineligible title")
                return False

            if metadata.pi_verified:
                supervisor.pi_metadata = metadata
                logger.info("Accepted: %s — Method: faculty_page", supervisor.name)
                return True

        # Stage C: OpenAlex bibliometric fallback
        fallback = self._openalex_fallback(supervisor)
        if fallback is not None:
            supervisor.pi_metadata = fallback
            logger.info(
                "Accepted: %s — Method: openalex_fallback — Reason: h_index=%s, works=%s, citations=%s, institution=%s",
                supervisor.name,
                supervisor.h_index,
                supervisor.works_count,
                supervisor.cited_by_count,
                supervisor.institution,
            )
            return True

        # All stages failed
        metadata = PIMetadata(
            pi_verified=False,
            verification_method="none",
            rejection_reason="unable to verify faculty position",
            confidence=0.0,
        )
        supervisor.pi_metadata = metadata
        self._log_rejection(supervisor, metadata.rejection_reason)
        return False

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _openalex_fallback(self, supervisor: Supervisor) -> PIMetadata | None:
        """
        Return accepted PIMetadata when bibliometric signals are strong enough,
        or None if the supervisor does not meet the thresholds.

        Confidence tiers:
          0.75 — h_index meets threshold (strong individual signal)
          0.70 — works_count meets threshold (prolific output)
          0.65 — cited_by_count meets threshold + institution known
          None  — no threshold met → caller rejects
        """
        # Must have a known institution to pass fallback
        if not supervisor.institution or supervisor.institution == "Unknown Institution":
            return None

        h = supervisor.h_index or 0
        works = supervisor.works_count or 0
        citations = supervisor.cited_by_count or 0

        if h >= self.fallback_min_h_index:
            confidence = 0.75
        elif works >= self.fallback_min_works:
            confidence = 0.70
        elif citations >= self.fallback_min_citations:
            confidence = 0.65
        else:
            return None

        return PIMetadata(
            pi_verified=True,
            verification_source="openalex",
            verification_method="openalex_fallback",
            confidence=confidence,
        )

    def _check_title(self, title: str, source: str) -> PIMetadata:
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
            return PIMetadata(
                pi_verified=True,
                verification_source=source,
                job_title=title,
                confidence=0.95,
            )

        return PIMetadata(
            pi_verified=False,
            verification_source=source,
            job_title=title,
            confidence=0.3,
            rejection_reason=f"unrecognised title: {title}",
        )

    def _result_to_metadata(self, result: ResolverResult) -> PIMetadata:
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
