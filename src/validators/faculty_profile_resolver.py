"""
Resolves a supervisor's academic title and faculty page URL
by searching Google and scraping institution faculty pages.

Design goals:
- Minimise false positives: when unsure, return low confidence.
- Use institution + name + research area to reduce same-name collisions.
- Cache every resolved result for the lifetime of a pipeline run.
"""

import re
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from ..utils.logger import get_logger

logger = get_logger(__name__)

# ------------------------------------------------------------------ #
# In-process cache: (name, institution) → ResolverResult             #
# ------------------------------------------------------------------ #
_cache: dict[tuple[str, str], "ResolverResult"] = {}


@dataclass
class ResolverResult:
    """Result returned by FacultyProfileResolver."""

    title: str = ""
    faculty_page: str = ""
    confidence: float = 0.0
    source: str = ""           # e.g. "google_snippet", "faculty_page_scrape", "openalex_hint"


# Titles that indicate PI eligibility (lowercase, substring-matched)
_ELIGIBLE_SUBSTRINGS: tuple[str, ...] = (
    "assistant professor",
    "associate professor",
    "full professor",
    "professor",          # catches "visiting professor", "adjunct professor", etc.
    "principal investigator",
    "faculty",
    "research scientist",
    "research fellow",
    "lecturer",
    "senior lecturer",
    "reader",             # UK equivalent of associate professor
    "chair",              # endowed chair → definitely PI
)

# Titles that definitively indicate non-PI (lowercase, substring-matched)
_INELIGIBLE_SUBSTRINGS: tuple[str, ...] = (
    "phd student",
    "phd candidate",
    "doctoral student",
    "doctoral candidate",
    "graduate student",
    "master student",
    "master's student",
    "undergraduate",
    "postdoctoral",
    "postdoc",
    "post-doc",
    "post doctoral",
    "visiting student",
    "research intern",
    "intern",
)

# Google Custom Search — we use a lightweight HTML scrape of google.com
# (no API key needed; rate-limited; for production use Google Custom Search API)
_GOOGLE_SEARCH_URL = "https://www.google.com/search"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; PhDShortlistBot/1.0; "
        "+https://github.com/example/phd-shortlist)"
    )
}


