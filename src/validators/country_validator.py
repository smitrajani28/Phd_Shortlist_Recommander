"""
Validates that a supervisor's institution country is within the
student's preferred countries.
"""

from ..models.student import StudentProfile
from ..models.supervisor import Supervisor
from ..models.validation import ValidationResult
from ..utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Normalisation: common name / OpenAlex code variants → ISO 3166-1 alpha-2
# ---------------------------------------------------------------------------
_COUNTRY_ALIASES: dict[str, str] = {
    # Full names
    "united states": "US", "united states of america": "US",
    "united kingdom": "GB", "great britain": "GB", "england": "GB",
    "canada": "CA", "germany": "DE", "deutschland": "DE",
    "france": "FR", "netherlands": "NL", "holland": "NL",
    "switzerland": "CH", "sweden": "SE", "denmark": "DK",
    "norway": "NO", "finland": "FI", "austria": "AT",
    "australia": "AU", "new zealand": "NZ",
    "singapore": "SG", "china": "CN", "japan": "JP",
    "south korea": "KR", "korea": "KR",
    "india": "IN", "israel": "IL",
    # Common 2-letter aliases that differ from ISO
    "uk": "GB", "usa": "US", "u.s.": "US", "u.s.a.": "US",
    "u.k.": "GB",
}


def _normalise(code: str) -> str:
    """Return the canonical ISO alpha-2 code for a raw country string."""
    stripped = code.strip()
    # Alias table lookup first (case-insensitive) — handles "uk", "u.k.", full names
    alias = _COUNTRY_ALIASES.get(stripped.lower())
    if alias:
        return alias
    # Fall back to uppercased value (already a correct ISO code like "US", "GB")
    return stripped.upper()


class CountryValidator:
    """
    Accepts supervisors whose institution country is in the student's
    preferred_countries list.

    Rules (in order):
      1. If student has no preferred countries → all pass.
      2. Exact match after normalising both sides to ISO alpha-2.
      3. Fallback alias map handles full names and common abbreviations.

    Attaches supervisor.country_validation (ValidationResult) on every call.
    """

    def __init__(self, profile: StudentProfile) -> None:
        self._preferred: set[str] = {
            _normalise(c) for c in profile.preferred_countries
        }

    def validate(self, supervisor: Supervisor) -> bool:
        """
        Returns True if the supervisor's country is accepted.
        Attaches ValidationResult to supervisor.country_validation.
        """
        # No preference → all pass
        if not self._preferred:
            supervisor.country_validation = ValidationResult(
                passed=True, score=1.0, source="no_preference"
            )
            return True

        normalised = _normalise(supervisor.country or "")

        if normalised in self._preferred:
            supervisor.country_validation = ValidationResult(
                passed=True, score=1.0, source="country_exact_match"
            )
            return True

        reason = (
            f"{supervisor.country or 'unknown'} not in target countries "
            f"[{', '.join(sorted(self._preferred))}]"
        )
        logger.info("Rejected: %s (%s) — %s", supervisor.name, supervisor.institution, reason)
        supervisor.country_validation = ValidationResult(
            passed=False, score=0.0, source="country_exact_match", reason=reason
        )
        return False
