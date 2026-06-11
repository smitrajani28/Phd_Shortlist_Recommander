"""
Extracts a supervisor's institutional contact email.

Priority order:
  1. Faculty profile page (from FacultyProfileResolver result stored on pi_metadata)
  2. OpenAlex profile URL
  3. Constructed institutional search query

Policy:
  - Prefer institutional emails (contain institution domain fragment).
  - Reject generic webmail (gmail, yahoo, hotmail, outlook).
  - Return the first institutional email found; empty string on failure.
"""

import re
import time
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

from ..models.supervisor import Supervisor
from ..utils.logger import get_logger

logger = get_logger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; PhDShortlistBot/1.0; "
        "+https://github.com/example/phd-shortlist)"
    )
}
_WEBMAIL_DOMAINS = ("gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "protonmail.com")
# Regex: standard email pattern
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


@dataclass
class EmailResult:
    email: str = ""
    source: str = ""   # "faculty_page" | "profile_url" | "not_found"


class EmailExtractor:
    """
    Scrapes a supervisor's known web pages to extract an institutional email.

    Args:
        timeout:     HTTP timeout in seconds.
        crawl_delay: Seconds to wait between requests.
    """

    def __init__(self, timeout: int = 8, crawl_delay: float = 1.0) -> None:
        self.timeout = timeout
        self.crawl_delay = crawl_delay

    def extract(self, supervisor: Supervisor) -> EmailResult:
        """
        Return an EmailResult for the supervisor.
        Always returns an EmailResult (email="" on failure).
        """
        # Source 1: faculty page captured by PIValidator / FacultyProfileResolver
        faculty_page = (
            supervisor.pi_metadata.verification_source
            if supervisor.pi_metadata and supervisor.pi_metadata.verification_source.startswith("http")
            else ""
        )
        if faculty_page:
            result = self._scrape_page(faculty_page, supervisor, source="faculty_page")
            if result.email:
                return result

        # Source 2: OpenAlex/ORCID profile URL
        profile_url = supervisor.profile_url or ""
        if profile_url and profile_url.startswith("http") and "openalex.org" not in profile_url:
            result = self._scrape_page(profile_url, supervisor, source="profile_url")
            if result.email:
                return result

        logger.debug("EmailExtractor: no email found for %s", supervisor.name)
        return EmailResult(source="not_found")

    # ------------------------------------------------------------------ #

    def _scrape_page(self, url: str, supervisor: Supervisor, source: str) -> EmailResult:
        try:
            time.sleep(self.crawl_delay)
            resp = httpx.get(url, headers=_HEADERS, timeout=self.timeout, follow_redirects=True)
            resp.raise_for_status()
        except Exception as exc:
            logger.debug("EmailExtractor: failed to fetch %s: %s", url, exc)
            return EmailResult()

        soup = BeautifulSoup(resp.text, "lxml")
        # Prefer mailto: links — most reliable
        for a in soup.select("a[href^='mailto:']"):
            email = a["href"].replace("mailto:", "").split("?")[0].strip().lower()
            if _is_institutional(email):
                logger.info("EmailExtractor: found email for %s → %s (%s)", supervisor.name, email, source)
                return EmailResult(email=email, source=source)

        # Fall back to regex scan of visible text
        text = soup.get_text(" ", strip=True)
        for match in _EMAIL_RE.finditer(text):
            email = match.group(0).lower()
            if _is_institutional(email):
                logger.info("EmailExtractor: found email for %s → %s (%s)", supervisor.name, email, source)
                return EmailResult(email=email, source=source)

        return EmailResult()


# ------------------------------------------------------------------ #
# Pure helpers                                                         #
# ------------------------------------------------------------------ #

def _is_institutional(email: str) -> bool:
    """Return True if email is not a known webmail provider."""
    domain = email.split("@")[-1] if "@" in email else ""
    return bool(domain) and not any(domain.endswith(w) for w in _WEBMAIL_DOMAINS)