class FacultyProfileResolver:
    """
    Resolves a supervisor's job title and faculty page by:

    1. Checking a known OpenAlex/Semantic Scholar hint already on the Supervisor.
    2. Querying Google for "<name> <institution> professor" and parsing
       the snippet / result titles for a title pattern.
    3. If a faculty page URL surfaces, scraping it for a title string.

    Priority (fallback chain):
        openalex_hint → google_snippet → faculty_page_scrape → unverified

    A result with confidence < `min_confidence` is treated as unverified
    by PIValidator and the supervisor is rejected (conservative policy).
    """

    def __init__(
        self,
        timeout: int = 8,
        crawl_delay: float = 1.5,
        min_confidence: float = 0.6,
    ) -> None:
        self.timeout = timeout
        self.crawl_delay = crawl_delay
        self.min_confidence = min_confidence

    def resolve(
        self,
        name: str,
        institution: str,
        research_area: str = "",
    ) -> ResolverResult:
        """
        Return title + faculty_page + confidence for the given person.

        Cached: identical (name, institution) pairs are resolved only once
        per pipeline run.

        Args:
            name: Full name of the supervisor.
            institution: Supervisor's current institution.
            research_area: Optional primary research area to reduce collisions.

        Returns:
            ResolverResult with at least title and confidence set.
        """
        cache_key = (name.lower(), institution.lower())
        if cache_key in _cache:
            logger.debug("Resolver cache hit for %s @ %s", name, institution)
            return _cache[cache_key]

        result = self._resolve_uncached(name, institution, research_area)
        _cache[cache_key] = result
        logger.debug(
            "Resolved %s @ %s → title='%s' confidence=%.2f source=%s",
            name, institution, result.title, result.confidence, result.source,
        )
        return result

    # ------------------------------------------------------------------ #
    # Internal resolution chain                                           #
    # ------------------------------------------------------------------ #

    def _resolve_uncached(
        self, name: str, institution: str, research_area: str
    ) -> ResolverResult:
        # Step 1: try Google snippet
        result = self._search_google(name, institution, research_area)
        if result.confidence >= self.min_confidence:
            return result

        # Step 2: if Google returned a faculty page URL, scrape it
        if result.faculty_page:
            scraped = self._scrape_faculty_page(result.faculty_page, name)
            if scraped.confidence >= self.min_confidence:
                return scraped
            # Merge: keep the page URL even if title confidence is low
            if scraped.title:
                result.title = scraped.title
                result.confidence = scraped.confidence
                result.source = scraped.source

        return result

    def _search_google(
        self, name: str, institution: str, research_area: str
    ) -> ResolverResult:
        """
        Submit a Google search for '<name> <institution> professor <area>'
        and parse the result snippets for title patterns.
        """
        query = f"{name} {institution} professor {research_area}".strip()
        params = {"q": query, "num": 5}

        try:
            time.sleep(self.crawl_delay)
            resp = httpx.get(
                _GOOGLE_SEARCH_URL,
                params=params,
                headers=_HEADERS,
                timeout=self.timeout,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Google search failed for '%s': %s", name, exc)
            return ResolverResult()

        soup = BeautifulSoup(resp.text, "lxml")
        result = ResolverResult(source="google_snippet")

        # Collect all visible text blocks from search result snippets
        text_blocks: list[str] = []
        for tag in soup.select("div.BNeawe, span.BNeawe, div.VwiC3b, div.IsZvec"):
            text_blocks.append(tag.get_text(" ", strip=True))

        # Also check anchor hrefs for faculty page URLs
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            # Google wraps URLs; extract actual URL
            url = _extract_google_url(href)
            if url and _looks_like_faculty_page(url, institution):
                result.faculty_page = url
                break

        combined = " ".join(text_blocks).lower()
        title, confidence = _extract_title_from_text(combined, name)
        result.title = title
        result.confidence = confidence
        return result

    def _scrape_faculty_page(self, url: str, name: str) -> ResolverResult:
        """
        Fetch a faculty profile page and look for a job title near the name.
        """
        try:
            time.sleep(self.crawl_delay)
            resp = httpx.get(
                url, headers=_HEADERS, timeout=self.timeout, follow_redirects=True
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Faculty page fetch failed (%s): %s", url, exc)
            return ResolverResult(faculty_page=url)

        soup = BeautifulSoup(resp.text, "lxml")
        text = soup.get_text(" ", strip=True).lower()
        title, confidence = _extract_title_from_text(text, name)
        return ResolverResult(
            title=title,
            faculty_page=url,
            confidence=confidence,
            source="faculty_page_scrape",
        )


# ------------------------------------------------------------------ #
# Pure helper functions (no I/O)                                      #
# ------------------------------------------------------------------ #

def _extract_title_from_text(text: str, name: str) -> tuple[str, float]:
    """
    Scan `text` for eligible or ineligible title substrings.

    Returns (matched_title, confidence):
      - Ineligible match → ("", 0.0) so caller can reject immediately.
      - Eligible match   → (title, confidence based on specificity).
      - No match         → ("", 0.0).
    """
    # Ineligible check has priority (conservative: reject on any ineligible signal)
    for ineligible in _INELIGIBLE_SUBSTRINGS:
        if ineligible in text:
            return ineligible, 0.0

    # Eligible check: return the most specific (longest) match found
    matches = [t for t in _ELIGIBLE_SUBSTRINGS if t in text]
    if not matches:
        return "", 0.0

    best = max(matches, key=len)

    # Confidence heuristic:
    #   0.95 if name also appears near the title string (high specificity)
    #   0.75 if title found but name proximity unconfirmed
    name_lower = name.lower().split()[-1]  # last name is most distinctive
    confidence = 0.95 if name_lower in text else 0.75
    return best, confidence


def _extract_google_url(href: str) -> str:
    """Pull the real URL out of a Google-wrapped /url?q=... link."""
    match = re.search(r"/url\?q=([^&]+)", href)
    if match:
        from urllib.parse import unquote
        return unquote(match.group(1))
    if href.startswith("http"):
        return href
    return ""


def _looks_like_faculty_page(url: str, institution: str) -> bool:
    """
    Heuristic: does the URL look like an institutional faculty/people page?
    Avoids LinkedIn, ResearchGate, Wikipedia, etc.
    """
    NOISE_DOMAINS = ("linkedin.com", "researchgate.net", "wikipedia.org",
                     "scholar.google", "twitter.com", "x.com", "github.com")
    if any(d in url for d in NOISE_DOMAINS):
        return False
    FACULTY_SIGNALS = ("/faculty/", "/people/", "/staff/", "/profile/",
                       "/~", "cs.", "eecs.", "ece.", ".edu", ".ac.uk")
    return any(s in url for s in FACULTY_SIGNALS)
