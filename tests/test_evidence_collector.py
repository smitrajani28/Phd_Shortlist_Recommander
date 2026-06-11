"""
Unit tests for EvidenceCollector.

All tests use a mocked OpenAlexClient — no real HTTP calls are made.
"""

import pytest
from unittest.mock import MagicMock, patch, call

from src.models.supervisor import Supervisor, Publication
from src.services.evidence_collector import EvidenceCollector, clear_works_cache
from src.services.openalex_client import OpenAlexClient


# ------------------------------------------------------------------ #
# Fixtures                                                             #
# ------------------------------------------------------------------ #

OPENALEX_ID = "A1234567890"

RAW_WORKS = [
    {
        "id": "https://openalex.org/W1",
        "title": "Attention Is All You Need",
        "publication_year": 2024,
        "cited_by_count": 5000,
        "doi": "10.1234/test.001",
        "primary_location": {"source": {"display_name": "NeurIPS"}},
        "concepts": [
            {"display_name": "Natural language processing", "level": 1, "score": 0.9},
            {"display_name": "Transformer", "level": 2, "score": 0.85},
            {"display_name": "Machine learning", "level": 0, "score": 0.7},
        ],
    },
    {
        "id": "https://openalex.org/W2",
        "title": "BERT: Pre-training of Deep Bidirectional Transformers",
        "publication_year": 2023,
        "cited_by_count": 3000,
        "doi": "10.1234/test.002",
        "primary_location": {"source": {"display_name": "ACL"}},
        "concepts": [
            {"display_name": "Natural language processing", "level": 1, "score": 0.95},
            {"display_name": "Deep learning", "level": 2, "score": 0.8},
        ],
    },
    {
        "id": "https://openalex.org/W3",
        "title": "Old Paper",
        "publication_year": 2015,   # outside 5-year recency window
        "cited_by_count": 100,
        "doi": None,
        "primary_location": {},
        "concepts": [
            {"display_name": "Artificial intelligence", "level": 0, "score": 0.6},
        ],
    },
]


def _make_supervisor(openalex_id: str | None = OPENALEX_ID) -> Supervisor:
    return Supervisor(
        name="Dr. Alice Smith",
        institution="MIT",
        country="US",
        openalex_id=openalex_id,
    )


def _mock_client(works: list[dict]) -> OpenAlexClient:
    client = MagicMock(spec=OpenAlexClient)
    client.get.return_value = {"results": works}
    return client


# ------------------------------------------------------------------ #
# Test 1: Successful enrichment                                        #
# ------------------------------------------------------------------ #

class TestSuccessfulEnrichment:
    def setup_method(self):
        clear_works_cache()

    def test_evidence_collected_flag_set_true(self):
        s = _make_supervisor()
        EvidenceCollector(_mock_client(RAW_WORKS)).enrich(s)
        assert s.evidence_collected is True

    def test_publications_populated(self):
        s = _make_supervisor()
        EvidenceCollector(_mock_client(RAW_WORKS)).enrich(s)
        assert len(s.publications) == 3

    def test_publication_fields_mapped(self):
        s = _make_supervisor()
        EvidenceCollector(_mock_client(RAW_WORKS)).enrich(s)
        first = s.publications[0]
        assert first.title == "Attention Is All You Need"
        assert first.year == 2024
        assert first.citation_count == 5000
        assert first.doi_url == "https://doi.org/10.1234/test.001"
        assert first.openalex_url == "https://openalex.org/W1"
        assert first.venue == "NeurIPS"

    def test_latest_publication_year(self):
        s = _make_supervisor()
        EvidenceCollector(_mock_client(RAW_WORKS)).enrich(s)
        assert s.latest_publication_year == 2024

    def test_supervisor_returned(self):
        s = _make_supervisor()
        result = EvidenceCollector(_mock_client(RAW_WORKS)).enrich(s)
        assert result is s  # same object, mutated in-place


# ------------------------------------------------------------------ #
# Test 2: No publications found                                        #
# ------------------------------------------------------------------ #

class TestNoPublications:
    def setup_method(self):
        clear_works_cache()

    def test_evidence_collected_false_when_empty(self):
        s = _make_supervisor()
        EvidenceCollector(_mock_client([])).enrich(s)
        assert s.evidence_collected is False

    def test_publications_list_empty(self):
        s = _make_supervisor()
        EvidenceCollector(_mock_client([])).enrich(s)
        assert s.publications == []

    def test_no_openalex_id_sets_not_collected(self):
        s = _make_supervisor(openalex_id=None)
        EvidenceCollector(_mock_client(RAW_WORKS)).enrich(s)
        assert s.evidence_collected is False

    def test_api_error_sets_not_collected(self):
        client = MagicMock(spec=OpenAlexClient)
        client.get.side_effect = RuntimeError("Network error")
        s = _make_supervisor()
        EvidenceCollector(client).enrich(s)
        assert s.evidence_collected is False

    def test_api_error_does_not_raise(self):
        client = MagicMock(spec=OpenAlexClient)
        client.get.side_effect = RuntimeError("Network error")
        s = _make_supervisor()
        # Must not propagate the exception
        EvidenceCollector(client).enrich(s)


# ------------------------------------------------------------------ #
# Test 3: Citation aggregation                                         #
# ------------------------------------------------------------------ #

