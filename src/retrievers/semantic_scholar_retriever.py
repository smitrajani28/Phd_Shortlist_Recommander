"""
Retrieves potential supervisors from the Semantic Scholar Academic Graph API.
API docs: https://api.semanticscholar.org/graph/v1
"""

from ..models.student import StudentProfile
from ..models.supervisor import Supervisor
from ..utils.logger import get_logger
from .base_retriever import BaseRetriever

logger = get_logger(__name__)


class SemanticScholarRetriever(BaseRetriever):
    """
    Queries Semantic Scholar's Author Search and Paper Search endpoints
    to discover supervisors with relevant publication histories.

    Supports optional API key for higher rate limits.
    """

    BASE_URL = "https://api.semanticscholar.org/graph/v1"

    def __init__(self, api_key: str | None = None, max_results: int = 100) -> None:
        """
        Args:
            api_key: Optional Semantic Scholar API key.
            max_results: Maximum number of authors to return.
        """
        self.api_key = api_key
        self.max_results = max_results

    def retrieve(self, profile: StudentProfile) -> list[Supervisor]:
        """
        Search Semantic Scholar for authors matching the student profile.

        Args:
            profile: Validated student profile.

        Returns:
            List of Supervisor objects with semantic_scholar_id populated.
        """
        # TODO: query /author/search?query=<keywords>
        # TODO: enrich each author with /author/{id}/papers
        # TODO: map response → Supervisor via _parse_author()
        logger.info("Semantic Scholar retrieval not yet implemented")
        return []

    def _parse_author(self, raw: dict) -> Supervisor:
        """
        Map a raw Semantic Scholar author object to a Supervisor model.

        Args:
            raw: Author dict from Semantic Scholar API.

        Returns:
            Partially-populated Supervisor.
        """
        # TODO: extract authorId, name, affiliations, paperCount, hIndex
        raise NotImplementedError
