"""
Unit tests for OpenAlexRetriever._parse_author().
All tests use mocked payloads — no real HTTP calls.
"""
import pytest
from unittest.mock import MagicMock
from src.retrievers.openalex_retriever import OpenAlexRetriever


@pytest.fixture
def retriever():
    r = OpenAlexRetriever.__new__(OpenAlexRetriever)
    r.client = MagicMock()
    r.max_results = 10
    return r


# ── helpers ────────────────────────────────────────────────────────────────────

def _base_raw(name="Test Author", openalex_id="https://openalex.org/A123"):
    return {
        "id": openalex_id,
        "display_name": name,
        "orcid": None,
        "summary_stats": {"h_index": 5},
        "counts_by_year": [],
        "x_concepts": [],
        "last_known_institutions": None,
        "affiliations": [],
    }


# ── institution extraction ─────────────────────────────────────────────────────

def test_institution_from_last_known_institutions(retriever):
    raw = _base_raw("Manning")
    raw["last_known_institutions"] = [
        {"display_name": "Stanford University", "country_code": "US"}
    ]
    sv = retriever._parse_author(raw)
    assert sv.institution == "Stanford University"
    assert sv.country == "US"


def test_institution_fallback_to_affiliations(retriever):
    """last_known_institutions is null/empty → should use affiliations[0].institution."""
    raw = _base_raw("Thomas Wolf")
    raw["last_known_institutions"] = None
    raw["affiliations"] = [
        {"institution": {"display_name": "Hugging Face", "country_code": "US"}, "years": [2023]}
    ]
    sv = retriever._parse_author(raw)
    assert sv.institution == "Hugging Face"
    assert sv.country == "US"


def test_institution_fallback_empty_list(retriever):
    """last_known_institutions is [] → should fall back to affiliations."""
    raw = _base_raw()
    raw["last_known_institutions"] = []
    raw["affiliations"] = [
        {"institution": {"display_name": "MIT", "country_code": "US"}, "years": [2022]}
    ]
    sv = retriever._parse_author(raw)
    assert sv.institution == "MIT"


def test_unknown_institution_when_all_missing(retriever):
    raw = _base_raw()
    raw["last_known_institutions"] = None
    raw["affiliations"] = []
    sv = retriever._parse_author(raw)
    assert sv.institution == "Unknown Institution"
    assert sv.country == ""


# ── country extraction ─────────────────────────────────────────────────────────

def test_country_from_last_known_institutions(retriever):
    raw = _base_raw()
    raw["last_known_institutions"] = [{"display_name": "University of Toronto", "country_code": "CA"}]
    sv = retriever._parse_author(raw)
    assert sv.country == "CA"


def test_country_from_affiliations_fallback(retriever):
    raw = _base_raw()
    raw["last_known_institutions"] = []
    raw["affiliations"] = [{"institution": {"display_name": "CNRS", "country_code": "FR"}, "years": [2021]}]
    sv = retriever._parse_author(raw)
    assert sv.country == "FR"


# ── x_concepts (no level field in current API) ────────────────────────────────

def test_research_areas_sorted_by_score(retriever):
    raw = _base_raw()
    raw["last_known_institutions"] = [{"display_name": "X", "country_code": "US"}]
    raw["x_concepts"] = [
        {"display_name": "Machine Learning", "score": 0.9},
        {"display_name": "Computer Science", "score": 0.95},
        {"display_name": "NLP", "score": 0.85},
    ]
    sv = retriever._parse_author(raw)
    assert sv.research_areas[0] == "Computer Science"
    assert sv.research_areas[1] == "Machine Learning"
    assert sv.research_areas[2] == "NLP"


def test_research_areas_empty_concepts(retriever):
    raw = _base_raw()
    raw["last_known_institutions"] = [{"display_name": "X", "country_code": "US"}]
    raw["x_concepts"] = []
    sv = retriever._parse_author(raw)
    assert sv.research_areas == []


def test_research_areas_no_level_filter_needed(retriever):
    """Old code filtered by level <= 2 which broke when level was absent. Ensure no level is required."""
    raw = _base_raw()
    raw["last_known_institutions"] = [{"display_name": "X", "country_code": "US"}]
    raw["x_concepts"] = [
        {"display_name": "Deep Learning", "score": 0.8},   # no 'level' key
        {"display_name": "Transformers", "score": 0.75},
    ]
    sv = retriever._parse_author(raw)
    assert len(sv.research_areas) == 2


# ── openalex_id ────────────────────────────────────────────────────────────────

def test_openalex_id_extracted(retriever):
    raw = _base_raw(openalex_id="https://openalex.org/A5023888391")
    raw["last_known_institutions"] = [{"display_name": "X", "country_code": "US"}]
    sv = retriever._parse_author(raw)
    assert sv.openalex_id == "A5023888391"
