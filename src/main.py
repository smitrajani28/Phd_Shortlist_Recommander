"""
Entry point for the PhD Shortlist Builder.

Retrieval slice (stages 1-7, stdout):
    python -m src.main sample_input/student.json

Full pipeline (all 8 stages, writes JSON file):
    python -m src.main sample_input/student.json --full

Full pipeline with analytics report:
    python -m src.main sample_input/student.json --report

Explicit output path:
    python -m src.main sample_input/student.json --full --output sample_output/alice.json

Outcome learning (standalone, no student profile needed):
    python -m src.main sample_input/outcomes.csv --learn
"""

import argparse
import json
from pathlib import Path

from .utils.config import get_settings
from .utils.logger import configure_logging, get_logger
from .pipelines.shortlist_pipeline import ShortlistPipeline
from .evaluation.pipeline_report import ReportExporter

logger = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PhD Shortlist Builder")
    parser.add_argument("input", type=Path, help="Student profile JSON or outcomes CSV (with --learn)")
    parser.add_argument(
        "--full", action="store_true",
        help="Run all 8 pipeline stages and export a JSON file",
    )
    parser.add_argument(
        "--report", action="store_true",
        help="Run full pipeline and save JSON + Markdown analytics report",
    )
    parser.add_argument(
        "--learn", action="store_true",
        help="Load outcomes CSV and export outcome learning report",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Explicit output file path (--full / --report only)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()
    configure_logging(level=settings.log_level, log_file=settings.log_file)

    if args.learn:
        # Standalone outcome learning — no student profile needed
        from .feedback.outcome_learner import OutcomeLearner
        learner = OutcomeLearner(
            outcome_weights=settings.outcome_weights,
            wrong_person_threshold=settings.wrong_person_threshold,
        )
        learner.load_csv(args.input)
        learner.build_supervisor_stats()
        report = learner.generate_report()
        learner.export_report()
        print(json.dumps({
            "total_supervisors": report.total_supervisors,
            "total_outcomes": report.total_outcomes,
            "average_success_rate": report.average_success_rate,
        }, indent=2))
        return

    pipeline = ShortlistPipeline(settings=settings)

    if args.report:
        output, report = pipeline.run_with_report(
            input_path=args.input,
            output_path=args.output,
        )
        report_exporter = ReportExporter(output_dir=pipeline.exporter.output_dir)
        json_path, md_path = report_exporter.export(report)
        print(json.dumps({
            "student_name": output.student_name,
            "recommendation_count": output.recommendation_count,
            "generated_at": output.generated_at,
            "report_json": str(json_path),
            "report_md": str(md_path),
            "total_runtime_seconds": report.runtime.total_runtime_seconds,
        }, indent=2))

    elif args.full:
        result = pipeline.run(input_path=args.input, output_path=args.output)
        print(json.dumps({
            "student_name": result.student_name,
            "recommendation_count": result.recommendation_count,
            "generated_at": result.generated_at,
            "pipeline_version": result.pipeline_version,
        }, indent=2))

    else:
        result = pipeline.run_retrieval(input_path=args.input)
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
