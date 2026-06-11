"""
Links PhD programs and open positions to a supervisor via their institution.

Strategy:
  1. Build 2–3 Google queries from institution name + research area.
  2. Parse result snippets for official graduate/PhD program URLs.
  3. Return up to max_programs LinkedProgram objects.

All HTTP is best-effort — failures are caught and logged; the rest of
the pipeline is never blocked by a linker failure.
"""

import re
import time
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

from ..models.supervisor import Supervisor
from ..utils.logger import get_logger

logger = get_logger(__name__)

_GOOGLE_URL = "https://www.google.com/search"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; PhDShortlistBot/1.0; "
        "+https://github.com/example/phd-shortlist)"
    )
}

# Noise domains never worth linking
_NOISE_DOMAINS = (
    "linkedin.com", "researchgate.net", "wikipedia.org",
    "scholar.google", "twitter.com", "x.com", "quora.com",
    "reddit.com", "facebook.com",
)

# Signals that a URL is an official grad-program page
_PROGRAM_SIGNALS = (
    "graduate", "phd", "doctoral", "admissions", "program",
    "degree", "apply", ".edu", ".ac.uk", ".ac.",
)


@dataclass
class LinkedProgram:
    program_name: str
    institution: str
    url: str
    status: str = "unknown"   # "open" | "unknown"


class ProgramLinker:
    """
    Finds PhD program pages for a supervisor's institution.

    Args:
        timeout:      HTTP timeout in seconds.
        crawl_delay:  Seconds to wait between Google requests.
        max_programs: Maximum LinkedProgram objects returned per supervisor.
    """

    def __init__(
        self,
        timeout: int = 8,
        crawl_delay: float = 1.0,
        max_programs: int = 3,
    ) -> None:
        self.timeout = timeout
        self.crawl_delay = crawl_delay
        self.max_programs = max_programs

    def link(self, supervisor: Supervisor) -> list[LinkedProgram]:
        """
        Return up to max_programs LinkedProgram objects for the supervisor.
        Returns [] on any failure rather than raising.
        """
        institution = supervisor.institution
        if not institution or institution == "Unknown Institution":
            return []

        area = supervisor.research_areas[0] if supervisor.research_areas else "computer science"
        queries = [
            f"{institution} PhD {area} program admissions",
            f"{institution} graduate school doctoral program",
        ]

        seen_urls: set[str] = set()
        programs: list[LinkedProgram] = []

        for query in queries:
            if len(programs) >= self.max_programs:
                break
            try:
                time.sleep(self.crawl_delay)
                resp = httpx.get(
                    _GOOGLE_URL,
                    params={"q": query, "num": 5},
                    headers=_HEADERS,
                    timeout=self.timeout,
                    follow_redirects=True,
                )
                resp.raise_for_status()
            except Exception as exc:
                logger.warning("ProgramLinker: Google search failed ('%s'): %s", query, exc)
                continue

            soup = BeautifulSoup(resp.text, "lxml")
            for a in soup.select("a[href]"):
                url = _extract_google_url(a.get("href", ""))
                if not url or url in seen_urls:
                    continue
                if any(d in url for d in _NOISE_DOMAINS):
                    continue
                if not any(s in url.lower() for s in _PROGRAM_SIGNALS):
                    continue
                seen_urls.add(url)
                # Derive a human-readable program name from the URL path
                name = _program_name_from_url(url, institution)
                status = "open" if any(w in url.lower() for w in ("apply", "admissions")) else "unknown"
                programs.append(LinkedProgram(
                    program_name=name,
                    institution=institution,
                    url=url,
                    status=status,
                ))
                if len(programs) >= self.max_programs:
                    break

        logger.info(
            "ProgramLinker: %s → %d program links found",
            supervisor.name, len(programs),
        )
        return programs


# ------------------------------------------------------------------ #
# Pure helpers                                                         #
# ------------------------------------------------------------------ #

def _extract_google_url(href: str) -> str:
    match = re.search(r"/url\?q=([^&]+)", href)
    if match:
        from urllib.parse import unquote
        return unquote(match.group(1))
    return href if href.startswith("http") else ""


def _program_name_from_url(url: str, institution: str) -> str:
    """Derive a short readable label from the URL path segments."""
    parts = [p for p in url.rstrip("/").split("/") if p and not p.startswith("?")]
    label = parts[-1].replace("-", " ").replace("_", " ").title() if parts else "PhD Program"
    return f"{institution} — {label}"
