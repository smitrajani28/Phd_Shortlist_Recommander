"""
Parses raw input JSON into a validated StudentProfile.
"""

import json
from pathlib import Path
from ..models.student import StudentProfile, ResearchInterest, AcademicBackground
from ..utils.logger import get_logger

logger = get_logger(__name__)


class StudentParser:
    """
    Loads and normalises raw student profile input.

    Supports two input shapes for research_interests:
      - plain strings:  ["NLP", "LLMs"]
      - weighted dicts: [{"topic": "NLP", "weight": 0.9}]

    extract_topics() additionally pulls keywords from:
      publication_keywords, project_keywords, skills
    returning a single deduplicated, lowercased list.
    """

    def parse_file(self, path: Path) -> StudentProfile:
        """
        Load a student profile from a JSON file on disk.

        Args:
            path: Path to the JSON file.

        Returns:
            Validated StudentProfile with extracted_topics populated.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If JSON fails schema validation.
        """
        logger.info("Loading student profile from %s", path)
        if not path.exists():
            raise FileNotFoundError(f"Profile not found: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return self.parse_dict(data)

    def parse_dict(self, data: dict) -> StudentProfile:
        """
        Build a StudentProfile from a raw dictionary.

        Normalises country codes to uppercase and strips whitespace.
        Populates extracted_topics from all keyword sources.

        Args:
            data: Raw dictionary from JSON input.

        Returns:
            Validated StudentProfile.
        """
        background_raw = data.get("background", {})
        background = AcademicBackground(
            degree=background_raw.get("degree", "").strip(),
            field=background_raw.get("field", "").strip(),
            institution=background_raw.get("institution", "").strip(),
            gpa=background_raw.get("gpa"),
        )

        interests = self._extract_interests(data.get("research_interests", []))
        countries = [c.strip().upper() for c in data.get("preferred_countries", [])]
        domains = [d.strip() for d in data.get("preferred_domains", [])]

        topics = self.extract_topics(data)

        return StudentProfile(
            name=data.get("name", "").strip(),
            email=data.get("email"),
            background=background,
            research_interests=interests,
            preferred_countries=countries,
            preferred_domains=domains,
            statement_of_purpose=data.get("statement_of_purpose"),
            extracted_topics=topics,
        )

    def _extract_interests(self, raw: list[dict] | list[str]) -> list[ResearchInterest]:
        """
        Coerce the research_interests field into ResearchInterest objects.

        Accepts plain strings and dicts with optional weight fields.

        Args:
            raw: List of raw interest entries.

        Returns:
            List of ResearchInterest instances.
        """
        interests: list[ResearchInterest] = []
        for item in raw:
            if isinstance(item, str):
                interests.append(ResearchInterest(topic=item.strip(), weight=1.0))
            elif isinstance(item, dict):
                interests.append(ResearchInterest(
                    topic=item.get("topic", "").strip(),
                    weight=float(item.get("weight", 1.0)),
                ))
        return interests

    def extract_topics(self, data: dict) -> list[str]:
        """
        Build a deduplicated, lowercased topic list from all keyword sources:
          - research_interests
          - publication_keywords
          - project_keywords
          - skills

        Args:
            data: Raw student profile dictionary.

        Returns:
            Ordered deduplicated list of topic strings (insertion order preserved).
        """
        seen: set[str] = set()
        topics: list[str] = []

        def _add(value: str) -> None:
            normalised = value.strip().lower()
            if normalised and normalised not in seen:
                seen.add(normalised)
                topics.append(normalised)

        # research_interests (highest priority)
        for item in data.get("research_interests", []):
            topic = item if isinstance(item, str) else item.get("topic", "")
            _add(topic)

        # additional keyword sources
        for source_key in ("publication_keywords", "project_keywords", "skills"):
            for kw in data.get(source_key, []):
                _add(kw if isinstance(kw, str) else str(kw))

        logger.debug("Extracted %d unique topics", len(topics))
        return topics
