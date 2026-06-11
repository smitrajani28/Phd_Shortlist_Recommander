"""
Faculty page scraper — not implemented in current release.
OpenAlex (src/retrievers/openalex_retriever.py) is the active retrieval source.
This stub satisfies the BaseRetriever interface and returns [] safely.
"""

from ..models.student import StudentProfile
from ..models.supervisor import Supervisor
from ..utils.logger import get_logger
from .base_retriever import BaseRetriever

logger = get_logger(__name__)


class FacultyScraper(BaseRetriever):
    """Stub retriever — returns [] in current release."""

    def __init__(self, target_urls: list[str] | None = None, delay_seconds: float = 1.0) -> None:
        self.target_urls = target_urls or []

    def retrieve(self, profile: StudentProfile) -> list[Supervisor]:
        return []
