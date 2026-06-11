# Design Decisions & Tradeoffs

This document records key architectural choices, the alternatives considered, and the rationale for each decision.

---

## 1. Pydantic v2 for Data Schemas

**Decision:** Use Pydantic v2 models for all data objects (StudentProfile, Supervisor, Recommendation).

**Rationale:**
- Runtime validation with clear error messages at system boundaries.
- `model_dump_json()` for zero-friction JSON serialisation.
- Schema generation (JSON Schema) for free.

**Tradeoff:** Adds a dependency and a small runtime cost vs plain dataclasses.

---

## 2. Multiple Retrieval Sources (OpenAlex + Semantic Scholar + Faculty Scraper)

**Decision:** Retrieve candidates from three independent sources and merge.

**Rationale:**
- No single source has complete coverage; union maximises recall.
- Each source exposes different metadata (OpenAlex: institution/country; S2: h-index; scraper: accepting-students flag).

**Tradeoff:** Higher latency and deduplication complexity.

---

## 3. Validator Chain (not a single filter function)

**Decision:** Four independent validator classes (PI, Country, Evidence, Domain) chained in the pipeline.

**Rationale:**
- Each validator has a single responsibility (SRP).
- Easy to disable or reorder individual validators without touching others (OCP).
- Each validator is independently unit-testable.

**Tradeoff:** Slightly more boilerplate than a single multi-condition `if` statement.

---

## 4. Configurable Scoring Weights

**Decision:** Scoring weights are injectable at construction time with a sensible default.

**Rationale:**
- Different student profiles may warrant different weighting (e.g., funding-constrained students weight funding_activity higher).
- Weights can be tuned without code changes.

**Tradeoff:** Weights must sum to 1.0; validation is the caller's responsibility.

---

## 5. LLM for Why-Match Generation (not template strings)

**Decision:** Use an LLM to generate personalised explanations rather than fill-in-the-blank templates.

**Rationale:**
- Templates produce repetitive, impersonal text that students notice immediately.
- LLMs can synthesise specific paper titles, grant names, and student interests into coherent prose.

**Tradeoff:** API cost, latency, and non-determinism. Mitigation: generate in batch and cache by (supervisor_id, student_id).

---

## 6. Pipeline Orchestrator with Injected Dependencies

**Decision:** `ShortlistPipeline` accepts all collaborators via constructor injection.

**Rationale:**
- Enables mocking any stage in unit tests without monkey-patching.
- Follows Dependency Inversion Principle — the pipeline depends on abstractions (BaseRetriever, BaseValidator), not concrete classes.

**Tradeoff:** Slightly more verbose instantiation in main.py.

---

## 7. pydantic-settings for Configuration

**Decision:** Use `pydantic-settings` to load env vars into a typed `Settings` object.

**Rationale:**
- All configuration is validated at startup — fail fast on misconfiguration.
- Single source of truth; no scattered `os.getenv()` calls.
- `@lru_cache` ensures Settings is a singleton.

**Tradeoff:** Adds `pydantic-settings` dependency on top of `pydantic`.

---

_Add new decisions here as the implementation progresses._

---

## 9. Outcome-Driven Ranking Adjustment

**Problem:**
Content-based scoring (research alignment, citations, recency) cannot distinguish between a supervisor who consistently responds positively and one who never replies or is systematically misidentified. A supervisor who has produced admissions for similar students should rank higher than one with identical bibliometric scores but poor contact history.

**Solution:**
`OutcomeLearner` reads a CSV of historical `student_id, supervisor_id, outcome` rows and computes a `success_score \u2208 [0, 1]` per supervisor. `RecommendationScorer` blends it in:
```
adjusted = (1 - feedback_weight) × base_score
          +      feedback_weight  × success_score
```
Default `feedback_weight = 0.15` (configurable). If no history exists for a supervisor, `adjusted = base_score` exactly — no regression.

**Safety — WRONG_PERSON penalty:**
A supervisor with `wrong_person_rate > 0.20` has unreliable contact data (emails reached the wrong person >20% of the time). Their `success_score` is reduced by `wrong_person_rate - threshold` to reflect this. Without the penalty, a supervisor who occasionally admits students but is frequently mis-contacted would receive an inflated score.

**Tradeoff — Historical bias:**
Supervisors with large contact histories dominate outcome scores. New supervisors with no history are treated neutrally (no adjustment). This is intentional: the system should not penalise supervisors simply for being new to the dataset. As contact data accumulates, the signal becomes more reliable.

**Feature is opt-in:**
Pipeline behaviour is identical when no outcomes file is loaded. `ShortlistPipeline.load_outcomes(path)` must be called explicitly to activate the feedback loop.

---

## 8. Embedding-Based Domain Validation

**Problem:**
Keyword token overlap causes wrong-domain contamination. A supervisor
studying "DNA barcoding" in plant biology shares tokens like "sequence"
and "analysis" with a student interested in genomic sequencing in
computational biology. Token overlap says they match; semantics say they do not.

**Solution:**
Added `EmbeddingDomainValidator` using `sentence-transformers/all-MiniLM-L6-v2`
to compute cosine similarity between dense text blobs:

- Student blob: extracted_topics + preferred_domains + SOP snippet
- Supervisor blob: research_concepts + research_areas + top-5 publication titles

New hybrid scoring formula (when enabled):
```
final = 0.30 * keyword_score + 0.30 * concept_score + 0.40 * embedding_score
```

**Integration:**
`DomainValidator` gains a `mode` parameter (`keyword_only` | `embedding_only` | `hybrid`).
The existing `validate(supervisor) -> bool` signature is preserved exactly.
If the embedding service cannot load, the system logs a warning and
automatically degrades to `keyword_only` — no pipeline disruption.

**Controlled by config:**
```
EMBEDDING_DOMAIN_ENABLED=true          # default: false
EMBEDDING_DOMAIN_THRESHOLD=0.55
EMBEDDING_MODEL_NAME=sentence-transformers/all-MiniLM-L6-v2
```

**Tradeoff:**
- First inference: ~200 ms model load + ~5 ms per supervisor pair.
- Subsequent calls: ~5 ms (model already loaded, embeddings cached by hash).
- Adds `sentence-transformers` (~500 MB) as an optional dependency.
- Significantly lower contamination on cross-domain surface-form collisions.
- Embeddings are cached in-process by `sha256(text)`, so the same
  supervisor queried twice in one run pays the encode cost only once.
