"""
Tests for WhyMatchGenerator, LLMClient Protocol, and fallback logic.
No real LLM calls are made — all LLM interactions use stub implementations.
"""

import pytest
from unittest.mock import MagicMock

from src.models.student import StudentProfile, AcademicBackground, ResearchInterest
from src.models.supervisor import Supervisor, Publication
from src.models.recommendation import Recommendation, ScoreBreakdown
from src.generators.why_match_generator import WhyMatchGenerator, _word_count
from src.generators.llm_client import LLMClient, OpenAILLMClient


# ------------------------------------------------------------------ #
# Factories                                                            #
# ------------------------------------------------------------------ #

def _student(
    interests: list[str] | None = None,
    domains: list[str] | None = None,
    sop: str = "I am interested in retrieval-augmented generation and LLMs.",
) -> StudentProfile:
    return StudentProfile(
        name="Jane Doe",
        background=AcademicBackground(degree="MSc", field="CS", institution="MIT"),
        extracted_topics=interests or ["retrieval augmented generation", "large language models", "nlp"],
        preferred_domains=domains or ["NLP", "AI"],
        statement_of_purpose=sop,
    )


def _supervisor(
    name: str = "Dr. Alice Smith",
    concepts: list[str] | None = None,
    pubs: list[dict] | None = None,
) -> Supervisor:
    publications = [
        Publication(
            title=p.get("title", "Untitled"),
            year=p.get("year", 2023),
            citation_count=p.get("citations", 100),
        )
        for p in (pubs or [
            {"title": "RAG for Scientific QA", "year": 2024, "citations": 500},
            {"title": "Dense Retrieval at Scale", "year": 2023, "citations": 200},
        ])
    ]
    return Supervisor(
        name=name,
        institution="Stanford University",
        country="US",
        research_concepts=concepts or ["Retrieval-augmented generation", "Natural language processing"],
        publications=publications,
        evidence_collected=True,
    )


def _recommendation(rank: int = 1, supervisor: Supervisor | None = None) -> Recommendation:
    s = supervisor or _supervisor()
    return Recommendation(
        rank=rank,
        supervisor=s,
        score=0.85 - (rank - 1) * 0.05,
        score_breakdown=ScoreBreakdown(
            overall_score=0.85,
            research_alignment=0.9,
            publication_activity=0.8,
            citation_impact=0.7,
            evidence_quality=0.8,
            country_preference=1.0,
        ),
        tier="reach",
    )


def _mock_llm(response: str = "This is a test explanation.") -> LLMClient:
    client = MagicMock(spec=LLMClient)
    client.generate.return_value = response
    return client


def _generator(llm=None, top_n: int = 50, max_words: int = 120) -> WhyMatchGenerator:
    return WhyMatchGenerator(llm_client=llm, top_n=top_n, max_words=max_words)


# ====================================================================
# LLMClient Protocol
# ====================================================================

class TestLLMClientProtocol:
    def test_mock_satisfies_protocol(self):
        """Any object with generate(prompt: str) -> str satisfies LLMClient."""
        mock = _mock_llm()
        assert isinstance(mock, LLMClient)

    def test_callable_stub_satisfies_protocol(self):
        class StubClient:
            def generate(self, prompt: str) -> str:
                return "stub response"

        assert isinstance(StubClient(), LLMClient)

    def test_openai_client_satisfies_protocol(self):
        """OpenAILLMClient structurally satisfies LLMClient (without importing openai)."""
        # Verify it has the right method signature without actually calling OpenAI
        assert hasattr(OpenAILLMClient, "generate")


# ====================================================================
# Prompt construction
# ====================================================================

