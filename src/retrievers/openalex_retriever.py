"""
Retrieves potential supervisors from the OpenAlex open academic graph.
API docs: https://docs.openalex.org
"""

from ..models.student import StudentProfile
from ..models.supervisor import Supervisor, Publication
from ..services.openalex_client import OpenAlexClient
from ..utils.logger import get_logger
from .base_retriever import BaseRetriever

logger = get_logger(__name__)


class OpenAlexRetriever(BaseRetriever):
    """
    Queries the OpenAlex REST API to find authors whose work aligns
    with the student's research interests.

    Strategy:
      1. For each extracted topic, call /works?filter=title.search:<topic>
         to find relevant papers, then collect unique author IDs.
      2. Fetch /authors/<id> for each author to get institution + metrics.
      3. Deduplicate by OpenAlex author ID across topics.

    HTTP, retry, and caching are delegated to OpenAlexClient.
    """

    def __init__(
        self,
        email: str | None = None,
        max_results: int = 100,
        timeout: int = 10,
        max_retries: int = 3,
    ) -> None:
        self.max_results = max_results
        self.client = OpenAlexClient(email=email, timeout=timeout, max_retries=max_retries)

    # ------------------------------------------------------------------ #
    # Public interface                                                     #
    # ------------------------------------------------------------------ #

    def retrieve(self, profile: StudentProfile) -> list[Supervisor]:
        """Search OpenAlex using top extracted topics from the student profile."""
        topics = profile.extracted_topics or [i.topic for i in profile.research_interests]
        # Use only the top 3 topics to keep API calls manageable
        return self.search_authors_by_topics(topics[:3])

    def search_authors_by_topic(self, topic: str) -> list[Supervisor]:
        """
        Find supervisors whose recent works match a single topic keyword.

        Searches /works?filter=title.search:<topic>, collects author IDs,
        then fetches full author records.
        """
        logger.info("OpenAlex: searching topic '%s'", topic)
        works = self._get_works_for_topic(topic)

        author_ids: list[str] = []
        seen: set[str] = set()
        for work in works:
            for authorship in work.get("authorships", []):
                author = authorship.get("author") or {}
                aid = author.get("id", "")
                short_id = aid.split("/")[-1] if aid else ""
                if short_id and short_id not in seen:
                    seen.add(short_id)
                    author_ids.append(short_id)
                    if len(author_ids) >= 5:   # cap author fetches per topic
                        break
            if len(author_ids) >= 5:
                break

        supervisors: list[Supervisor] = []
        for aid in author_ids:
            try:
                supervisors.append(self.get_author_details(aid))
            except Exception as exc:
                logger.warning("Could not fetch author %s: %s", aid, exc)

        logger.info("OpenAlex: topic '%s' → %d authors", topic, len(supervisors))
        return supervisors

    def search_authors_by_topics(self, topics: list[str]) -> list[Supervisor]:
        """Search across multiple topics; deduplicate by openalex_id."""
        seen_ids: set[str] = set()
        results: list[Supervisor] = []
        for topic in topics:
            for supervisor in self.search_authors_by_topic(topic):
                if supervisor.openalex_id not in seen_ids:
                    seen_ids.add(supervisor.openalex_id or "")
                    results.append(supervisor)
        logger.info("OpenAlex: %d unique authors across %d topics", len(results), len(topics))
        return results

    def get_author_details(self, author_id: str) -> Supervisor:
        """Fetch a full author record from /authors/<id> and map it to Supervisor."""
        data = self.client.get(f"/authors/{author_id}")
        return self._parse_author(data)

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _get_works_for_topic(self, topic: str) -> list[dict]:
        data = self.client.get("/works", params={
            "filter": f"title.search:{topic}",
            "sort": "cited_by_count:desc",
            "per-page": min(self.max_results, 25),
            "select": "id,title,authorships,publication_year",
        })
        return data.get("results", [])

    def _parse_author(self, raw: dict) -> Supervisor:
        """
        Map a raw OpenAlex author object to a Supervisor model.

        Institution resolution order (OpenAlex v2+ — last_known_institution is null):
          1. last_known_institutions[0]   (plural list, current API)
          2. affiliations[0].institution  (full affiliation history)
          3. "Unknown Institution"        (fallback)
        """
        # --- institution / country ---
        affiliation: dict = (
            # 1. last_known_institutions (plural) — current API field
            ((raw.get("last_known_institutions") or [{}])[0])
            # 2. affiliations[].institution — fallback
            or (raw.get("affiliations") or [{}])[0].get("institution") or {}
        )
        institution = affiliation.get("display_name", "Unknown Institution") or "Unknown Institution"
        country = affiliation.get("country_code", "")

        # --- research areas ---
        # x_concepts no longer carries a 'level' field in the current API;
        # take the top concepts by score instead.
        research_areas = [
            c["display_name"]
            for c in sorted(
                (raw.get("x_concepts") or []),
                key=lambda c: c.get("score", 0),
                reverse=True,
            )
            if c.get("display_name")
        ]

        logger.info(
            "Parsed author: %s | Institution: %s | Country: %s",
            raw.get("display_name", "Unknown"),
            institution,
            country or "N/A",
        )

        recent_publications = self._extract_recent_publications(raw)

        return Supervisor(
            name=raw.get("display_name", "Unknown"),
            institution=institution,
            country=country,
            profile_url=(
                raw.get("orcid")
                or f"https://openalex.org/{raw.get('id', '').split('/')[-1]}"
            ),
            research_areas=research_areas[:10],
            recent_publications=recent_publications,
            h_index=raw.get("summary_stats", {}).get("h_index"),
            openalex_id=raw.get("id", "").split("/")[-1],
        )

    def _extract_recent_publications(self, author_raw: dict) -> list[Publication]:
        """
        Build a lightweight Publication list from counts_by_year.
        Full records are fetched later by EvidenceCollector.
        """
        pubs: list[Publication] = []
        for entry in (author_raw.get("counts_by_year") or [])[:5]:
            year = entry.get("year")
            if year:
                pubs.append(Publication(
                    title=f"Works in {year} ({entry.get('works_count', 0)} papers)",
                    year=year,
                    citation_count=entry.get("cited_by_count", 0),
                ))
        return pubs
