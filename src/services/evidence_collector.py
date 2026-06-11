"""
Enriches PI-validated supervisors with verifiable research evidence
fetched from the OpenAlex /works endpoint.

Populates on each Supervisor:
  publications              — top-10 by citation count (rich records)
  total_citations           — sum across fetched publications
  recent_publication_count  — papers within `recency_years` window
  latest_publication_year   — year of the most recent paper
  research_concepts         — deduplicated concept labels from all papers
  evidence_collected        — True on success, False on failure
"""

from datetime import datetime
from typing import Any

from ..models.supervisor import Supervisor, Publication
from ..services.openalex_client import OpenAlexClient
from ..utils.logger import get_logger

logger = get_logger(__name__)

# Cache keyed by openalex_id → list of raw work dicts
_works_cache: dict[str, list[dict]] = {}


class EvidenceCollector:
    """
    Fetches and aggregates publication evidence for a single supervisor.

    Design:
    - One public method: enrich(supervisor) → Supervisor
    - Returns the same object (mutated in-place) so pipeline chaining is trivial
    - If openalex_id is missing or the API fails, sets evidence_collected=False
      and returns the supervisor unchanged — downstream EvidenceValidator decides
      whether to reject it (separation of concerns)
    - Cache prevents duplicate API calls for the same author across pipeline runs

    Args:
        client:         Shared OpenAlexClient instance.
        max_works:      Maximum publications to fetch per author (default 10).
        recency_years:  Window for recent_publication_count (default 5).
    """

    def __init__(
        self,
        client: OpenAlexClient,
        max_works: int = 10,
        recency_years: int = 5,
    ) -> None:
        self.client = client
        self.max_works = max_works
        self.recency_years = recency_years
        self._cutoff_year = datetime.now().year - recency_years

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def enrich(self, supervisor: Supervisor) -> Supervisor:
        """
        Fetch publication evidence from OpenAlex and write it onto the supervisor.

        Args:
            supervisor: PI-validated Supervisor (must have openalex_id set).

        Returns:
            The same Supervisor object with evidence fields populated.
            evidence_collected=False if enrichment was not possible.
        """
        logger.info("Collecting evidence for %s", supervisor.name)

        if not supervisor.openalex_id:
            logger.warning("No openalex_id for %s — skipping evidence collection", supervisor.name)
            supervisor.evidence_collected = False
            return supervisor

        try:
            works = self._fetch_works(supervisor.openalex_id)
        except Exception as exc:
            logger.warning("Evidence fetch failed for %s: %s", supervisor.name, exc)
            supervisor.evidence_collected = False
            return supervisor

        if not works:
            logger.warning("No publications found for %s", supervisor.name)
            supervisor.evidence_collected = False
            return supervisor

        logger.info("Found %d publications for %s", len(works), supervisor.name)
        self._write_evidence(supervisor, works)
        return supervisor

    def enrich_all(self, supervisors: list[Supervisor]) -> list[Supervisor]:
        """
        Enrich a list of supervisors in-place. Never raises.

        Args:
            supervisors: List of PI-validated supervisors.

        Returns:
            Same list with evidence fields populated where possible.
        """
        for s in supervisors:
            self.enrich(s)
        return supervisors

    # ------------------------------------------------------------------ #
    # Fetch                                                                #
    # ------------------------------------------------------------------ #

    def _fetch_works(self, openalex_id: str) -> list[dict]:
        """
        Fetch the top `max_works` publications for an author, sorted by
        citation count descending.

        Cached by openalex_id to avoid duplicate requests.

        Args:
            openalex_id: Short OpenAlex author ID, e.g. "A2208157607".

        Returns:
            List of raw work dicts from the OpenAlex API.
        """
        if openalex_id in _works_cache:
            logger.debug("Works cache hit for %s", openalex_id)
            return _works_cache[openalex_id]

        data = self.client.get(
            "/works",
            params={
                "filter": f"authorships.author.id:{openalex_id}",
                "sort": "cited_by_count:desc",
                "per-page": self.max_works,
                "select": (
                    "id,title,publication_year,cited_by_count,"
                    "primary_location,doi,concepts"
                ),
            },
        )
        works: list[dict] = data.get("results", [])
        _works_cache[openalex_id] = works
        return works

    # ------------------------------------------------------------------ #
    # Parse + aggregate                                                    #
    # ------------------------------------------------------------------ #

    def _write_evidence(self, supervisor: Supervisor, works: list[dict]) -> None:
        """
        Parse raw works, compute summary statistics, and write all evidence
        fields onto the supervisor in-place.
        """
        publications: list[Publication] = [self._parse_work(w) for w in works]

        total_citations = sum(p.citation_count for p in publications)
        recent_count = sum(
            1 for p in publications if p.year >= self._cutoff_year
        )
        years = [p.year for p in publications if p.year]
        latest_year = max(years) if years else None
        concepts = _deduplicate_concepts(
            c for p in publications for c in p.concepts
        )

        supervisor.publications = publications
        supervisor.total_citations = total_citations
        supervisor.recent_publication_count = recent_count
        supervisor.latest_publication_year = latest_year
        supervisor.research_concepts = concepts
        supervisor.evidence_collected = True

        logger.info(
            "Evidence for %s: total_citations=%d, recent=%d, latest_year=%s, concepts=%d",
            supervisor.name, total_citations, recent_count, latest_year, len(concepts),
        )

    def _parse_work(self, raw: dict) -> Publication:
        """
        Map a single raw OpenAlex work object to a Publication model.

        Extracts: title, year, citation_count, doi_url, openalex_url, concepts.
        """
        # DOI URL
        doi = raw.get("doi") or ""
        doi_url = doi if doi.startswith("http") else (f"https://doi.org/{doi}" if doi else None)

        # OpenAlex URL
        work_id: str = raw.get("id", "")
        openalex_url = work_id if work_id.startswith("http") else None

        # Venue from primary_location
        primary = raw.get("primary_location") or {}
        source = primary.get("source") or {}
        venue = source.get("display_name")

        # Concept labels (level ≤ 2 for relevance, sorted by score desc)
        raw_concepts: list[dict] = raw.get("concepts") or []
        concepts = [
            c["display_name"]
            for c in sorted(raw_concepts, key=lambda x: x.get("score", 0), reverse=True)
            if c.get("level", 99) <= 2 and c.get("display_name")
        ]

        return Publication(
            title=raw.get("title") or "Untitled",
            year=raw.get("publication_year") or 0,
            citation_count=raw.get("cited_by_count") or 0,
            venue=venue,
            doi_url=doi_url,
            openalex_url=openalex_url,
            concepts=concepts,
        )


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _deduplicate_concepts(concepts: Any) -> list[str]:
    """Return a deduplicated list preserving first-seen order."""
    seen: set[str] = set()
    result: list[str] = []
    for c in concepts:
        low = c.lower()
        if low not in seen:
            seen.add(low)
            result.append(c)
    return result


def clear_works_cache() -> None:
    """Flush the works cache. Used in tests to isolate runs."""
    _works_cache.clear()
