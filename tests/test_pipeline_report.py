"""
Tests for PipelineReport models and ReportExporter.
Covers: model fields, derived metrics, JSON export, Markdown export,
        file naming, directory creation, and run_with_report() integration.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.evaluation.pipeline_report import (
    PipelineReport,
    ValidationStageMetrics,
    RuntimeMetrics,
    ReportExporter,
    _slugify,
)


# ------------------------------------------------------------------ #
# Factories                                                            #
# ------------------------------------------------------------------ #

def _validation(**kwargs) -> ValidationStageMetrics:
    defaults = dict(
        retrieved_count=142,
        pi_validated_count=58,
        country_validated_count=41,
        domain_validated_count=27,
        evidence_validated_count=19,
        recommendation_count=19,
    )
    return ValidationStageMetrics(**{**defaults, **kwargs})


def _runtime(**kwargs) -> RuntimeMetrics:
    defaults = dict(
        retrieval_seconds=120.5,
        validation_seconds=80.2,
        scoring_seconds=5.1,
        why_match_seconds=200.3,
        export_seconds=1.0,
        total_runtime_seconds=407.1,
    )
    return RuntimeMetrics(**{**defaults, **kwargs})


def _report(**kwargs) -> PipelineReport:
    defaults = dict(
        student_name="Jane Doe",
        student_id="jane_doe",
        pipeline_version="1.0.0",
        validation=_validation(),
        runtime=_runtime(),
        tier_counts={"reach": 4, "target": 10, "safety": 5},
    )
    return PipelineReport(**{**defaults, **kwargs})


# ====================================================================
# ValidationStageMetrics
# ====================================================================

class TestValidationStageMetrics:
    def test_all_fields_present(self):
        m = _validation()
        assert m.retrieved_count == 142
        assert m.pi_validated_count == 58
        assert m.country_validated_count == 41
        assert m.domain_validated_count == 27
        assert m.evidence_validated_count == 19
        assert m.recommendation_count == 19

    def test_pi_rejection_rate(self):
        m = _validation(retrieved_count=100, pi_validated_count=60)
        assert abs(m.pi_rejection_rate - 0.40) < 1e-4

    def test_pi_rejection_rate_zero_retrieved(self):
        m = _validation(retrieved_count=0, pi_validated_count=0)
        assert m.pi_rejection_rate == 0.0

    def test_overall_pass_rate(self):
        m = _validation(retrieved_count=100, recommendation_count=20)
        assert abs(m.overall_pass_rate - 0.20) < 1e-4

    def test_overall_pass_rate_zero_retrieved(self):
        m = _validation(retrieved_count=0, recommendation_count=0)
        assert m.overall_pass_rate == 0.0

    def test_default_values_are_zero(self):
        m = ValidationStageMetrics()
        assert m.retrieved_count == 0
        assert m.recommendation_count == 0


# ====================================================================
# RuntimeMetrics
# ====================================================================

class TestRuntimeMetrics:
    def test_all_fields_present(self):
        r = _runtime()
        assert r.retrieval_seconds == 120.5
        assert r.total_runtime_seconds == 407.1

    def test_default_values_are_zero(self):
        r = RuntimeMetrics()
        assert r.total_runtime_seconds == 0.0
        assert r.scoring_seconds == 0.0


# ====================================================================
# PipelineReport
# ====================================================================

class TestPipelineReport:
    def test_student_name_stored(self):
        r = _report(student_name="Bob Smith")
        assert r.student_name == "Bob Smith"

    def test_generated_at_is_set_automatically(self):
        r = PipelineReport(student_name="X", validation=_validation(), runtime=_runtime())
        assert r.generated_at  # non-empty ISO timestamp

    def test_tier_counts_stored(self):
        r = _report(tier_counts={"reach": 3, "target": 8})
        assert r.tier_counts["reach"] == 3
        assert r.tier_counts["target"] == 8

    def test_pipeline_version_default(self):
        r = PipelineReport(student_name="X", validation=_validation(), runtime=_runtime())
        assert r.pipeline_version == "1.0.0"


# ====================================================================
# ReportExporter — JSON
# ====================================================================

class TestReportExporterJSON:
    def test_creates_output_directory(self, tmp_path):
        nested = tmp_path / "a" / "b"
        ReportExporter(output_dir=nested).export(_report())
        assert nested.exists()

    def test_json_file_created(self, tmp_path):
        json_path, _ = ReportExporter(tmp_path).export(_report())
        assert json_path.exists()

    def test_json_filename_uses_student_id(self, tmp_path):
        json_path, _ = ReportExporter(tmp_path).export(_report(student_id="alice_chen"))
        assert json_path.name == "alice_chen_report.json"

    def test_json_filename_fallback(self, tmp_path):
        json_path, _ = ReportExporter(tmp_path).export(_report(student_id=""))
        assert json_path.name == "output_report.json"

    def test_json_is_valid(self, tmp_path):
        json_path, _ = ReportExporter(tmp_path).export(_report())
        data = json.loads(json_path.read_text())
        assert isinstance(data, dict)

    def test_json_contains_required_fields(self, tmp_path):
        json_path, _ = ReportExporter(tmp_path).export(_report())
        data = json.loads(json_path.read_text())
        assert "student_name" in data
        assert "validation" in data
        assert "runtime" in data
        assert "tier_counts" in data
        assert "pipeline_version" in data

    def test_json_validation_counts_correct(self, tmp_path):
        json_path, _ = ReportExporter(tmp_path).export(_report())
        v = json.loads(json_path.read_text())["validation"]
        assert v["retrieved_count"] == 142
        assert v["pi_validated_count"] == 58
        assert v["recommendation_count"] == 19

    def test_json_runtime_correct(self, tmp_path):
        json_path, _ = ReportExporter(tmp_path).export(_report())
        r = json.loads(json_path.read_text())["runtime"]
        assert r["total_runtime_seconds"] == 407.1

    def test_json_keys_sorted(self, tmp_path):
        json_path, _ = ReportExporter(tmp_path).export(_report())
        data = json.loads(json_path.read_text())
        keys = list(data.keys())
        assert keys == sorted(keys)

    def test_json_deterministic(self, tmp_path):
        rpt = _report()
        e = ReportExporter(tmp_path)
        p1 = tmp_path / "a.json"
        p2 = tmp_path / "b.json"
        e._write_json(rpt, p1)
        e._write_json(rpt, p2)
        assert p1.read_text() == p2.read_text()


# ====================================================================
# ReportExporter — Markdown
# ====================================================================

class TestReportExporterMarkdown:
    def test_md_file_created(self, tmp_path):
        _, md_path = ReportExporter(tmp_path).export(_report())
        assert md_path.exists()

    def test_md_filename_uses_student_id(self, tmp_path):
        _, md_path = ReportExporter(tmp_path).export(_report(student_id="bob"))
        assert md_path.name == "bob_report.md"

    def test_md_contains_student_name(self, tmp_path):
        _, md_path = ReportExporter(tmp_path).export(_report(student_name="Jane Doe"))
        assert "Jane Doe" in md_path.read_text()

    def test_md_contains_retrieved_count(self, tmp_path):
        _, md_path = ReportExporter(tmp_path).export(_report())
        assert "142" in md_path.read_text()

    def test_md_contains_recommendation_count(self, tmp_path):
        _, md_path = ReportExporter(tmp_path).export(_report())
        assert "19" in md_path.read_text()

    def test_md_contains_runtime(self, tmp_path):
        _, md_path = ReportExporter(tmp_path).export(_report())
        assert "407" in md_path.read_text()

    def test_md_contains_tier_section(self, tmp_path):
        _, md_path = ReportExporter(tmp_path).export(
            _report(tier_counts={"reach": 3, "target": 8, "safety": 2})
        )
        content = md_path.read_text()
        assert "Reach" in content or "reach" in content.lower()

    def test_md_contains_validation_funnel_header(self, tmp_path):
        _, md_path = ReportExporter(tmp_path).export(_report())
        assert "Validation Funnel" in md_path.read_text()

    def test_md_no_tier_section_when_empty(self, tmp_path):
        _, md_path = ReportExporter(tmp_path).export(_report(tier_counts={}))
        assert "Recommendation Tiers" not in md_path.read_text()


# ====================================================================
# _slugify
# ====================================================================

class TestSlugify:
    @pytest.mark.parametrize("name,expected", [
        ("Jane Doe", "jane_doe"),
        ("alice_chen", "alice_chen"),
        ("", "output"),
        ("  ", "output"),
    ])
    def test_slugify(self, name, expected):
        assert _slugify(name) == expected


# ====================================================================
# run_with_report() pipeline integration
# ====================================================================

class TestRunWithReport:
    """Verify run_with_report() returns correct types and wires counts properly."""

    def _build_pipeline(self):
        from src.utils.config import Settings
        from src.pipelines.shortlist_pipeline import ShortlistPipeline
        settings = Settings(
            pi_use_network=False,
            evidence_min_recent_publications=0,
            evidence_min_total_citations=0,
            evidence_max_publication_gap=50,
            domain_min_score=0.0,
        )
        return ShortlistPipeline(settings=settings)

    def test_returns_tuple(self, tmp_path):
        pipeline = self._build_pipeline()
        from src.models.student import StudentProfile, AcademicBackground
        from src.models.recommendation import ShortlistOutput
        from src.evaluation.pipeline_report import PipelineReport

        mock_profile = StudentProfile(
            name="Test Student",
            background=AcademicBackground(degree="MSc", field="CS", institution="MIT"),
            extracted_topics=["nlp"],
        )

        with patch.object(pipeline.parser, "parse_file", return_value=mock_profile), \
             patch.object(pipeline.openalex, "retrieve", return_value=[]), \
             patch.object(pipeline, "_retrieve", return_value=[]):
            result = pipeline.run_with_report(input_path=tmp_path / "dummy.json")

        assert isinstance(result, tuple)
        assert len(result) == 2
        output, report = result
        assert isinstance(output, ShortlistOutput)
        assert isinstance(report, PipelineReport)

    def test_report_has_student_name(self, tmp_path):
        pipeline = self._build_pipeline()
        from src.models.student import StudentProfile, AcademicBackground

        mock_profile = StudentProfile(
            name="Alice Kim",
            background=AcademicBackground(degree="MSc", field="CS", institution="MIT"),
        )

        with patch.object(pipeline.parser, "parse_file", return_value=mock_profile), \
             patch.object(pipeline, "_retrieve", return_value=[]):
            _, report = pipeline.run_with_report(input_path=tmp_path / "dummy.json")

        assert report.student_name == "Alice Kim"

    def test_report_runtime_total_is_positive(self, tmp_path):
        pipeline = self._build_pipeline()
        from src.models.student import StudentProfile, AcademicBackground

        mock_profile = StudentProfile(
            name="Test",
            background=AcademicBackground(degree="MSc", field="CS", institution="MIT"),
        )

        with patch.object(pipeline.parser, "parse_file", return_value=mock_profile), \
             patch.object(pipeline, "_retrieve", return_value=[]):
            _, report = pipeline.run_with_report(input_path=tmp_path / "dummy.json")

        assert report.runtime.total_runtime_seconds >= 0.0

    def test_validation_counts_reflect_empty_pipeline(self, tmp_path):
        pipeline = self._build_pipeline()
        from src.models.student import StudentProfile, AcademicBackground

        mock_profile = StudentProfile(
            name="Test",
            background=AcademicBackground(degree="MSc", field="CS", institution="MIT"),
        )

        with patch.object(pipeline.parser, "parse_file", return_value=mock_profile), \
             patch.object(pipeline, "_retrieve", return_value=[]):
            _, report = pipeline.run_with_report(input_path=tmp_path / "dummy.json")

        assert report.validation.retrieved_count == 0
        assert report.validation.recommendation_count == 0
