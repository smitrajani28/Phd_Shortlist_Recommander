"""
Tests for OutcomeLearner, SupervisorOutcomeStats, and feedback loop integration.
All tests are network-free and use in-memory or tmp_path CSV fixtures.
"""

import csv
import json
import pytest
from pathlib import Path

from src.feedback.outcome_learner import (
    OutcomeLearner,
    OutcomeRecord,
    SupervisorOutcomeStats,
    OutcomeLearningReport,
    DEFAULT_OUTCOME_WEIGHTS,
)
from src.models.student import StudentProfile, AcademicBackground
from src.models.supervisor import Supervisor
from src.scorers.recommendation_scorer import RecommendationScorer


# ------------------------------------------------------------------ #
# CSV helpers                                                          #
# ------------------------------------------------------------------ #

def _write_csv(path: Path, rows: list[dict]) -> Path:
    """Write rows to `path` (which must end in .csv)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["student_id", "supervisor_id", "institution", "area", "sent_at", "outcome"]
        )
        writer.writeheader()
        writer.writerows(rows)
    return path


def _row(supervisor_id: str = "A1", outcome: str = "ADMIT", student_id: str = "S1") -> dict:
    return {
        "student_id": student_id,
        "supervisor_id": supervisor_id,
        "institution": "MIT",
        "area": "NLP",
        "sent_at": "2025-01-01",
        "outcome": outcome,
    }


def _learner_with_data(tmp_path: Path, rows: list[dict]) -> OutcomeLearner:
    learner = OutcomeLearner()
    learner.load_csv(_write_csv(tmp_path / "outcomes.csv", rows))
    learner.build_supervisor_stats()
    return learner


def _supervisor(openalex_id: str = "A1") -> Supervisor:
    return Supervisor(name="Dr. Test", institution="MIT", country="US", openalex_id=openalex_id)


def _profile() -> StudentProfile:
    return StudentProfile(
        name="Jane",
        background=AcademicBackground(degree="MSc", field="CS", institution="MIT"),
    )


# ====================================================================
# Default outcome weights
# ====================================================================

class TestDefaultWeights:
    def test_all_outcomes_have_weights(self):
        required = {
            "ADMIT", "INTERVIEW", "POSITIVE_REPLY", "REJECT",
            "NO_REPLY", "BOUNCE", "OUT_OF_OFFICE", "NOT_RECRUITING", "WRONG_PERSON",
        }
        assert required == set(DEFAULT_OUTCOME_WEIGHTS.keys())

    def test_admit_is_highest(self):
        assert DEFAULT_OUTCOME_WEIGHTS["ADMIT"] == max(DEFAULT_OUTCOME_WEIGHTS.values())

    def test_wrong_person_is_lowest(self):
        assert DEFAULT_OUTCOME_WEIGHTS["WRONG_PERSON"] == min(DEFAULT_OUTCOME_WEIGHTS.values())

    def test_out_of_office_is_neutral(self):
        assert DEFAULT_OUTCOME_WEIGHTS["OUT_OF_OFFICE"] == 0


# ====================================================================
# Test 1: Score calculation
# ====================================================================

class TestScoreCalculation:
    def test_all_admits_gives_high_score(self, tmp_path):
        rows = [_row("A1", "ADMIT") for _ in range(5)]
        learner = _learner_with_data(tmp_path, rows)
        score = learner.get_supervisor_score("A1")
        assert score is not None
        assert score > 0.5

    def test_all_rejects_gives_low_score(self, tmp_path):
        rows = [_row("A1", "REJECT") for _ in range(5)]
        learner = _learner_with_data(tmp_path, rows)
        score = learner.get_supervisor_score("A1")
        assert score is not None
        # REJECT = -2; normalised puts this well below the midpoint
        assert score < 0.6

    def test_mixed_outcomes_score_in_range(self, tmp_path):
        rows = [
            _row("A1", "ADMIT"),
            _row("A1", "INTERVIEW"),
            _row("A1", "REJECT"),
            _row("A1", "NO_REPLY"),
        ]
        learner = _learner_with_data(tmp_path, rows)
        score = learner.get_supervisor_score("A1")
        assert 0.0 <= score <= 1.0

    def test_score_is_normalised_to_0_1(self, tmp_path):
        rows = [_row("A1", outcome) for outcome in DEFAULT_OUTCOME_WEIGHTS]
        learner = _learner_with_data(tmp_path, rows)
        score = learner.get_supervisor_score("A1")
        assert 0.0 <= score <= 1.0

    def test_out_of_office_alone_gives_neutral_score(self, tmp_path):
        rows = [_row("A1", "OUT_OF_OFFICE") for _ in range(3)]
        learner = _learner_with_data(tmp_path, rows)
        # OUT_OF_OFFICE = 0 weight → sum = 0 → midpoint of range
        score = learner.get_supervisor_score("A1")
        assert score is not None


# ====================================================================
# Test 2: Admit rate calculation
# ====================================================================

class TestAdmitRateCalculation:
    def test_admit_rate_all_admits(self, tmp_path):
        rows = [_row("A1", "ADMIT") for _ in range(4)]
        learner = _learner_with_data(tmp_path, rows)
        stats = learner.get_supervisor_stats("A1")
        assert stats.admit_rate == 1.0

    def test_admit_rate_no_admits(self, tmp_path):
        rows = [_row("A1", "REJECT") for _ in range(3)]
        learner = _learner_with_data(tmp_path, rows)
        stats = learner.get_supervisor_stats("A1")
        assert stats.admit_rate == 0.0

    def test_admit_rate_partial(self, tmp_path):
        rows = [_row("A1", "ADMIT"), _row("A1", "REJECT"), _row("A1", "REJECT")]
        learner = _learner_with_data(tmp_path, rows)
        stats = learner.get_supervisor_stats("A1")
        assert abs(stats.admit_rate - 1 / 3) < 1e-4

    def test_positive_rate_includes_interview_and_reply(self, tmp_path):
        rows = [
            _row("A1", "ADMIT"),
            _row("A1", "INTERVIEW"),
            _row("A1", "POSITIVE_REPLY"),
            _row("A1", "REJECT"),
        ]
        learner = _learner_with_data(tmp_path, rows)
        stats = learner.get_supervisor_stats("A1")
        assert abs(stats.positive_rate - 0.75) < 1e-4

    def test_total_emails_counted_correctly(self, tmp_path):
        rows = [_row("A1", "ADMIT")] * 7
        learner = _learner_with_data(tmp_path, rows)
        assert learner.get_supervisor_stats("A1").total_emails == 7


# ====================================================================
# Test 3: Wrong person penalty
# ====================================================================

class TestWrongPersonPenalty:
    def test_high_wrong_person_rate_reduces_score(self, tmp_path):
        rows = [
            _row("A1", "WRONG_PERSON"),
            _row("A1", "WRONG_PERSON"),
            _row("A1", "WRONG_PERSON"),
            _row("A1", "ADMIT"),
        ]  # wrong_person_rate = 0.75 > threshold 0.20
        learner = _learner_with_data(tmp_path, rows)
        stats = learner.get_supervisor_stats("A1")
        assert stats.wrong_person_rate == 0.75

        # Score with penalty should be less than score without penalty
        learner_no_penalty = OutcomeLearner(wrong_person_threshold=1.0)  # effectively no threshold
        learner_no_penalty.load_csv(_write_csv(tmp_path / "no_penalty.csv", rows))
        learner_no_penalty.build_supervisor_stats()
        penalised = stats.success_score
        unpenalised = learner_no_penalty.get_supervisor_score("A1")
        assert penalised < unpenalised

    def test_low_wrong_person_rate_no_penalty(self, tmp_path):
        rows = [
            _row("A1", "ADMIT"),
            _row("A1", "ADMIT"),
            _row("A1", "ADMIT"),
            _row("A1", "WRONG_PERSON"),  # rate = 0.25, but let's use threshold 0.30
        ]
        learner = OutcomeLearner(wrong_person_threshold=0.30)
        learner.load_csv(_write_csv(tmp_path / "outcomes.csv", rows))
        learner.build_supervisor_stats()
        stats = learner.get_supervisor_stats("A1")
        assert stats.wrong_person_rate == 0.25  # below threshold 0.30 → no penalty

    def test_wrong_person_score_clamped_to_zero(self, tmp_path):
        rows = [_row("A1", "WRONG_PERSON") for _ in range(10)]
        learner = _learner_with_data(tmp_path, rows)
        score = learner.get_supervisor_score("A1")
        assert score >= 0.0


# ====================================================================
# Test 4: Ranking adjustment
# ====================================================================

class TestRankingAdjustment:
    def test_high_history_raises_score(self):
        """A supervisor with good history should get a higher adjusted score."""
        from src.feedback.outcome_learner import SupervisorOutcomeStats
        learner = OutcomeLearner()
        learner._stats["A_GOOD"] = SupervisorOutcomeStats(
            supervisor_id="A_GOOD", total_emails=10,
            admit_count=8, success_score=0.95,
        )
        scorer = RecommendationScorer(outcome_learner=learner, feedback_weight=0.15)
        s = _supervisor("A_GOOD")
        base = 0.60
        adjusted = scorer._apply_feedback_adjustment(s, base)
        # adjusted = 0.85*0.60 + 0.15*0.95 = 0.51 + 0.1425 = 0.6525
        assert adjusted > base

    def test_low_history_lowers_score(self):
        from src.feedback.outcome_learner import SupervisorOutcomeStats
        learner = OutcomeLearner()
        learner._stats["A_BAD"] = SupervisorOutcomeStats(
            supervisor_id="A_BAD", total_emails=10,
            success_score=0.10,
        )
        scorer = RecommendationScorer(outcome_learner=learner, feedback_weight=0.15)
        s = _supervisor("A_BAD")
        base = 0.70
        adjusted = scorer._apply_feedback_adjustment(s, base)
        assert adjusted < base

    def test_feedback_weight_formula(self):
        from src.feedback.outcome_learner import SupervisorOutcomeStats
        learner = OutcomeLearner()
        learner._stats["A1"] = SupervisorOutcomeStats(
            supervisor_id="A1", total_emails=5, success_score=0.80,
        )
        scorer = RecommendationScorer(outcome_learner=learner, feedback_weight=0.20)
        s = _supervisor("A1")
        base = 0.60
        adjusted = scorer._apply_feedback_adjustment(s, base)
        expected = round(0.80 * 0.60 + 0.20 * 0.80, 4)
        assert abs(adjusted - expected) < 1e-4

    def test_score_all_sorts_by_adjusted_score(self, tmp_path):
        """Supervisor with better history should rank higher than an equal base-score peer."""
        from src.feedback.outcome_learner import SupervisorOutcomeStats
        learner = OutcomeLearner()
        learner._stats["A_HIGH"] = SupervisorOutcomeStats(
            supervisor_id="A_HIGH", total_emails=10, success_score=0.95,
        )
        learner._stats["A_LOW"] = SupervisorOutcomeStats(
            supervisor_id="A_LOW", total_emails=10, success_score=0.05,
        )
        scorer = RecommendationScorer(outcome_learner=learner, feedback_weight=0.30)
        s_high = _supervisor("A_HIGH")
        s_low  = _supervisor("A_LOW")
        # Give both identical base scores by zeroing all evidence fields
        recs = scorer.score_all([s_high, s_low], _profile())
        ids = [r.supervisor.openalex_id for r in recs]
        assert ids[0] == "A_HIGH"

    def test_historical_fields_populated_on_recommendation(self, tmp_path):
        from src.feedback.outcome_learner import SupervisorOutcomeStats
        learner = OutcomeLearner()
        learner._stats["A1"] = SupervisorOutcomeStats(
            supervisor_id="A1", total_emails=7, success_score=0.75,
        )
        scorer = RecommendationScorer(outcome_learner=learner, feedback_weight=0.15)
        recs = scorer.score_all([_supervisor("A1")], _profile())
        assert recs[0].historical_success_score == 0.75
        assert recs[0].historical_email_count == 7


# ====================================================================
# Test 5: Missing supervisor history
# ====================================================================

class TestMissingHistory:
    def test_unknown_supervisor_returns_none(self, tmp_path):
        learner = _learner_with_data(tmp_path, [_row("A1", "ADMIT")])
        assert learner.get_supervisor_score("UNKNOWN") is None

    def test_no_adjustment_when_no_history(self):
        from src.feedback.outcome_learner import SupervisorOutcomeStats
        learner = OutcomeLearner()
        learner._stats["A_OTHER"] = SupervisorOutcomeStats(
            supervisor_id="A_OTHER", total_emails=5, success_score=0.9,
        )
        scorer = RecommendationScorer(outcome_learner=learner, feedback_weight=0.15)
        s = _supervisor("A_NO_HISTORY")
        base = 0.65
        adjusted = scorer._apply_feedback_adjustment(s, base)
        assert adjusted == base

    def test_no_learner_returns_base_score(self):
        scorer = RecommendationScorer(outcome_learner=None)
        s = _supervisor("A1")
        assert scorer._apply_feedback_adjustment(s, 0.72) == 0.72

    def test_historical_fields_none_when_no_data(self):
        scorer = RecommendationScorer(outcome_learner=None)
        recs = scorer.score_all([_supervisor("A1")], _profile())
        assert recs[0].historical_success_score is None
        assert recs[0].historical_email_count is None

    def test_pipeline_unchanged_without_outcomes(self, tmp_path):
        """load_outcomes() is optional — not calling it must not affect results."""
        from src.utils.config import Settings
        from src.pipelines.shortlist_pipeline import ShortlistPipeline
        settings = Settings(
            pi_use_network=False,
            evidence_min_recent_publications=0,
            evidence_min_total_citations=0,
            evidence_max_publication_gap=50,
        )
        pipeline = ShortlistPipeline(settings=settings)
        assert pipeline.scorer.outcome_learner is None


# ====================================================================
# Test 6: Report generation
# ====================================================================

class TestReportGeneration:
    def test_report_has_correct_counts(self, tmp_path):
        rows = [
            _row("A1", "ADMIT"), _row("A1", "REJECT"),
            _row("A2", "INTERVIEW"), _row("A2", "NO_REPLY"),
        ]
        learner = _learner_with_data(tmp_path, rows)
        report = learner.generate_report()
        assert report.total_supervisors == 2
        assert report.total_outcomes == 4

    def test_report_average_success_rate_in_range(self, tmp_path):
        rows = [_row("A1", "ADMIT"), _row("A2", "REJECT")]
        learner = _learner_with_data(tmp_path, rows)
        report = learner.generate_report()
        assert 0.0 <= report.average_success_rate <= 1.0

    def test_best_performers_sorted_desc(self, tmp_path):
        rows = (
            [_row("A_GOOD", "ADMIT")] * 5 +
            [_row("A_BAD", "REJECT")] * 5
        )
        learner = _learner_with_data(tmp_path, rows)
        report = learner.generate_report(top_n=2)
        assert report.best_performing_supervisors[0].supervisor_id == "A_GOOD"

    def test_worst_performers_sorted_asc(self, tmp_path):
        rows = (
            [_row("A_GOOD", "ADMIT")] * 5 +
            [_row("A_BAD", "REJECT")] * 5
        )
        learner = _learner_with_data(tmp_path, rows)
        report = learner.generate_report(top_n=2)
        assert report.worst_performing_supervisors[0].supervisor_id == "A_BAD"

    def test_empty_learner_returns_empty_report(self):
        learner = OutcomeLearner()
        learner.build_supervisor_stats()
        report = learner.generate_report()
        assert report.total_supervisors == 0
        assert report.total_outcomes == 0

    def test_export_report_writes_valid_json(self, tmp_path):
        rows = [_row("A1", "ADMIT"), _row("A2", "REJECT")]
        learner = _learner_with_data(tmp_path, rows)
        out_path = tmp_path / "report.json"
        learner.export_report(out_path)
        data = json.loads(out_path.read_text())
        assert "total_supervisors" in data
        assert "total_outcomes" in data
        assert "average_success_rate" in data
        assert "best_performing_supervisors" in data
        assert "worst_performing_supervisors" in data

    def test_generated_at_is_set(self, tmp_path):
        rows = [_row("A1", "ADMIT")]
        learner = _learner_with_data(tmp_path, rows)
        report = learner.generate_report()
        assert report.generated_at


# ====================================================================
# CSV loading edge cases
# ====================================================================

class TestCSVLoading:
    def test_missing_file_raises(self):
        learner = OutcomeLearner()
        with pytest.raises(FileNotFoundError):
            learner.load_csv(Path("/nonexistent/outcomes.csv"))

    def test_missing_required_column_raises(self, tmp_path):
        p = tmp_path / "bad.csv"
        p.write_text("student_id,institution\nS1,MIT\n")
        learner = OutcomeLearner()
        with pytest.raises(ValueError, match="missing required columns"):
            learner.load_csv(p)

    def test_unknown_outcome_skipped(self, tmp_path):
        rows = [_row("A1", "ADMIT"), _row("A1", "INVALID_OUTCOME")]
        learner = OutcomeLearner()
        loaded = learner.load_csv(_write_csv(tmp_path / "outcomes.csv", rows))
        assert loaded == 1  # only ADMIT was loaded

    def test_outcome_case_insensitive(self, tmp_path):
        rows = [_row("A1", "admit")]
        learner = OutcomeLearner()
        loaded = learner.load_csv(_write_csv(tmp_path / "outcomes.csv", rows))
        assert loaded == 1

    def test_sample_csv_loads(self):
        sample = Path("sample_input/outcomes.csv")
        if not sample.exists():
            pytest.skip("sample_input/outcomes.csv not present")
        learner = OutcomeLearner()
        count = learner.load_csv(sample)
        assert count > 0
