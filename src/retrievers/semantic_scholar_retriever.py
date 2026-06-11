"""
Semantic Scholar retriever — not implemented in current release.
OpenAlex (src/retrievers/openalex_retriever.py) is the active retrieval source.
This stub satisfies the BaseRetriever interface and returns [] safely.
"""

from ..models.student import StudentProfile
from ..models.supervisor import Supervisor
from ..utils.logger import get_logger
from .base_retriever import BaseRetriever

logger = get_logger(__name__)


class SemanticScholarRetriever(BaseRetriever):
    """Stub retriever — returns [] in current release."""

    def __init__(self, api_key: str | None = None, max_results: int = 100) -> None:
        self.api_key = api_key

    def retrieve(self, profile: StudentProfile) -> list[Supervisor]:
        return []