class TestPromptConstruction:
    def test_prompt_contains_student_name(self):
        gen = _generator()
        prompt = gen._build_prompt(_student(), _supervisor())
        assert "Jane Doe" in prompt

    def test_prompt_contains_supervisor_name(self):
        gen = _generator()
        prompt = gen._build_prompt(_student(), _supervisor(name="Prof. Bob"))
        assert "Prof. Bob" in prompt

    def test_prompt_contains_student_interest(self):
        gen = _generator()
        prompt = gen._build_prompt(
            _student(interests=["machine learning", "nlp"]),
            _supervisor(),
        )
        assert "machine learning" in prompt

    def test_prompt_contains_supervisor_concept(self):
        gen = _generator()
        prompt = gen._build_prompt(
            _student(),
            _supervisor(concepts=["Graph neural networks", "Knowledge graphs"]),
        )
        assert "Graph neural networks" in prompt

    def test_prompt_contains_publication_title(self):
        gen = _generator()
        prompt = gen._build_prompt(
            _student(),
            _supervisor(pubs=[{"title": "My Unique Paper Title", "year": 2024, "citations": 10}]),
        )
        assert "My Unique Paper Title" in prompt

    def test_prompt_contains_institution(self):
        gen = _generator()
        prompt = gen._build_prompt(_student(), _supervisor())
        assert "Stanford University" in prompt

    def test_prompt_has_constraint_instruction(self):
        gen = _generator()
        prompt = gen._build_prompt(_student(), _supervisor())
        # Template instructs 2–3 sentences and 120 words
        assert "120" in prompt or "2" in prompt

    def test_top_3_pubs_only(self):
        gen = _generator()
        many_pubs = [
            {"title": f"Paper {i}", "year": 2020 + i, "citations": i * 10}
            for i in range(8)
        ]
        prompt = gen._build_prompt(_student(), _supervisor(pubs=many_pubs))
        # Only top 3 by citation count should appear; Paper 7 (70 cites) should be there
        assert "Paper 7" in prompt
        # Paper 0 (0 cites) should not be in top 3
        assert "Paper 0" not in prompt


# ====================================================================
# Fallback generation
# ====================================================================

class TestFallbackGeneration:
    def test_fallback_is_non_empty(self):
        gen = _generator(llm=None)
        result = gen._fallback(_student(), _supervisor())
        assert result.strip() != ""

    def test_fallback_references_supervisor_name(self):
        gen = _generator(llm=None)
        result = gen._fallback(_student(), _supervisor(name="Prof. Carol"))
        assert "Prof. Carol" in result

    def test_fallback_references_institution(self):
        gen = _generator(llm=None)
        result = gen._fallback(_student(), _supervisor())
        assert "Stanford University" in result

    def test_fallback_references_matching_concept(self):
        gen = _generator(llm=None)
        student = _student(interests=["retrieval augmented generation"])
        supervisor = _supervisor(concepts=["Retrieval-augmented generation", "NLP"])
        result = gen._fallback(student, supervisor)
        # Should mention the matched concept somewhere
        assert "retrieval" in result.lower() or "augmented" in result.lower()

    def test_fallback_references_publication(self):
        gen = _generator(llm=None)
        supervisor = _supervisor(pubs=[{"title": "Key Paper on RAG", "year": 2024, "citations": 500}])
        result = gen._fallback(_student(), supervisor)
        assert "Key Paper on RAG" in result

    def test_fallback_works_with_no_publications(self):
        gen = _generator(llm=None)
        s = _supervisor()
        s.publications = []
        result = gen._fallback(_student(), s)
        assert result.strip() != ""

    def test_fallback_works_with_no_concepts(self):
        gen = _generator(llm=None)
        s = _supervisor(concepts=[])
        s.research_areas = ["machine learning"]
        result = gen._fallback(_student(), s)
        assert result.strip() != ""

    def test_fallback_used_when_no_llm_configured(self):
        gen = _generator(llm=None)
        result = gen.generate(_student(), _supervisor())
        assert result.strip() != ""


# ====================================================================
# LLM failure path
# ====================================================================