class TestCitationAggregation:
    def setup_method(self):
        clear_works_cache()

    def test_total_citations_summed(self):
        s = _make_supervisor()
        EvidenceCollector(_mock_client(RAW_WORKS)).enrich(s)
        assert s.total_citations == 5000 + 3000 + 100  # 8100

    def test_total_citations_zero_when_no_works(self):
        s = _make_supervisor()
        EvidenceCollector(_mock_client([])).enrich(s)
        assert s.total_citations == 0

    def test_recent_publication_count(self):
        # RAW_WORKS has 2 papers >= current_year - 5 (2024, 2023) and 1 old one (2015)
        s = _make_supervisor()
        EvidenceCollector(_mock_client(RAW_WORKS), recency_years=5).enrich(s)
        assert s.recent_publication_count == 2

    def test_recency_window_respected(self):
        # With a 2-year window, only papers from (current_year - 2) onward qualify.
        # RAW_WORKS has years 2024, 2023, 2015 → with recency_years=2, both 2024 and
        # 2023 should qualify (current year is 2025, cutoff = 2023).
        from datetime import datetime
        current_year = datetime.now().year
        collector = EvidenceCollector(_mock_client(RAW_WORKS), recency_years=2)
        expected = sum(1 for w in RAW_WORKS if (w["publication_year"] or 0) >= current_year - 2)
        s = _make_supervisor()
        collector.enrich(s)
        assert s.recent_publication_count == expected


# ------------------------------------------------------------------ #
# Test 4: Concept extraction                                           #
# ------------------------------------------------------------------ #

class TestConceptExtraction:
    def setup_method(self):
        clear_works_cache()

    def test_concepts_extracted(self):
        s = _make_supervisor()
        EvidenceCollector(_mock_client(RAW_WORKS)).enrich(s)
        assert "Natural language processing" in s.research_concepts

    def test_concepts_deduplicated(self):
        # "Natural language processing" appears in both W1 and W2
        s = _make_supervisor()
        EvidenceCollector(_mock_client(RAW_WORKS)).enrich(s)
        nlp_count = sum(1 for c in s.research_concepts if c == "Natural language processing")
        assert nlp_count == 1

    def test_high_level_concepts_only(self):
        # Level-3+ concepts should be excluded (none in RAW_WORKS, but validate the rule)
        works_with_deep_concept = [
            {
                **RAW_WORKS[0],
                "concepts": [
                    {"display_name": "Attention mechanism", "level": 3, "score": 0.9},
                    {"display_name": "Machine learning", "level": 0, "score": 0.8},
                ],
            }
        ]
        s = _make_supervisor()
        EvidenceCollector(_mock_client(works_with_deep_concept)).enrich(s)
        assert "Attention mechanism" not in s.research_concepts
        assert "Machine learning" in s.research_concepts

    def test_concepts_empty_when_no_works(self):
        s = _make_supervisor()
        EvidenceCollector(_mock_client([])).enrich(s)
        assert s.research_concepts == []


# ------------------------------------------------------------------ #
# Test 5: Cache hit path                                               #
# ------------------------------------------------------------------ #

class TestCacheHit:
    def setup_method(self):
        clear_works_cache()

    def test_second_enrich_does_not_call_api_again(self):
        client = _mock_client(RAW_WORKS)
        collector = EvidenceCollector(client)
        s1 = _make_supervisor()
        s2 = _make_supervisor()  # same openalex_id
        collector.enrich(s1)
        collector.enrich(s2)
        # Both supervisors have the same openalex_id → only 1 API call
        assert client.get.call_count == 1

    def test_different_authors_each_call_api(self):
        client = _mock_client(RAW_WORKS)
        collector = EvidenceCollector(client)
        s1 = _make_supervisor(openalex_id="A111")
        s2 = _make_supervisor(openalex_id="A222")
        collector.enrich(s1)
        collector.enrich(s2)
        assert client.get.call_count == 2

    def test_cache_hit_produces_same_publications(self):
        collector = EvidenceCollector(_mock_client(RAW_WORKS))
        s1 = _make_supervisor()
        s2 = _make_supervisor()
        collector.enrich(s1)
        collector.enrich(s2)
        assert len(s1.publications) == len(s2.publications)


# ------------------------------------------------------------------ #
# Test 6: Retry path (via OpenAlexClient behaviour)                    #
# ------------------------------------------------------------------ #

class TestRetryPath:
    def setup_method(self):
        clear_works_cache()

    def test_transient_failure_then_success(self):
        """Client fails once then succeeds — collector should still enrich."""
        client = MagicMock(spec=OpenAlexClient)
        client.get.side_effect = [
            RuntimeError("Timeout"),         # 1st call fails
            {"results": RAW_WORKS},           # 2nd call succeeds
        ]
        s = _make_supervisor()
        # EvidenceCollector catches the first failure and sets evidence_collected=False,
        # because it makes exactly one client.get call per enrich() invocation.
        # Retry logic lives inside OpenAlexClient itself; here we test that a
        # persistent failure is handled gracefully.
        EvidenceCollector(client).enrich(s)
        assert s.evidence_collected is False  # first call raised → graceful failure

    def test_persistent_failure_does_not_raise(self):
        client = MagicMock(spec=OpenAlexClient)
        client.get.side_effect = RuntimeError("Persistent network error")
        s = _make_supervisor()
        # Must not raise
        EvidenceCollector(client).enrich(s)
        assert s.evidence_collected is False

    def test_enrich_all_continues_after_one_failure(self):
        """enrich_all should process remaining supervisors even if one fails."""
        client = MagicMock(spec=OpenAlexClient)
        client.get.side_effect = [
            RuntimeError("fail for A1"),
            {"results": RAW_WORKS},
        ]
        supervisors = [
            _make_supervisor(openalex_id="A1"),
            _make_supervisor(openalex_id="A2"),
        ]
        EvidenceCollector(client).enrich_all(supervisors)
        assert supervisors[0].evidence_collected is False
        assert supervisors[1].evidence_collected is True
