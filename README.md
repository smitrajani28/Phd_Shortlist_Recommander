# PhD Shortlist Builder

An AI-powered system that takes a student profile JSON as input and produces a ranked shortlist of 50–200 PhD supervisors and programs.

## Pipeline

```
Student Profile JSON
        ↓  Stage 1 — Interest Extraction   (StudentParser)
        ↓  Stage 2 — Professor Retrieval   (OpenAlex API)
        ↓  Stage 3 — PI Validation         (PIValidator + FacultyProfileResolver)
        ↓  Stage 4 — Evidence Collection   (EvidenceCollector → OpenAlex /works)
        ↓  Stage 5 — Validation Layer      (Country + Domain + Evidence validators)
        ↓  Stage 6 — Scoring               (RecommendationScorer — 5 dimensions)
        ↓  Stage 7 — Why Match Generation  (LLM or deterministic fallback)
        ↓  Stage 8 — JSON Export           (JsonExporter)
Shortlist Output JSON
```

## Setup

```bash
# 1. Create virtual environment
python -m venv .venv && source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env and fill in your API keys
```

## Usage

### Retrieval slice (stages 1–7, prints to stdout)
```bash
python -m src.main sample_input/student.json
```

### Full pipeline — all 8 stages, writes shortlist JSON
```bash
python -m src.main sample_input/student.json --full
```

### Full pipeline with analytics report
```bash
python -m src.main sample_input/student.json --report
```
Writes:
- `sample_output/<student_name>.json` — ranked shortlist
- `sample_output/<student_name>_report.json` — pipeline analytics
- `sample_output/<student_name>_report.md` — human-readable summary

### Explicit output path
```bash
python -m src.main sample_input/student.json --full --output sample_output/alice.json
```

## Report Example

```
# Pipeline Report — Jane Doe

## Validation Funnel

| Stage              | Count | Rejected |
|--------------------|------:|---------:|
| Retrieved          |   142 |          |
| PI Validated       |    58 |       84 |
| Country Validated  |    41 |       17 |
| Domain Validated   |    27 |       14 |
| Evidence Validated |    19 |        8 |
| **Recommendations**|  **19**|         |

**Overall pass rate:** 13.4%
**PI rejection rate:** 59.2%

## Runtime

| Stage      | Seconds |
|------------|--------:|
| Retrieval  |  120.50 |
| Validation |   80.20 |
| Scoring    |    5.10 |
| Why Match  |  200.30 |
| Export     |    1.00 |
| **Total**  | **407.10** |
```

## Project Structure

```
src/
├── models/         # Pydantic data schemas
├── parsers/        # Raw JSON → StudentProfile
├── retrievers/     # OpenAlex retriever
├── validators/     # PI, Country, Domain, Evidence + embedding validator
├── scorers/        # Composite match scoring (5 dimensions)
├── generators/     # LLM why_match generation + fallback
├── exporters/      # JSON file export
├── evaluation/     # PipelineReport models + ReportExporter
├── services/       # OpenAlexClient, EvidenceCollector, EmbeddingService
├── pipelines/      # Central orchestrator (ShortlistPipeline)
└── utils/          # Logger, config, constants

tests/              # Pytest unit tests (307 passing)
sample_input/       # Example student profile JSON
sample_output/      # Generated shortlist + report files
```

## Running Tests

```bash
pytest tests/ -v
```

## Closing the Feedback Loop

The system learns from historical email outcomes to improve future recommendations.

When students (or programs) record the outcome of each supervisor contact in a CSV:

```
student_id,supervisor_id,institution,area,sent_at,outcome
106419,A5031856973,UNSW,PTSD,2026-07-12,ADMIT
106420,A2208157607,MIT,NLP,2026-06-10,POSITIVE_REPLY
```

Those outcomes are converted into per-supervisor **success scores** and incorporated into future ranking:

```
adjusted_score = (1 - feedback_weight) × base_score
               +      feedback_weight  × success_score
```

Default `feedback_weight = 0.15` — historical signal has 15% influence; content-based scoring retains 85%.

Supported outcomes and weights:

| Outcome | Weight |
|---|---:|
| ADMIT | +5 |
| INTERVIEW | +3 |
| POSITIVE_REPLY | +2 |
| OUT_OF_OFFICE | 0 |
| REJECT | -2 |
| NO_REPLY | -1 |
| BOUNCE | -3 |
| NOT_RECRUITING | -5 |
| WRONG_PERSON | -10 |

A supervisor with `wrong_person_rate > 0.20` receives an additional score penalty to reflect unreliable contact data.

**The feature is fully optional** — if no outcomes file is provided, the pipeline behaves exactly as before.

### Usage

```bash
# Standalone outcome learning report
python -m src.main sample_input/outcomes.csv --learn

# Output:
# { "total_supervisors": 6, "total_outcomes": 17, "average_success_rate": 0.54 }
```

Writes: `sample_output/outcome_learning_report.json`

## Output Schema

See [schema.md](schema.md) for the full output JSON schema specification.

## Design Decisions

See [DECISIONS.md](DECISIONS.md) for architecture tradeoffs and rationale.
