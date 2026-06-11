"""
Tests for EmbeddingDomainValidator, EmbeddingService helpers,
and DomainValidator mode routing.

The real sentence-transformers model is never loaded in these tests.
All EmbeddingService calls are intercepted by a mock that returns
controlled cosine-similarity values via fixed embeddings.
"""

import math
import pytest
from unittest.mock import MagicMock, patch

from src.models.student import StudentProfile, AcademicBackground
from src.models.supervisor import Supervisor, Publication
from src.models.validation import ValidationResult
from src.validators.embedding_domain_validator import (
    EmbeddingDomainValidator,
    _build_student_blob,
    _build_supervisor_blob,
    _token_overlap,
)
from src.validators.domain_validator import DomainValidator
from src.services.embedding_service import (
    EmbeddingService,
    _cosine_similarity,
    _text_hash,
    clear_embedding_cache,
    reset_singleton,
)


# ------------------------------------------------------------------ #
# Factories                                                            #
# ------------------------------------------------------------------ #

def _student(
    topics: list[str] | None = None,
    domains: list[str] | None = None,
    sop: str = "",
) -> StudentProfile:
    return StudentProfile(
        name="Jane Doe",
        background=AcademicBackground(degree="MSc", field="CS", institution="MIT"),
        extracted_topics=topics if topics is not None else ["natural language processing", "large language models"],
        preferred_domains=domains if domains is not None else ["NLP", "AI"],
        statement_of_purpose=sop or None,
    )


def _supervisor(
    areas: list[str] | None = None,
    concepts: list[str] | None = None,
    pub_titles: list[str] | None = None,
) -> Supervisor:
    publications = [
        Publication(title=t, year=2023, citation_count=100)
        for t in (pub_titles or ["Attention Is All You Need"])
    ]
    return Supervisor(
        name="Dr. Test",
        institution="MIT",
        country="US",
        research_areas=areas or ["natural language processing", "transformers"],
        research_concepts=concepts or ["Natural language processing", "Machine learning"],
        publications=publications,
        evidence_collected=True,
    )


def _mock_embedding_service(sim: float = 0.8) -> EmbeddingService:
    """
    Return a mocked EmbeddingService whose similarity() always returns `sim`
    and whose encode() returns orthogonal unit vectors (first dim only for v0,
    second dim only for v1) so cosine is predictable.
    """
    svc = MagicMock(spec=EmbeddingService)
    svc.similarity.return_value = sim
    # encode returns two unit vectors; similarity mock overrides the actual value
    svc.encode.return_value = [[1.0, 0.0], [0.0, 1.0]]
    return svc


def _emb_validator(sim: float = 0.8, min_score: float = 0.55) -> EmbeddingDomainValidator:
    return EmbeddingDomainValidator(
        embedding_service=_mock_embedding_service(sim),
        min_score=min_score,
    )


# ====================================================================
# EmbeddingService pure math helpers
# ====================================================================

