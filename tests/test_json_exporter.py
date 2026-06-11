"""
Tests for JsonExporter.

Covers: path creation, content validation, empty shortlist,
        deterministic output, student_id filename derivation,
        metadata fields, sort_keys guarantee.
"""

import json
import pytest
from datetime import datetime, timezone
from pathlib import Path

from src.models.recommendation import ShortlistOutput, Recommendation, ScoreBreakdown
from src.models.supervisor import Supervisor
from src.exporters.json_exporter import JsonExporter, _slugify


# ------------------------------------------------------------------ #
# Factories                                                            #
# ------------------------------------------------------------------ #

TIMESTAMP = "2025-01-15T10:00:00+00:00"


def _supervisor() -> Supervisor:
    return Supervisor(name="Dr. Test", institution="MIT", country="US")


def _breakdown() -> ScoreBreakdown:
    return ScoreBreakdown(
        overall_score=0.82,
        research_alignment=0.90,
        publication_activity=0.80,
        citation_impact=0.70,
        evidence_quality=0.75,
        country_preference=1.00,
    )


def _recommendation(rank: int = 1) -> Recommendation:
    return Recommendation(
        rank=rank,
        supervisor=_supervisor(),
        score=0.82,
        score_breakdown=_breakdown(),
        tier="reach",
        why_match="Strong match on NLP topics.",
    )


def _output(
    name: str = "Jane Doe",
    student_id: str = "jane_doe",
    recs: int = 2,
) -> ShortlistOutput:
    return ShortlistOutput(
        student_name=name,
        student_id=student_id,
        total_candidates_evaluated=50,
        recommendation_count=recs,
        generated_at=TIMESTAMP,
        recommendations=[_recommendation(rank=i + 1) for i in range(recs)],
    )


# ====================================================================
# 1. Export path creation
# ====================================================================

