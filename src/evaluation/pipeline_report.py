"""
Pipeline evaluation models and report exporter.

Captures per-stage validation counts, per-stage timing, and derived
contamination metrics. Exported as both JSON and Markdown.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from ..utils.logger import get_logger

logger = get_logger(__name__)


# ------------------------------------------------------------------ #
# Models                                                               #
# ------------------------------------------------------------------ #

class ValidationStageMetrics(BaseModel):
    """Supervisor counts at each validation gate."""

    retrieved_count: int = 0
    pi_validated_count: int = 0
    country_validated_count: int = 0
    domain_validated_count: int = 0
    evidence_validated_count: int = 0
    recommendation_count: int = 0

    @property
    def pi_rejection_rate(self) -> float:
        if not self.retrieved_count:
            return 0.0
        return round(1 - self.pi_validated_count / self.retrieved_count, 4)

    @property
    def overall_pass_rate(self) -> float:
        if not self.retrieved_count:
            return 0.0
        return round(self.recommendation_count / self.retrieved_count, 4)


class RuntimeMetrics(BaseModel):
    """Wall-clock seconds spent in each major pipeline stage."""

    retrieval_seconds: float = 0.0
    validation_seconds: float = 0.0
    scoring_seconds: float = 0.0
    why_match_seconds: float = 0.0
    export_seconds: float = 0.0
    total_runtime_seconds: float = 0.0


class PipelineReport(BaseModel):
    """
    Full analytics report for one pipeline run.
    Written to <output_dir>/<student_id>_report.json and _report.md.
    """

    student_name: str
    student_id: str = ""
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    pipeline_version: str = "1.0.0"
    validation: ValidationStageMetrics = Field(default_factory=ValidationStageMetrics)
    runtime: RuntimeMetrics = Field(default_factory=RuntimeMetrics)
    # Tier breakdown of final recommendations
    tier_counts: dict[str, int] = Field(default_factory=dict)


# ------------------------------------------------------------------ #
# Exporter                                                             #
# ------------------------------------------------------------------ #

class ReportExporter:
    """
    Writes a PipelineReport to disk as:
      <output_dir>/<student_id>_report.json
      <output_dir>/<student_id>_report.md

    Args:
        output_dir: Directory for report files. Created if absent.
    """

    def __init__(self, output_dir: Path = Path("sample_output")) -> None:
        self.output_dir = output_dir

    def export(self, report: PipelineReport) -> tuple[Path, Path]:
        """
        Write JSON and Markdown report files.

        Returns:
            (json_path, md_path) tuple of written paths.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        stem = _slugify(report.student_id) if report.student_id else "output"

        json_path = self.output_dir / f"{stem}_report.json"
        md_path   = self.output_dir / f"{stem}_report.md"

        self._write_json(report, json_path)
        self._write_markdown(report, md_path)

        logger.info("Report exported: %s | %s", json_path, md_path)
        return json_path, md_path

    # ---------------------------------------------------------------- #

    def _write_json(self, report: PipelineReport, path: Path) -> None:
        payload = json.loads(report.model_dump_json())
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )

    def _write_markdown(self, report: PipelineReport, path: Path) -> None:
        v = report.validation
        r = report.runtime

        lines = [
            f"# Pipeline Report — {report.student_name}",
            "",
            f"**Generated:** {report.generated_at}  ",
            f"**Pipeline version:** {report.pipeline_version}",
            "",
            "## Validation Funnel",
            "",
            f"| Stage              | Count | Rejected |",
            f"|--------------------|------:|---------:|",
            f"| Retrieved          | {v.retrieved_count:>5} |          |",
            f"| PI Validated       | {v.pi_validated_count:>5} | {v.retrieved_count - v.pi_validated_count:>8} |",
            f"| Country Validated  | {v.country_validated_count:>5} | {v.pi_validated_count - v.country_validated_count:>8} |",
            f"| Domain Validated   | {v.domain_validated_count:>5} | {v.country_validated_count - v.domain_validated_count:>8} |",
            f"| Evidence Validated | {v.evidence_validated_count:>5} | {v.domain_validated_count - v.evidence_validated_count:>8} |",
            f"| **Recommendations**| **{v.recommendation_count}** |          |",
            "",
            f"**Overall pass rate:** {v.overall_pass_rate:.1%}  ",
            f"**PI rejection rate:** {v.pi_rejection_rate:.1%}",
            "",
        ]

        if report.tier_counts:
            lines += [
                "## Recommendation Tiers",
                "",
                "| Tier   | Count |",
                "|--------|------:|",
            ]
            for tier in ("reach", "target", "safety"):
                count = report.tier_counts.get(tier, 0)
                lines.append(f"| {tier.capitalize():<6} | {count:>5} |")
            lines.append("")

        lines += [
            "## Runtime",
            "",
            f"| Stage        | Seconds |",
            f"|--------------|--------:|",
            f"| Retrieval    | {r.retrieval_seconds:>7.2f} |",
            f"| Validation   | {r.validation_seconds:>7.2f} |",
            f"| Scoring      | {r.scoring_seconds:>7.2f} |",
            f"| Why Match    | {r.why_match_seconds:>7.2f} |",
            f"| Export       | {r.export_seconds:>7.2f} |",
            f"| **Total**    | **{r.total_runtime_seconds:.2f}** |",
            "",
        ]

        path.write_text("\n".join(lines), encoding="utf-8")


# ------------------------------------------------------------------ #
# Helper                                                               #
# ------------------------------------------------------------------ #

def _slugify(text: str) -> str:
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s-]+", "_", slug)
    return slug or "output"
