"""
Validates that a supervisor has sufficient evidence of active research,
using fields populated by EvidenceCollector in Stage 4.

Thresholds (all configurable, applied as AND):
  - recent_publication_count  >= min_recent_publications  (default 3)
  - total_citations            >= min_total_citations      (default 50)
  - latest_publication_year   >= current_year - max_gap   (default 5 years)

A supervisor whose evidence_collected=False fails immediately with
reason "evidence not collected".
"""

from datetime import datetime
from ..models.supervisor import Supervisor
from ..models.validation import ValidationResult
from ..utils.logger import get_logger

logger = get_logger(__name__)


class EvidenceValidator:
    """
    Rejects supervisors that are inactive or insufficiently evidenced.

    All three thresholds must pass. This is intentionally conservative:
    a supervisor who has not published recently and has low citation
    impact is unlikely to be a strong match for a competitive PhD applicant.

    Args:
        min_recent_publications: Minimum papers within the recency window.
        min_total_citations:     Minimum total citation count.
        max_publication_gap:     Reject if latest paper is older than
                                 current_year - max_publication_gap.
    """

    def __init__(
        self,
        min_recent_publications: int = 3,
        min_total_citations: int = 50,
        max_publication_gap: int = 5,
    ) -> None:
        self.min_recent_publications = min_recent_publications
        self.min_total_citations = min_total_citations
        self._min_latest_year = datetime.now().year - max_publication_gap

    def validate(self, supervisor: Supervisor) -> bool:
        """
        Returns True if all evidence thresholds are met.
        Attaches ValidationResult to supervisor.evidence_validation.
        """
        # Fast path: evidence was never collected
        if not supervisor.evidence_collected:
            return self._reject(supervisor, "evidence not collected")

        failures: list[str] = []

        if supervisor.recent_publication_count < self.min_recent_publications:
            failures.append(
                f"recent_publications {supervisor.recent_publication_count} "
                f"< {self.min_recent_publications}"
            )

        if supervisor.total_citations < self.min_total_citations:
            failures.append(
                f"total_citations {supervisor.total_citations} "
                f"< {self.min_total_citations}"
            )

        latest = supervisor.latest_publication_year or 0
        if latest < self._min_latest_year:
            failures.append(
                f"latest_publication_year {latest} "
                f"< {self._min_latest_year}"
            )

        if failures:
            return self._reject(supervisor, "; ".join(failures))

        supervisor.evidence_validation = ValidationResult(
            passed=True,
            score=self._score(supervisor),
            source="evidence_thresholds",
        )
        return True

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _reject(self, supervisor: Supervisor, reason: str) -> bool:
        logger.info(
            "Rejected: %s (%s) — %s", supervisor.name, supervisor.institution, reason
        )
        supervisor.evidence_validation = ValidationResult(
            passed=False, score=0.0, source="evidence_thresholds", reason=reason
        )
        return False

    def _score(self, supervisor: Supervisor) -> float:
        """
        Soft score in [0, 1] reflecting evidence strength above the floor.
        Used for transparency; not used for hard accept/reject decisions.
        """
        recent_norm = min(supervisor.recent_publication_count / max(self.min_recent_publications * 2, 1), 1.0)
        citation_norm = min(supervisor.total_citations / max(self.min_total_citations * 10, 1), 1.0)
        return round((recent_norm + citation_norm) / 2, 4)
