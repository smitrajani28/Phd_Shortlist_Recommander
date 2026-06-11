"""
Unit tests for StudentParser.
"""

import pytest
from src.parsers.student_parser import StudentParser


class TestStudentParser:
    def setup_method(self):
        self.parser = StudentParser()

    def test_parse_dict_returns_student_profile(self):
        raw = {
            "name": "Jane Doe",
            "background": {"degree": "MSc", "field": "CS", "institution": "MIT"},
            "research_interests": ["NLP", "LLMs"],
            "preferred_countries": ["US"],
            "preferred_domains": ["AI"],
        }
        # TODO: uncomment once implemented
        # profile = self.parser.parse_dict(raw)
        # assert profile.name == "Jane Doe"
        pytest.skip("StudentParser.parse_dict not yet implemented")

    def test_parse_file_raises_for_missing_file(self, tmp_path):
        missing = tmp_path / "nonexistent.json"
        # TODO: uncomment once implemented
        # with pytest.raises(FileNotFoundError):
        #     self.parser.parse_file(missing)
        pytest.skip("StudentParser.parse_file not yet implemented")

    def test_extract_interests_handles_strings(self):
        # TODO: test plain string → ResearchInterest coercion
        pytest.skip("StudentParser._extract_interests not yet implemented")
