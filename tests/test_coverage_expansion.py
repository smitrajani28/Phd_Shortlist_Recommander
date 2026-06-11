"""
Tests for coverage expansion and enrichment components:
  - topic expansion
  - retrieval deduplication
  - EmailExtractor
  - ProgramLinker
"""

import pytest
from unittest.mock import MagicMock, patch

from src.retrievers.openalex_retriever import OpenAlexRetriever, expand_topics
from src.extractors.email_extractor import EmailExtractor, _is_institutional
from src.linkers.program_linker import ProgramLinker, _extract_google_url, _program_name_from_url
from src.models.supervisor import Supervisor, PIMetadata


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _make_supervisor(
    name="Test Prof",
    institution="MIT",
    country="US",
    openalex_id="A123",
    h_index=20,
    works_count=50,
    cited_by_count=500,
    profile_url="https://web.mit.edu/~test",
    pi_metadata=None,
) -> Supervisor:
    return Supervisor(
        name=name,
        institution=institution,
        country=country,
        openalex_id=openalex_id,
        h_index=h_index,
        works_count=works_count,
        cited_by_count=cited_by_count,
        research_areas=["machine learning", "NLP"],
        profile_url=profile_url,
        pi_metadata=pi_metadata or PIMetadata(pi_verified=True, verification_source="openalex"),
    )


# ================================================================== #
# PART 1 — Topic expansion                                            #
# ================================================================== #

class TestTopicExpansion:
    def test_base_topics_preserved_first(self):
        result = expand_topics(["large language models"], max_topics=10)
        assert result[0] == "large language models"

    def test_known_topic_is_expanded(self):
        result = expand_topics(["large language models"], max_topics=10)
        assert len(result) > 1

    def test_expansion_includes_known_variants(self):
        result = expand_topics(["large language models"], max_topics=10)
        assert "LLM" in result or "generative AI" in result

    def test_max_topics_cap_respected(self):
        result = expand_topics(["large language models", "machine learning", "NLP"], max_topics=5)
        assert len(result) <= 5

    def test_no_duplicates_in_expanded(self):
        result = expand_topics(["natural language processing", "NLP"], max_topics=20)
        assert len(result) == len(set(r.lower() for r in result))

    def test_unknown_topic_returned_unchanged(self):
        result = expand_topics(["quantum entanglement"], max_topics=10)
        assert result == ["quantum entanglement"]

    def test_empty_input_returns_empty(self):
        assert expand_topics([], max_topics=10) == []

    def test_multiple_topics_all_expanded(self):
        result = expand_topics(["large language models", "machine learning"], max_topics=20)
        # Should have more than just the 2 base topics
        assert len(result) > 2


# ================================================================== #
# PART 2 — Retrieval deduplication                                    #
# ================================================================== #

class TestRetrievalDeduplication:
    def _make_retriever(self):
        r = OpenAlexRetriever.__new__(OpenAlexRetriever)
        r.client = MagicMock()
        r.per_topic_works = 10
        r.authors_per_topic = 5
        r.max_topics = 5
        r.max_results = 50
        r.max_workers = 2
        return r

    def test_duplicate_openalex_id_removed(self):
        r = self._make_retriever()
        sv1 = _make_supervisor(name="Alice", institution="MIT", openalex_id="A001")
        sv2 = _make_supervisor(name="Alice", institution="MIT", openalex_id="A001")
        # Patch search_authors_by_topic to return the same author twice
        r.search_authors_by_topic = MagicMock(side_effect=[[sv1], [sv2]])
        result = r.search_authors_by_topics(["topic1", "topic2"])
        assert len(result) == 1

    def test_duplicate_name_institution_removed(self):
        r = self._make_retriever()
        sv1 = _make_supervisor(name="Alice Smith", institution="MIT", openalex_id="A001")
        sv2 = _make_supervisor(name="Alice Smith", institution="MIT", openalex_id="")
        r.search_authors_by_topic = MagicMock(side_effect=[[sv1], [sv2]])
        result = r.search_authors_by_topics(["topic1", "topic2"])
        assert len(result) == 1

    def test_different_authors_both_kept(self):
        r = self._make_retriever()
        sv1 = _make_supervisor(name="Alice", institution="MIT", openalex_id="A001")
        sv2 = _make_supervisor(name="Bob", institution="Stanford", openalex_id="A002")
        r.search_authors_by_topic = MagicMock(side_effect=[[sv1], [sv2]])
        result = r.search_authors_by_topics(["topic1", "topic2"])
        assert len(result) == 2

    def test_retriever_respects_authors_per_topic(self):
        r = self._make_retriever()
        r.authors_per_topic = 3
        # 5 works each with 2 authorships = 10 potential author IDs
        works = [
            {"authorships": [
                {"author": {"id": f"https://openalex.org/A{i}"}},
                {"author": {"id": f"https://openalex.org/A{i+100}"}},
            ]}
            for i in range(5)
        ]
        r._get_works_for_topic = MagicMock(return_value=works)
        r.get_author_details = MagicMock(
            side_effect=lambda aid: _make_supervisor(openalex_id=aid)
        )
        result = r.search_authors_by_topic("some topic")
        assert len(result) <= 3


# ================================================================== #
# PART 3 — EmailExtractor                                             #
# ================================================================== #