class TestLLMFailurePath:
    def test_exception_falls_back(self):
        failing_llm = MagicMock(spec=LLMClient)
        failing_llm.generate.side_effect = RuntimeError("API timeout")
        gen = _generator(llm=failing_llm)
        result = gen.generate(_student(), _supervisor())
        assert result.strip() != ""

    def test_empty_response_falls_back(self):
        gen = _generator(llm=_mock_llm(response=""))
        result = gen.generate(_student(), _supervisor())
        assert result.strip() != ""

    def test_whitespace_only_response_falls_back(self):
        gen = _generator(llm=_mock_llm(response="   \n  "))
        result = gen.generate(_student(), _supervisor())
        assert result.strip() != ""

    def test_oversized_response_falls_back(self):
        long_response = " ".join(["word"] * 200)  # 200 words, limit is 120
        gen = _generator(llm=_mock_llm(response=long_response), max_words=120)
        result = gen.generate(_student(), _supervisor())
        # Should be the fallback, not the oversized LLM response
        assert _word_count(result) <= 120

    def test_llm_called_once_per_generate(self):
        llm = _mock_llm("Good match.")
        gen = _generator(llm=llm)
        gen.generate(_student(), _supervisor())
        assert llm.generate.call_count == 1

    def test_score_unchanged_after_failure(self):
        """Scoring must remain unchanged regardless of LLM output."""
        failing_llm = MagicMock(spec=LLMClient)
        failing_llm.generate.side_effect = RuntimeError("fail")
        gen = _generator(llm=failing_llm)
        rec = _recommendation()
        original_score = rec.score
        gen.generate_batch(_student(), [rec])
        assert rec.score == original_score


# ====================================================================
# Recommendation updates
# ====================================================================

class TestRecommendationUpdates:
    def test_why_match_populated(self):
        gen = _generator(llm=_mock_llm("Great match explanation."))
        rec = _recommendation()
        gen.generate_batch(_student(), [rec])
        assert rec.why_match == "Great match explanation."

    def test_all_recommendations_get_why_match(self):
        gen = _generator(llm=None)  # fallback for all
        recs = [_recommendation(rank=i) for i in range(1, 6)]
        gen.generate_batch(_student(), recs)
        assert all(r.why_match.strip() != "" for r in recs)

    def test_rank_order_unchanged(self):
        gen = _generator(llm=_mock_llm("Some explanation."))
        recs = [_recommendation(rank=i) for i in range(1, 4)]
        gen.generate_batch(_student(), recs)
        assert [r.rank for r in recs] == [1, 2, 3]

    def test_scores_unchanged_after_batch(self):
        gen = _generator(llm=_mock_llm("explanation"))
        recs = [_recommendation(rank=i) for i in range(1, 4)]
        original_scores = [r.score for r in recs]
        gen.generate_batch(_student(), recs)
        assert [r.score for r in recs] == original_scores

    def test_tiers_unchanged_after_batch(self):
        gen = _generator(llm=_mock_llm("explanation"))
        recs = [_recommendation(rank=i) for i in range(1, 4)]
        gen.generate_batch(_student(), recs)
        assert all(r.tier == "reach" for r in recs)

    def test_returns_same_list_reference(self):
        gen = _generator(llm=None)
        recs = [_recommendation()]
        result = gen.generate_batch(_student(), recs)
        assert result is recs


# ====================================================================
# Cost control: top_n
# ====================================================================

class TestCostControl:
    def test_llm_called_only_for_top_n(self):
        llm = _mock_llm("LLM explanation.")
        gen = _generator(llm=llm, top_n=2)
        recs = [_recommendation(rank=i) for i in range(1, 5)]  # 4 recommendations
        gen.generate_batch(_student(), recs)
        # LLM should be called for rank 1 and 2 only
        assert llm.generate.call_count == 2

    def test_beyond_top_n_uses_fallback(self):
        llm = _mock_llm("LLM text")
        gen = _generator(llm=llm, top_n=1)
        recs = [_recommendation(rank=1), _recommendation(rank=2)]
        gen.generate_batch(_student(), recs)
        # rank 2 should not get the LLM text; it gets the fallback
        assert recs[1].why_match != "LLM text"
        assert recs[1].why_match.strip() != ""

    def test_top_n_zero_all_fallback(self):
        llm = _mock_llm("LLM text")
        gen = _generator(llm=llm, top_n=0)
        recs = [_recommendation(rank=1)]
        gen.generate_batch(_student(), recs)
        assert llm.generate.call_count == 0
        assert recs[0].why_match.strip() != ""


# ====================================================================
# Word count helper
# ====================================================================

class TestWordCount:
    def test_empty_string(self):
        assert _word_count("") == 0

    def test_single_word(self):
        assert _word_count("hello") == 1

    def test_counts_correctly(self):
        assert _word_count("one two three four five") == 5

    def test_ignores_extra_whitespace(self):
        assert _word_count("  one  two  ") == 2