class TestExportPathCreation:
    def test_creates_output_directory(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c"
        exporter = JsonExporter(output_dir=nested)
        exporter.export(_output())
        assert nested.exists()

    def test_filename_uses_student_id(self, tmp_path):
        exporter = JsonExporter(output_dir=tmp_path)
        written = exporter.export(_output(student_id="alice_chen"))
        assert written.name == "alice_chen.json"

    def test_filename_falls_back_to_output_when_no_id(self, tmp_path):
        exporter = JsonExporter(output_dir=tmp_path)
        written = exporter.export(_output(student_id=""))
        assert written.name == "output.json"

    def test_explicit_output_path_is_used(self, tmp_path):
        custom = tmp_path / "custom" / "my_result.json"
        exporter = JsonExporter(output_dir=tmp_path)
        written = exporter.export(_output(), output_path=custom)
        assert written == custom
        assert custom.exists()

    def test_returns_path(self, tmp_path):
        exporter = JsonExporter(output_dir=tmp_path)
        result = exporter.export(_output())
        assert isinstance(result, Path)
        assert result.exists()


# ====================================================================
# 2. Export content validation
# ====================================================================

class TestExportContent:
    def test_valid_json_written(self, tmp_path):
        exporter = JsonExporter(output_dir=tmp_path)
        path = exporter.export(_output())
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_top_level_fields_present(self, tmp_path):
        exporter = JsonExporter(output_dir=tmp_path)
        path = exporter.export(_output())
        data = json.loads(path.read_text())
        assert "student_name" in data
        assert "generated_at" in data
        assert "recommendations" in data
        assert "pipeline_version" in data
        assert "recommendation_count" in data
        assert "total_candidates_evaluated" in data

    def test_student_name_correct(self, tmp_path):
        exporter = JsonExporter(output_dir=tmp_path)
        path = exporter.export(_output(name="Bob Smith"))
        data = json.loads(path.read_text())
        assert data["student_name"] == "Bob Smith"

    def test_recommendations_list_correct_length(self, tmp_path):
        exporter = JsonExporter(output_dir=tmp_path)
        path = exporter.export(_output(recs=3))
        data = json.loads(path.read_text())
        assert len(data["recommendations"]) == 3

    def test_recommendation_has_required_fields(self, tmp_path):
        exporter = JsonExporter(output_dir=tmp_path)
        path = exporter.export(_output(recs=1))
        rec = json.loads(path.read_text())["recommendations"][0]
        for field in ("rank", "score", "score_breakdown", "supervisor", "tier", "why_match"):
            assert field in rec, f"Missing field: {field}"

    def test_supervisor_fields_present(self, tmp_path):
        exporter = JsonExporter(output_dir=tmp_path)
        path = exporter.export(_output(recs=1))
        sup = json.loads(path.read_text())["recommendations"][0]["supervisor"]
        for field in ("name", "institution", "country"):
            assert field in sup

    def test_score_breakdown_fields_present(self, tmp_path):
        exporter = JsonExporter(output_dir=tmp_path)
        path = exporter.export(_output(recs=1))
        bd = json.loads(path.read_text())["recommendations"][0]["score_breakdown"]
        for field in (
            "overall_score", "research_alignment", "publication_activity",
            "citation_impact", "evidence_quality", "country_preference",
        ):
            assert field in bd

    def test_pipeline_version_is_string(self, tmp_path):
        exporter = JsonExporter(output_dir=tmp_path)
        path = exporter.export(_output())
        data = json.loads(path.read_text())
        assert isinstance(data["pipeline_version"], str)
        assert len(data["pipeline_version"]) > 0

    def test_recommendation_count_matches_list_length(self, tmp_path):
        exporter = JsonExporter(output_dir=tmp_path)
        path = exporter.export(_output(recs=4))
        data = json.loads(path.read_text())
        assert data["recommendation_count"] == len(data["recommendations"])

    def test_indent_is_2(self, tmp_path):
        exporter = JsonExporter(output_dir=tmp_path)
        path = exporter.export(_output())
        raw = path.read_text()
        # indent=2 means lines starting with exactly "  " (2 spaces)
        assert any(line.startswith("  ") for line in raw.splitlines())

    def test_utf8_encoding(self, tmp_path):
        out = _output(name="José García")
        exporter = JsonExporter(output_dir=tmp_path)
        path = exporter.export(out)
        raw = path.read_bytes().decode("utf-8")
        assert "José García" in raw


# ====================================================================
# 3. Empty shortlist export
# ====================================================================

class TestEmptyShortlistExport:
    def test_empty_recommendations_exported(self, tmp_path):
        out = ShortlistOutput(
            student_name="Empty Student",
            student_id="empty_student",
            total_candidates_evaluated=0,
            recommendation_count=0,
            generated_at=TIMESTAMP,
            recommendations=[],
        )
        exporter = JsonExporter(output_dir=tmp_path)
        path = exporter.export(out)
        data = json.loads(path.read_text())
        assert data["recommendations"] == []
        assert data["recommendation_count"] == 0

    def test_empty_export_is_valid_json(self, tmp_path):
        out = ShortlistOutput(
            student_name="X",
            student_id="x",
            total_candidates_evaluated=0,
            generated_at=TIMESTAMP,
        )
        exporter = JsonExporter(output_dir=tmp_path)
        path = exporter.export(out)
        assert json.loads(path.read_text()) is not None


# ====================================================================
# 4. Deterministic output
# ====================================================================

class TestDeterministicOutput:
    def test_same_input_same_output(self, tmp_path):
        out = _output()
        e = JsonExporter(output_dir=tmp_path)
        p1 = tmp_path / "a.json"
        p2 = tmp_path / "b.json"
        e.export(out, output_path=p1)
        e.export(out, output_path=p2)
        assert p1.read_text() == p2.read_text()

    def test_keys_are_sorted(self, tmp_path):
        exporter = JsonExporter(output_dir=tmp_path)
        path = exporter.export(_output())
        data = json.loads(path.read_text())
        keys = list(data.keys())
        assert keys == sorted(keys)

    def test_recommendation_keys_are_sorted(self, tmp_path):
        exporter = JsonExporter(output_dir=tmp_path)
        path = exporter.export(_output(recs=1))
        rec_keys = list(json.loads(path.read_text())["recommendations"][0].keys())
        assert rec_keys == sorted(rec_keys)

    def test_overwrite_same_path_produces_identical_file(self, tmp_path):
        out = _output()
        exporter = JsonExporter(output_dir=tmp_path)
        dest = tmp_path / "test.json"
        exporter.export(out, output_path=dest)
        content1 = dest.read_text()
        exporter.export(out, output_path=dest)
        content2 = dest.read_text()
        assert content1 == content2


# ====================================================================
# _slugify helper
# ====================================================================

class TestSlugify:
    @pytest.mark.parametrize("name,expected", [
        ("Jane Doe", "jane_doe"),
        ("Alice  Chen", "alice_chen"),
        ("José García", "josé_garcía"),   # unicode preserved in filenames
        ("", "output"),
        ("  ", "output"),
        ("Dr. Alice-Smith", "dr_alice_smith"),  # hyphens → underscores
    ])
    def test_slugify(self, name: str, expected: str):
        assert _slugify(name) == expected
