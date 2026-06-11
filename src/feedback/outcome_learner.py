"""
Outcome learning from historical email contact data.

Converts a CSV of past student→supervisor email outcomes into per-supervisor
success scores that adjust future rankings.

CSV format:
    student_id, supervisor_id, institution, area, sent_at, outcome

Supported outcomes and default weights:
    ADMIT           +5   (strongest positive signal)
    INTERVIEW       +3
    POSITIVE_REPLY  +2
    REJECT          -2
    NO_REPLY        -1
    BOUNCE          -3
    OUT_OF_OFFICE    0   (neutral — temporary absence)
    NOT_RECRUITING  -5
    WRONG_PERSON   -10   (data quality issue — hard penalty)

Safety rule:
    If wrong_person_rate > threshold (default 0.20), success_score is
    further reduced to reflect unreliable contact data for that supervisor.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from ..utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Default outcome weights (overridden via Settings)
# ---------------------------------------------------------------------------
DEFAULT_OUTCOME_WEIGHTS: dict[str, int] = {
    "ADMIT":          5,
    "INTERVIEW":      3,
    "POSITIVE_REPLY": 2,
    "REJECT":        -2,
    "NO_REPLY":      -1,
    "BOUNCE":        -3,
    "OUT_OF_OFFICE":  0,
    "NOT_RECRUITING": -5,
    "WRONG_PERSON":  -10,
}

# Positive outcomes used for admit_rate and positive_rate
_ADMIT_OUTCOMES   = frozenset({"ADMIT"})
_POSITIVE_OUTCOMES = frozenset({"ADMIT", "INTERVIEW", "POSITIVE_REPLY"})
_WRONG_PERSON_OUTCOME = "WRONG_PERSON"

# Score normalisation: theoretical max per email = weight of ADMIT = 5
_MAX_WEIGHT_PER_EMAIL = 5


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class OutcomeRecord(BaseModel):
    """A single historical email contact outcome."""

    student_id: str
    supervisor_id: str
    institution: str = ""
    area: str = ""
    sent_at: str = ""
    outcome: str


class SupervisorOutcomeStats(BaseModel):
    """Aggregated outcome statistics for one supervisor."""

    supervisor_id: str
    total_emails: int = 0
    admit_count: int = 0
    positive_count: int = 0
    wrong_person_count: int = 0
    raw_score_sum: int = 0

    admit_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    positive_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    wrong_person_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    success_score: float = Field(default=0.0, ge=0.0, le=1.0)


class OutcomeLearningReport(BaseModel):
    """Summary report produced by OutcomeLearner.generate_report()."""

    generated_at: str = Field(
        default_factory=lambda: datetime.now().isoformat()
    )
    total_supervisors: int = 0
    total_outcomes: int = 0
    average_success_rate: float = 0.0
    best_performing_supervisors: list[SupervisorOutcomeStats] = Field(default_factory=list)
    worst_performing_supervisors: list[SupervisorOutcomeStats] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# OutcomeLearner
# ---------------------------------------------------------------------------

class OutcomeLearner:
    """
    Loads historical email outcomes and converts them to per-supervisor
    success scores used by RecommendationScorer.

    Usage:
        learner = OutcomeLearner()
        learner.load_csv(Path("outcomes.csv"))
        learner.build_supervisor_stats()
        score = learner.get_supervisor_score("A1234567")  # 0.0–1.0

    Args:
        outcome_weights:          Map of outcome string → integer weight.
        wrong_person_threshold:   Rate above which wrong_person penalty is applied.
    """

    def __init__(
        self,
        outcome_weights: dict[str, int] | None = None,
        wrong_person_threshold: float = 0.20,
    ) -> None:
        self.outcome_weights = outcome_weights or DEFAULT_OUTCOME_WEIGHTS
        self.wrong_person_threshold = wrong_person_threshold
        self._records: list[OutcomeRecord] = []
        self._stats: dict[str, SupervisorOutcomeStats] = {}   # supervisor_id → stats

    # ------------------------------------------------------------------ #
    # Data loading                                                         #
    # ------------------------------------------------------------------ #

    def load_csv(self, path: Path) -> int:
        """
        Load outcome records from a CSV file.

        Expected columns (order-independent, header required):
            student_id, supervisor_id, institution, area, sent_at, outcome

        Args:
            path: Path to the CSV file.

        Returns:
            Number of records loaded.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If required columns are missing.
        """
        if not path.exists():
            raise FileNotFoundError(f"Outcomes file not found: {path}")

        loaded = 0
        with path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            _validate_csv_headers(reader.fieldnames or [])
            for row in reader:
                outcome_str = row["outcome"].strip().upper()
                if outcome_str not in self.outcome_weights:
                    logger.warning("Unknown outcome '%s' — skipping row", outcome_str)
                    continue
                self._records.append(OutcomeRecord(
                    student_id=row.get("student_id", "").strip(),
                    supervisor_id=row.get("supervisor_id", "").strip(),
                    institution=row.get("institution", "").strip(),
                    area=row.get("area", "").strip(),
                    sent_at=row.get("sent_at", "").strip(),
                    outcome=outcome_str,
                ))
                loaded += 1

        logger.info("Loaded %d outcome records from %s", loaded, path)
        return loaded

    # ------------------------------------------------------------------ #
    # Stats computation                                                    #
    # ------------------------------------------------------------------ #

    def build_supervisor_stats(self) -> dict[str, SupervisorOutcomeStats]:
        """
        Aggregate all loaded records into per-supervisor statistics.

        Computes admit_rate, positive_rate, wrong_person_rate, and success_score.
        Applies the wrong_person penalty when wrong_person_rate > threshold.

        Returns:
            Dict of supervisor_id → SupervisorOutcomeStats.
        """
        # Aggregate raw counts per supervisor
        counts: dict[str, dict] = defaultdict(lambda: {
            "total": 0, "admit": 0, "positive": 0,
            "wrong_person": 0, "score_sum": 0,
        })

        for rec in self._records:
            sid = rec.supervisor_id
            counts[sid]["total"] += 1
            counts[sid]["score_sum"] += self.outcome_weights.get(rec.outcome, 0)
            if rec.outcome in _ADMIT_OUTCOMES:
                counts[sid]["admit"] += 1
            if rec.outcome in _POSITIVE_OUTCOMES:
                counts[sid]["positive"] += 1
            if rec.outcome == _WRONG_PERSON_OUTCOME:
                counts[sid]["wrong_person"] += 1

        self._stats = {}
        for sid, c in counts.items():
            total = c["total"]
            admit_rate       = c["admit"]        / total
            positive_rate    = c["positive"]     / total
            wrong_person_rate = c["wrong_person"] / total

            # Normalise raw_score_sum to [0, 1] against theoretical maximum
            max_possible = total * _MAX_WEIGHT_PER_EMAIL
            raw_normalised = (c["score_sum"] / max_possible) if max_possible > 0 else 0.0
            # Shift from [-1, 1] range to [0, 1]: (x + 1) / 2
            min_possible = total * min(self.outcome_weights.values())
            range_size = max_possible - min_possible
            if range_size > 0:
                normalised = (c["score_sum"] - min_possible) / range_size
            else:
                normalised = 0.5

            success_score = round(min(max(normalised, 0.0), 1.0), 4)

            # Safety: wrong_person penalty reduces trustworthiness of all data
            if wrong_person_rate > self.wrong_person_threshold:
                penalty = wrong_person_rate - self.wrong_person_threshold
                success_score = round(max(success_score - penalty, 0.0), 4)
                logger.info(
                    "Wrong-person penalty applied to %s "
                    "(rate=%.2f > threshold=%.2f): success_score reduced to %.4f",
                    sid, wrong_person_rate, self.wrong_person_threshold, success_score,
                )

            self._stats[sid] = SupervisorOutcomeStats(
                supervisor_id=sid,
                total_emails=total,
                admit_count=c["admit"],
                positive_count=c["positive"],
                wrong_person_count=c["wrong_person"],
                raw_score_sum=c["score_sum"],
                admit_rate=round(admit_rate, 4),
                positive_rate=round(positive_rate, 4),
                wrong_person_rate=round(wrong_person_rate, 4),
                success_score=success_score,
            )

        logger.info("Built stats for %d supervisors", len(self._stats))
        return self._stats

    # ------------------------------------------------------------------ #
    # Lookup                                                               #
    # ------------------------------------------------------------------ #

    def get_supervisor_score(self, supervisor_id: str) -> float | None:
        """
        Return the success_score for a supervisor, or None if no data exists.

        None signals to the scorer that no adjustment should be applied.

        Args:
            supervisor_id: OpenAlex author ID (short form, e.g. "A1234567").

        Returns:
            Float in [0, 1] or None.
        """
        stats = self._stats.get(supervisor_id)
        return stats.success_score if stats else None

    def get_supervisor_stats(self, supervisor_id: str) -> SupervisorOutcomeStats | None:
        """Return the full stats object for a supervisor, or None."""
        return self._stats.get(supervisor_id)

    # ------------------------------------------------------------------ #
    # Report                                                               #
    # ------------------------------------------------------------------ #

    def generate_report(self, top_n: int = 5) -> OutcomeLearningReport:
        """
        Generate a summary report of outcome learning.

        Args:
            top_n: Number of best/worst supervisors to include.

        Returns:
            Populated OutcomeLearningReport.
        """
        if not self._stats:
            return OutcomeLearningReport(
                total_supervisors=0,
                total_outcomes=len(self._records),
            )

        all_stats = list(self._stats.values())
        avg_success = round(
            sum(s.success_score for s in all_stats) / len(all_stats), 4
        )

        sorted_stats = sorted(all_stats, key=lambda s: s.success_score, reverse=True)

        return OutcomeLearningReport(
            total_supervisors=len(all_stats),
            total_outcomes=len(self._records),
            average_success_rate=avg_success,
            best_performing_supervisors=sorted_stats[:top_n],
            worst_performing_supervisors=sorted_stats[-top_n:][::-1],
        )

    def export_report(
        self, output_path: Path = Path("sample_output/outcome_learning_report.json")
    ) -> Path:
        """
        Write the outcome learning report to a JSON file.

        Args:
            output_path: Destination path.

        Returns:
            Path that was written.
        """
        report = self.generate_report()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.loads(report.model_dump_json())
        output_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("Outcome learning report written to %s", output_path)
        return output_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_csv_headers(fieldnames: list[str]) -> None:
    required = {"student_id", "supervisor_id", "outcome"}
    missing = required - set(fieldnames)
    if missing:
        raise ValueError(f"CSV is missing required columns: {missing}")