class TestIsInstitutional:
    def test_edu_email_is_institutional(self):
        assert _is_institutional("alice@mit.edu")

    def test_ac_uk_is_institutional(self):
        assert _is_institutional("alice@ox.ac.uk")

    def test_gmail_is_not_institutional(self):
        assert not _is_institutional("alice@gmail.com")

    def test_yahoo_is_not_institutional(self):
        assert not _is_institutional("alice@yahoo.com")

    def test_empty_string_is_not_institutional(self):
        assert not _is_institutional("")

    def test_no_at_sign_is_not_institutional(self):
        assert not _is_institutional("notanemail")


class TestEmailExtractor:
    def _extractor(self):
        e = EmailExtractor.__new__(EmailExtractor)
        e.timeout = 5
        e.crawl_delay = 0
        return e

    def test_extracts_mailto_link(self):
        extractor = self._extractor()
        html = '<html><body><a href="mailto:alice@mit.edu">Email</a></body></html>'
        sv = _make_supervisor(profile_url="https://web.mit.edu/~alice")
        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = html
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp
            result = extractor._scrape_page("https://web.mit.edu/~alice", sv, source="profile_url")
        assert result.email == "alice@mit.edu"
        assert result.source == "profile_url"

    def test_rejects_gmail_from_page(self):
        extractor = self._extractor()
        html = '<html><body><a href="mailto:alice@gmail.com">Email</a></body></html>'
        sv = _make_supervisor(profile_url="https://web.mit.edu/~alice")
        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = html
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp
            result = extractor._scrape_page("https://web.mit.edu/~alice", sv, source="profile_url")
        assert result.email == ""

    def test_falls_back_to_regex_in_text(self):
        extractor = self._extractor()
        html = "<html><body>Contact: bob@stanford.edu for info</body></html>"
        sv = _make_supervisor(profile_url="https://stanford.edu/~bob")
        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = html
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp
            result = extractor._scrape_page("https://stanford.edu/~bob", sv, source="profile_url")
        assert result.email == "bob@stanford.edu"

    def test_returns_empty_on_http_failure(self):
        extractor = self._extractor()
        sv = _make_supervisor(profile_url="https://example.edu/~fail")
        with patch("httpx.get", side_effect=Exception("timeout")):
            result = extractor._scrape_page("https://example.edu/~fail", sv, source="profile_url")
        assert result.email == ""

    def test_no_profile_url_returns_not_found(self):
        extractor = self._extractor()
        sv = _make_supervisor(profile_url=None)
        sv.pi_metadata = PIMetadata(pi_verified=True, verification_source="openalex")
        result = extractor.extract(sv)
        assert result.source == "not_found"


# ================================================================== #
# PART 4 — ProgramLinker                                              #
# ================================================================== #

class TestProgramLinkerHelpers:
    def test_extract_google_url_from_wrapped_href(self):
        href = "/url?q=https://mit.edu/graduate&sa=U"
        assert _extract_google_url(href) == "https://mit.edu/graduate"

    def test_extract_google_url_passthrough_for_direct_http(self):
        assert _extract_google_url("https://example.edu") == "https://example.edu"

    def test_extract_google_url_empty_for_relative(self):
        assert _extract_google_url("/relative/path") == ""

    def test_program_name_from_url(self):
        name = _program_name_from_url("https://cs.mit.edu/graduate-admissions", "MIT")
        assert "MIT" in name
        assert len(name) > 3


class TestProgramLinker:
    def _linker(self):
        lnk = ProgramLinker.__new__(ProgramLinker)
        lnk.timeout = 5
        lnk.crawl_delay = 0
        lnk.max_programs = 3
        return lnk

    def test_unknown_institution_returns_empty(self):
        lnk = self._linker()
        sv = _make_supervisor(institution="Unknown Institution")
        result = lnk.link(sv)
        assert result == []

    def test_empty_institution_returns_empty(self):
        lnk = self._linker()
        sv = _make_supervisor(institution="")
        result = lnk.link(sv)
        assert result == []

    def test_returns_at_most_max_programs(self):
        lnk = self._linker()
        lnk.max_programs = 2
        sv = _make_supervisor(institution="MIT")
        # HTML with 5 distinct program-like links
        html = """<html><body>
            <a href="/url?q=https://grad.mit.edu/phd-program">PhD</a>
            <a href="/url?q=https://grad.mit.edu/admissions">Admissions</a>
            <a href="/url?q=https://eecs.mit.edu/graduate">EECS Graduate</a>
            <a href="/url?q=https://csail.mit.edu/doctoral">Doctoral</a>
        </body></html>"""
        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = html
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp
            result = lnk.link(sv)
        assert len(result) <= 2

    def test_http_failure_returns_empty_not_raises(self):
        lnk = self._linker()
        sv = _make_supervisor(institution="MIT")
        with patch("httpx.get", side_effect=Exception("network error")):
            result = lnk.link(sv)
        assert result == []

    def test_linked_program_has_required_fields(self):
        lnk = self._linker()
        sv = _make_supervisor(institution="Stanford University")
        html = '<html><body><a href="/url?q=https://cs.stanford.edu/graduate">PhD Program</a></body></html>'
        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = html
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp
            result = lnk.link(sv)
        if result:
            prog = result[0]
            assert prog.program_name
            assert prog.institution == "Stanford University"
            assert prog.url.startswith("http")
            assert prog.status in ("open", "unknown")
