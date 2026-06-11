"""
Scrapes faculty listing pages from university department websites
to supplement API-sourced supervisor data.
"""

from ..models.student import StudentProfile
from ..models.supervisor import Supervisor
from ..utils.logger import get_logger
from .base_retriever import BaseRetriever

logger = get_logger(__name__)


class FacultyScraper(BaseRetriever):
    """
    Scrapes publicly available university faculty pages to discover
    supervisors not yet indexed by OpenAlex or Semantic Scholar.

    Respects robots.txt and adds polite delays between requests.
    Target URLs are configured via FACULTY_URLS in config/env.
    """

    def __init__(self, target_urls: list[str] | None = None, delay_seconds: float = 1.0) -> None:
        """
        Args:
            target_urls: List of faculty listing page URLs to scrape.
            delay_seconds: Polite crawl delay between requests.
        """
        self.target_urls = target_urls or []
        self.delay_seconds = delay_seconds

    def retrieve(self, profile: StudentProfile) -> list[Supervisor]:
        """
        Scrape configured faculty pages for candidate supervisors.

        Args:
            profile: Used to filter scraped faculty by research area keywords.

        Returns:
            List of Supervisor objects scraped from faculty pages.
        """
        # TODO: iterate self.target_urls
        # TODO: fetch HTML, parse with BeautifulSoup
        # TODO: extract name, title, research areas, email
        # TODO: filter by relevance to profile.research_interests
        logger.info("Faculty scraper not yet implemented")
        return []

    def _parse_faculty_page(self, html: str, institution: str) -> list[Supervisor]:
        """
        Extract individual faculty entries from raw HTML.

        Args:
            html: Raw HTML content of a faculty listing page.
            institution: Institution name to tag each Supervisor with.

        Returns:
            List of partially-populated Supervisor objects.
        """
        # TODO: use BeautifulSoup to locate faculty cards/rows
        # TODO: extract name, department, email, profile link
        raise NotImplementedError

    def _is_relevant(self, supervisor: Supervisor, profile: StudentProfile) -> bool:
        """
        Check whether a scraped supervisor's research areas overlap
        with the student's interests.

        Args:
            supervisor: Scraped supervisor candidate.
            profile: Student profile with research interests.

        Returns:
            True if there is sufficient keyword overlap.
        """
        # TODO: simple set-intersection or fuzzy keyword match
        raise NotImplementedError