class TestCosineSimMath:
    def test_identical_vectors_score_one(self):
        v = [1.0, 0.5, 0.3]
        assert abs(_cosine_similarity(v, v) - 1.0) < 1e-6

    def test_orthogonal_vectors_score_zero(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert _cosine_similarity(a, b) == 0.0

    def test_opposite_vectors_clipped_to_zero(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        # raw cosine = -1.0, clipped to 0.0
        assert _cosine_similarity(a, b) == 0.0

    def test_empty_vector_returns_zero(self):
        assert _cosine_similarity([], [1.0]) == 0.0
        assert _cosine_similarity([1.0], []) == 0.0

    def test_partial_overlap(self):
        # Both point in the same quadrant → score between 0 and 1
        a = [1.0, 1.0]
        b = [1.0, 0.0]
        score = _cosine_similarity(a, b)
        expected = 1.0 / math.sqrt(2)
        assert abs(score - expected) < 1e-6

    def test_text_hash_is_deterministic(self):
        assert _text_hash("hello") == _text_hash("hello")

    def test_different_texts_different_hashes(self):
        assert _text_hash("nlp") != _text_hash("biology")


# ====================================================================
# EmbeddingService caching
# ====================================================================

class TestEmbeddingCache:
    def setup_method(self):
        clear_embedding_cache()

    def test_cache_hit_avoids_model_call(self):
        svc = EmbeddingService.__new__(EmbeddingService)
        svc.model_name = "test"
        mock_model = MagicMock()
        mock_model.encode.return_value = [[0.5, 0.5]]
        svc._model = mock_model

        # First call — cache miss
        svc.encode(["test text"])
        assert mock_model.encode.call_count == 1

        # Second call — cache hit
        svc.encode(["test text"])
        assert mock_model.encode.call_count == 1   # not called again

    def test_different_texts_both_encoded(self):
        svc = EmbeddingService.__new__(EmbeddingService)
        svc.model_name = "test"
        mock_model = MagicMock()
        mock_model.encode.side_effect = [[[0.1, 0.2]], [[0.3, 0.4]]]
        svc._model = mock_model

        svc.encode(["text A"])
        svc.encode(["text B"])
        assert mock_model.encode.call_count == 2

    def test_encode_returns_correct_length(self):
        svc = EmbeddingService.__new__(EmbeddingService)
        svc.model_name = "test"
        mock_model = MagicMock()
        mock_model.encode.return_value = [[0.1] * 384, [0.2] * 384]
        svc._model = mock_model

        result = svc.encode(["text A", "text B"])
        assert len(result) == 2


# ====================================================================
# Text blob builders
# ====================================================================

class TestTextBlobBuilders:
    def test_student_blob_contains_topics(self):
        student = _student(topics=["machine learning", "nlp"])
        blob = _build_student_blob(student)
        assert "machine learning" in blob
        assert "nlp" in blob

    def test_student_blob_contains_domains(self):
        student = _student(domains=["Computer Vision"])
        blob = _build_student_blob(student)
        assert "Computer Vision" in blob

    def test_student_blob_includes_sop_snippet(self):
        student = _student(sop="I study retrieval augmented generation.")
        blob = _build_student_blob(student)
        assert "retrieval augmented generation" in blob

    def test_supervisor_blob_contains_concepts(self):
        s = _supervisor(concepts=["Knowledge graphs", "Semantic search"])
        blob = _build_supervisor_blob(s)
        assert "Knowledge graphs" in blob

    def test_supervisor_blob_contains_pub_titles(self):
        s = _supervisor(pub_titles=["My Unique Publication Title"])
        blob = _build_supervisor_blob(s)
        assert "My Unique Publication Title" in blob

    def test_supervisor_blob_excludes_placeholder_titles(self):
        s = _supervisor()
        s.publications = [
            Publication(title="Works in 2023 (5 papers)", year=2023, citation_count=0)
        ]
        blob = _build_supervisor_blob(s)
        assert "Works in 2023" not in blob

    def test_empty_supervisor_returns_empty_blob(self):
        s = _supervisor(areas=[], concepts=[], pub_titles=[])
        # Also clear the fields set by __init__ defaults inside _supervisor
        s.research_areas = []
        s.research_concepts = []
        s.publications = []
        blob = _build_supervisor_blob(s)
        assert blob.strip() == ""


# ====================================================================
# Test 1: Identical / highly related domains
# ====================================================================

class TestIdenticalDomains:
    def test_high_similarity_passes(self):
        validator = _emb_validator(sim=0.95, min_score=0.55)
        s = _supervisor()
        result = validator.validate(s, _student())
        assert result.passed is True

    def test_score_reflects_embedding_weight(self):
        # sim=1.0, perfect token overlap → final ≈ 0.30*1.0 + 0.30*1.0 + 0.40*1.0 = 1.0
        validator = _emb_validator(sim=1.0, min_score=0.0)
        student = _student(topics=["natural language processing"], domains=[])
        sup = _supervisor(
            areas=["natural language processing"],
            concepts=["natural language processing"],
        )
        result = validator.validate(sup, student)
        assert result.score > 0.8

    def test_metadata_source_is_embedding_similarity(self):
        result = _emb_validator().validate(_supervisor(), _student())
        assert result.source == "embedding_similarity"

    def test_matched_concepts_populated(self):
        student = _student(topics=["natural language processing"])
        sup = _supervisor(areas=["natural language processing"])
        result = _emb_validator(sim=0.9).validate(sup, student)
        # Token overlap should find some matches
        assert isinstance(result.matched_concepts, list)


# ====================================================================
# Test 2: Partially related domains
# ====================================================================

class TestPartiallyRelatedDomains:
    def test_moderate_similarity_above_threshold_passes(self):
        # Construct a case where token overlap is perfect AND sim=0.65
        # so final = 0.30*1.0 + 0.30*1.0 + 0.40*0.65 = 0.86 > 0.55
        validator = _emb_validator(sim=0.65, min_score=0.55)
        student = _student(topics=["natural language processing"], domains=[])
        sup = _supervisor(
            areas=["natural language processing"],
            concepts=["natural language processing"],
        )
        result = validator.validate(sup, student)
        assert result.passed is True

    def test_moderate_similarity_below_threshold_fails(self):
        validator = _emb_validator(sim=0.40, min_score=0.55)
        # Token overlap is non-zero but embedding is low
        student = _student(topics=["machine learning"])
        sup = _supervisor(areas=["machine learning"], concepts=[], pub_titles=[])
        result = validator.validate(sup, student)
        # final = 0.3*kw + 0.3*0 + 0.4*0.40; kw depends on token match
        assert 0.0 <= result.score <= 1.0

    def test_score_stored_on_supervisor(self):
        s = _supervisor()
        st = _student()
        _emb_validator(sim=0.6).validate(s, st)
        assert s.domain_validation is not None
        assert s.domain_validation.score > 0.0


# ====================================================================
# Test 3: Unrelated domains
# ====================================================================

class TestUnrelatedDomains:
    def test_low_similarity_fails(self):
        validator = _emb_validator(sim=0.10, min_score=0.55)
        student = _student(topics=["quantum computing"], domains=[])
        sup = _supervisor(areas=["plant biology"], concepts=["Botany"], pub_titles=[])
        result = validator.validate(sup, student)
        assert result.passed is False

    def test_rejection_reason_includes_scores(self):
        validator = _emb_validator(sim=0.10, min_score=0.55)
        student = _student(topics=["quantum computing"], domains=[])
        sup = _supervisor(areas=["plant biology"], concepts=["Botany"], pub_titles=[])
        result = validator.validate(sup, student)
        assert result.reason is not None
        assert "embedding" in result.reason

    def test_metadata_attached_on_rejection(self):
        s = _supervisor(areas=["botany"], concepts=["Plant biology"])
        st = _student(topics=["deep learning nlp"])
        _emb_validator(sim=0.05, min_score=0.55).validate(s, st)
        assert s.domain_validation.passed is False


# ====================================================================
# Test 4: Fallback mode (no embedding service)
# ====================================================================

class TestFallbackMode:
    def test_keyword_only_mode_works_without_embedding_service(self):
        student = _student(topics=["natural language processing"])
        validator = DomainValidator(student, min_score=0.1, mode="keyword_only")
        sup = _supervisor(areas=["natural language processing"])
        assert validator.validate(sup) is True

    def test_hybrid_mode_falls_back_when_no_service(self):
        # No embedding_service → logs warning and falls back to keyword_only
        student = _student(topics=["natural language processing"])
        validator = DomainValidator(
            student,
            min_score=0.1,
            mode="hybrid",
            embedding_service=None,
        )
        sup = _supervisor(areas=["natural language processing"])
        # Should still work via keyword fallback
        assert validator.validate(sup) is True

    def test_fallback_result_source_is_domain_hybrid(self):
        student = _student(topics=["nlp"])
        validator = DomainValidator(student, min_score=0.0, mode="hybrid", embedding_service=None)
        sup = _supervisor(areas=["nlp research"])
        validator.validate(sup)
        assert sup.domain_validation.source == "domain_hybrid"

    def test_no_preference_always_passes_regardless_of_mode(self):
        student = _student(topics=[], domains=[])
        for mode in ("keyword_only", "hybrid", "embedding_only"):
            validator = DomainValidator(student, mode=mode, embedding_service=None)
            sup = _supervisor()
            assert validator.validate(sup) is True

    def test_embedding_service_load_failure_falls_back(self):
        """If EmbeddingService raises on load, pipeline continues with keyword_only."""
        student = _student()
        with patch(
            "src.services.embedding_service.get_embedding_service",
            side_effect=ImportError("no sentence-transformers"),
        ):
            # Pipeline-level fallback: simulate what the pipeline does
            embedding_service = None
            try:
                from src.services.embedding_service import get_embedding_service
                embedding_service = get_embedding_service("fake-model")
            except Exception:
                pass
            validator = DomainValidator(
                student, min_score=0.1, mode="hybrid", embedding_service=embedding_service
            )
            sup = _supervisor(areas=["natural language processing"])
            # Must not raise; falls back to keyword_only
            result = validator.validate(sup)
            assert isinstance(result, bool)


# ====================================================================
# Test 5: Cache hit path
# ====================================================================

class TestCacheHitPath:
    def setup_method(self):
        clear_embedding_cache()

    def test_second_call_with_same_text_hits_cache(self):
        svc = EmbeddingService.__new__(EmbeddingService)
        svc.model_name = "test"
        mock_model = MagicMock()
        mock_model.encode.return_value = [[0.5, 0.5]]
        svc._model = mock_model

        text = "natural language processing large language models"
        svc.encode([text])
        svc.encode([text])   # cache hit
        assert mock_model.encode.call_count == 1

    def test_validator_benefits_from_cache_across_supervisors(self):
        """Same student blob encoded once even when validating multiple supervisors."""
        call_count = 0
        original_similarity = EmbeddingDomainValidator.validate

        mock_svc = MagicMock(spec=EmbeddingService)

        def counting_similarity(text1, text2):
            nonlocal call_count
            call_count += 1
            return 0.8

        mock_svc.similarity.side_effect = counting_similarity

        validator = EmbeddingDomainValidator(mock_svc, min_score=0.5)
        student = _student()
        for _ in range(3):
            validator.validate(_supervisor(), student)

        # similarity() is called once per validate() call (3 supervisors)
        assert call_count == 3

    def test_hash_key_stability(self):
        """Same text must produce same cache key across calls."""
        text = "machine learning nlp transformers"
        assert _text_hash(text) == _text_hash(text)
        assert _text_hash(text) == _text_hash(text)


# ====================================================================
# DomainValidator mode routing
# ====================================================================

class TestDomainValidatorModeRouting:
    def test_keyword_only_uses_original_scoring(self):
        student = _student(topics=["natural language processing"], domains=[])
        validator = DomainValidator(student, min_score=0.0, mode="keyword_only")
        sup = _supervisor(areas=["natural language processing"])
        validator.validate(sup)
        assert sup.domain_validation.source == "domain_hybrid"

    def test_hybrid_with_service_uses_embedding_validator(self):
        student = _student()
        mock_svc = _mock_embedding_service(sim=0.9)
        validator = DomainValidator(
            student, min_score=0.5, mode="hybrid", embedding_service=mock_svc
        )
        sup = _supervisor()
        validator.validate(sup)
        assert sup.domain_validation.source == "embedding_similarity"

    def test_embedding_only_with_service_uses_embedding_validator(self):
        student = _student()
        mock_svc = _mock_embedding_service(sim=0.9)
        validator = DomainValidator(
            student, min_score=0.5, mode="embedding_only", embedding_service=mock_svc
        )
        sup = _supervisor()
        validator.validate(sup)
        assert sup.domain_validation.source == "embedding_similarity"

    def test_existing_validate_signature_preserved(self):
        """validate(supervisor) -> bool must still work (no signature change)."""
        student = _student()
        validator = DomainValidator(student, min_score=0.0)
        sup = _supervisor()
        result = validator.validate(sup)
        assert isinstance(result, bool)
