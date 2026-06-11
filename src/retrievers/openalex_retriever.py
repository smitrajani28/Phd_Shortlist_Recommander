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

# ---------------------------------------------------------------------------
# Topic expansion map
# Maps a canonical topic keyword → list of related query variants.
# Used to widen retrieval without broadening validation gates.
# ---------------------------------------------------------------------------
_TOPIC_EXPANSIONS: dict[str, list[str]] = {
    "large language models": ["LLM", "generative AI", "foundation models", "transformers"],
    "retrieval augmented generation": ["retrieval systems", "information retrieval", "semantic search", "dense retrieval"],
    "natural language processing": ["NLP", "computational linguistics", "text mining", "language understanding"],
    "machine learning": ["deep learning", "neural networks", "supervised learning", "representation learning"],
    "computer vision": ["image recognition", "visual learning", "object detection", "visual representation"],
    "knowledge graphs": ["knowledge representation", "ontology", "semantic web", "entity linking"],
    "reinforcement learning": ["RL", "reward learning", "policy optimization", "multi-agent learning"],
    "question answering": ["open-domain QA", "reading comprehension", "factoid QA"],
    "transformer architectures": ["attention mechanism", "BERT", "GPT", "encoder decoder"],
    "fine-tuning": ["transfer learning", "pre-training", "domain adaptation", "instruction tuning"],
    "semantic search": ["dense retrieval", "vector search", "bi-encoder", "neural IR"],
    "vector databases": ["approximate nearest neighbor", "embedding index", "FAISS", "vector store"],
}


def expand_topics(topics: list[str], max_topics: int) -> list[str]:
    """
    Expand a list of base topics using the expansion map.

    Returns a deduplicated list capped at max_topics, preserving
    base topics first so they are always searched.
    """
    seen: set[str] = set()
    expanded: list[str] = []

    def _add(t: str) -> None:
        key = t.lower().strip()
        if key not in seen:
            seen.add(key)
            expanded.append(t)

    # Base topics first
    for t in topics:
        _add(t)

    # Expansions second
    for t in topics:
        for variant in _TOPIC_EXPANSIONS.get(t.lower().strip(), []):
            _add(variant)
            if len(expanded) >= max_topics:
                return expanded

    return expanded[:max_topics]


class OpenAlexRetriever(BaseRetriever):
    """
    Queries the OpenAlex REST API to find authors whose work aligns
    with the student's research interests.

    Strategy:
      1. Expand base topics using _TOPIC_EXPANSIONS.
      2. For each topic call /works?filter=title.search:<topic> and collect
         unique author IDs (up to authors_per_topic).
      3. Fetch /authors/<id> for each author to get institution + metrics.
      4. Deduplicate by openalex_id AND (name, institution) across all topics.
      5. Log a per-topic retrieval report.
    """

    def __init__(
        self,
        email: str | None = None,
        max_results: int = 100,
        timeout: int = 10,
        max_retries: int = 3,
        per_topic_works: int = 50,
        authors_per_topic: int = 20,
        max_topics: int = 8,
    ) -> None:
        self.max_results = max_results
        self.per_topic_works = per_topic_works
        self.authors_per_topic = authors_per_topic
        self.max_topics = max_topics
        self.client = OpenAlexClient(email=email, timeout=timeout, max_retries=max_retries)

    # ------------------------------------------------------------------ #
    # Public interface                                                     #
    # ------------------------------------------------------------------ #

    def retrieve(self, profile: StudentProfile) -> list[Supervisor]:
        """Search OpenAlex using expanded topics from the student profile."""
        base_topics = profile.extracted_topics or [i.topic for i in profile.research_interests]
        topics = expand_topics(base_topics, self.max_topics)
        logger.info("Retrieval topics (%d): %s", len(topics), topics)
        return self.search_authors_by_topics(topics)

    def search_authors_by_topic(self, topic: str) -> list[Supervisor]:
        """
        Find supervisors whose recent works match a single topic keyword.
        Fetches up to self.authors_per_topic unique authors.
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
            if len(author_ids) >= self.authors_per_topic:
                break

        author_ids = author_ids[:self.authors_per_topic]

        supervisors: list[Supervisor] = []
        for aid in author_ids:
            try:
                supervisors.append(self.get_author_details(aid))
            except Exception as exc:
                logger.warning("Could not fetch author %s: %s", aid, exc)

        logger.info(
            "OpenAlex: topic '%s' — works=%d, author_ids=%d, fetched=%d",
            topic, len(works), len(author_ids), len(supervisors),
        )
        return supervisors

    def search_authors_by_topics(self, topics: list[str]) -> list[Supervisor]:
        """
        Search across multiple topics; deduplicate by openalex_id and (name, institution).
        Logs a per-topic and final retrieval summary.
        """
        seen_ids: set[str] = set()
        seen_name_inst: set[tuple[str, str]] = set()
        results: list[Supervisor] = []

        for topic in topics:
            before = len(results)
            for supervisor in self.search_authors_by_topic(topic):
                oid = supervisor.openalex_id or ""
                name_key = (supervisor.name.lower(), supervisor.institution.lower())
                if oid and oid in seen_ids:
                    continue
                if name_key in seen_name_inst:
                    continue
                seen_ids.add(oid)
                seen_name_inst.add(name_key)
                results.append(supervisor)
            logger.info(
                "Retrieval report — topic: '%s' | new unique: %d | running total: %d",
                topic, len(results) - before, len(results),
            )

        logger.info(
            "Retrieval complete — %d unique authors across %d topics",
            len(results), len(topics),
        )
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
            "per-page": min(self.per_topic_works, 200),
            "select": "id,title,authorships,publication_year",
        })
        return data.get("results", [])

    def _parse_author(self, raw: dict) -> Supervisor:
        """
        Map a raw OpenAlex author object to a Supervisor model.

        Institution resolution order (OpenAlex v2+):
          1. last_known_institutions[0]   (plural list, current API)
          2. affiliations[0].institution  (full affiliation history)
          3. "Unknown Institution"        (fallback)
        """
        affiliation: dict = (
            ((raw.get("last_known_institutions") or [{}])[0])
            or (raw.get("affiliations") or [{}])[0].get("institution") or {}
        )
        institution = affiliation.get("display_name", "Unknown Institution") or "Unknown Institution"
        country = affiliation.get("country_code", "")

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

        return Supervisor(
            name=raw.get("display_name", "Unknown"),
            institution=institution,
            country=country,
            profile_url=(
                raw.get("orcid")
                or f"https://openalex.org/{raw.get('id', '').split('/')[-1]}"
            ),
            research_areas=research_areas[:10],
            recent_publications=self._extract_recent_publications(raw),
            h_index=raw.get("summary_stats", {}).get("h_index"),
            works_count=raw.get("works_count"),
            cited_by_count=raw.get("cited_by_count"),
            openalex_id=raw.get("id", "").split("/")[-1],
        )

    def _extract_recent_publications(self, author_raw: dict) -> list[Publication]:
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
