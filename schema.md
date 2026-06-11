# Output Schema

The pipeline exports a single JSON file conforming to the `ShortlistOutput` Pydantic model
(`src/models/recommendation.py`).

---

## ShortlistOutput (top-level object)

| Field                        | Type               | Default   | Description                                          |
|------------------------------|--------------------|-----------|------------------------------------------------------|
| `student_name`               | `string`           | required  | Name from the input student profile                  |
| `student_id`                 | `string`           | `""`      | Slugified student name used as the output filename   |
| `total_candidates_evaluated` | `integer`          | required  | Total supervisors retrieved before any filtering     |
| `recommendation_count`       | `integer`          | `0`       | Number of recommendations in this file               |
| `pipeline_version`           | `string`           | `"1.0.0"` | Version string of the pipeline that generated this   |
| `generated_at`               | `string`           | required  | ISO 8601 UTC timestamp of generation                 |
| `recommendations`            | `Recommendation[]` | `[]`      | Ranked list of recommendations (target: 50–200)      |

---

## Recommendation

| Field                    | Type               | Default    | Description                                                      |
|--------------------------|--------------------|------------|------------------------------------------------------------------|
| `rank`                   | `integer ≥ 1`      | required   | 1-based rank (1 = best match)                                    |
| `supervisor`             | `Supervisor`       | required   | Full supervisor profile                                          |
| `score`                  | `float [0, 1]`     | required   | Composite match score (may be feedback-adjusted)                 |
| `score_breakdown`        | `ScoreBreakdown`   | required   | Per-dimension score detail                                       |
| `tier`                   | `string`           | `"target"` | `reach` \| `target` \| `safety`                                  |
| `why_match`              | `string`           | `""`       | LLM-generated personalised explanation (2–3 sentences)           |
| `program_url`            | `string \| null`   | `null`     | Legacy single-URL field (superseded by `linked_programs`)        |
| `linked_programs`        | `LinkedProgram[]`  | `[]`       | PhD program pages linked to the supervisor's institution         |
| `contact_email`          | `string \| null`   | `null`     | Extracted institutional contact email                            |
| `email_source`           | `string \| null`   | `null`     | `faculty_page` \| `profile_url` \| `not_found`                   |
| `historical_success_score` | `float [0,1] \| null` | `null` | Supervisor success rate from historical email outcomes          |
| `historical_email_count` | `integer \| null`  | `null`     | Total historical emails sent to this supervisor                  |

---

## ScoreBreakdown

All sub-scores are in `[0, 1]`. `overall_score` is the weighted composite.

| Field                  | Type           | Weight | Description                                                      |
|------------------------|----------------|--------|------------------------------------------------------------------|
| `overall_score`        | `float [0, 1]` | —      | Weighted composite of all five dimensions                        |
| `research_alignment`   | `float [0, 1]` | 40%    | Token overlap between student topics and supervisor concepts     |
| `publication_activity` | `float [0, 1]` | 20%    | Recency of latest publication combined with volume of recent output |
| `citation_impact`      | `float [0, 1]` | 15%    | Log-normalised total citations (saturation at 5 000)             |
| `evidence_quality`     | `float [0, 1]` | 15%    | Publication depth × concept diversity of the evidence record     |
| `country_preference`   | `float [0, 1]` | 10%    | Ranked match with student's preferred countries                  |

---

## LinkedProgram

| Field          | Type     | Default     | Description                                         |
|----------------|----------|-------------|-----------------------------------------------------|
| `program_name` | `string` | required    | Human-readable label derived from the URL path      |
| `institution`  | `string` | required    | Institution name (matches `Supervisor.institution`) |
| `url`          | `string` | required    | URL of the PhD program or graduate admissions page  |
| `status`       | `string` | `"unknown"` | `open` \| `unknown`                                 |

---

## Supervisor

### Core identity

| Field          | Type             | Default  | Description                              |
|----------------|------------------|----------|------------------------------------------|
| `name`         | `string`         | required | Full name                                |
| `institution`  | `string`         | required | University or research institute         |
| `department`   | `string \| null` | `null`   | Academic department                      |
| `country`      | `string`         | required | ISO 3166-1 alpha-2 country code          |
| `email`        | `string \| null` | `null`   | Contact email (written by EmailExtractor)|
| `profile_url`  | `string \| null` | `null`   | Faculty profile or ORCID page            |

### Research profile

| Field                 | Type            | Default | Description                                                   |
|-----------------------|-----------------|---------|---------------------------------------------------------------|
| `research_areas`      | `string[]`      | `[]`    | Keywords from OpenAlex x_concepts (sorted by score)          |
| `recent_publications` | `Publication[]` | `[]`    | Lightweight year-bucket summary from `counts_by_year`        |
| `grants`              | `Grant[]`       | `[]`    | Known research grants (currently always empty — no data source) |

### Funding / status

| Field                  | Type      | Default     | Description                                 |
|------------------------|-----------|-------------|---------------------------------------------|
| `is_accepting_students`| `boolean` | `true`      | Whether the supervisor is recruiting (always `true` — not yet populated) |
| `funding_status`       | `string`  | `"unknown"` | `active` \| `inactive` \| `unknown`         |

### Bibliometrics

| Field            | Type               | Default | Description                                        |
|------------------|--------------------|---------|----------------------------------------------------|
| `h_index`        | `integer \| null`  | `null`  | Hirsch index from OpenAlex `summary_stats`         |
| `works_count`    | `integer \| null`  | `null`  | Total works count from OpenAlex                    |
| `cited_by_count` | `integer \| null`  | `null`  | Total citations from OpenAlex                      |

### External IDs

| Field                 | Type             | Default | Description                        |
|-----------------------|------------------|---------|------------------------------------|
| `openalex_id`         | `string \| null` | `null`  | Short OpenAlex author ID (e.g. `A5046006076`) |
| `semantic_scholar_id` | `string \| null` | `null`  | Semantic Scholar author identifier |

### PI validation

| Field         | Type                | Default | Description                                       |
|---------------|---------------------|---------|---------------------------------------------------|
| `job_title`   | `string \| null`    | `null`  | Job title if provided by the source               |
| `pi_metadata` | `PIMetadata \| null`| `null`  | Full PI validation result (see PIMetadata)        |

### Evidence-collection fields (populated by EvidenceCollector)

| Field                     | Type            | Default | Description                                               |
|---------------------------|-----------------|---------|-----------------------------------------------------------|
| `publications`            | `Publication[]` | `[]`    | Top-N richest publications fetched by EvidenceCollector   |
| `total_citations`         | `integer`       | `0`     | Sum of `citation_count` across all fetched publications   |
| `recent_publication_count`| `integer`       | `0`     | Publications within the recency window (`RECENCY_YEARS`)  |
| `latest_publication_year` | `integer \| null`| `null` | Year of the most recent publication found                 |
| `research_concepts`       | `string[]`      | `[]`    | Deduplicated concept labels extracted from publications   |
| `evidence_collected`      | `boolean`       | `false` | `true` once EvidenceCollector has run successfully        |

### Per-gate validation results

| Field                | Type                      | Default | Description                                 |
|----------------------|---------------------------|---------|---------------------------------------------|
| `country_validation` | `ValidationResult \| null`| `null`  | Attached by CountryValidator                |
| `domain_validation`  | `ValidationResult \| null`| `null`  | Attached by DomainValidator                 |
| `evidence_validation`| `ValidationResult \| null`| `null`  | Attached by EvidenceValidator               |

---

## PIMetadata

| Field                 | Type             | Default | Description                                                     |
|-----------------------|------------------|---------|-----------------------------------------------------------------|
| `pi_verified`         | `boolean`        | `false` | Whether this supervisor was accepted as a PI                    |
| `verification_source` | `string`         | `""`    | Source used for verification (e.g. `"openalex"`, URL)           |
| `verification_method` | `string`         | `""`    | `title_hint` \| `faculty_page` \| `openalex_fallback` \| `none` |
| `job_title`           | `string`         | `""`    | Job title found during resolution                               |
| `confidence`          | `float [0, 1]`   | `0.0`   | Resolver confidence score                                       |
| `rejection_reason`    | `string \| null` | `null`  | Reason for rejection (set when `pi_verified=false`)             |

---

## ValidationResult

| Field             | Type       | Default  | Description                                     |
|-------------------|------------|----------|-------------------------------------------------|
| `passed`          | `boolean`  | required | Whether the supervisor passed this gate         |
| `score`           | `float`    | `0.0`    | Gate-specific score (meaning depends on gate)   |
| `source`          | `string`   | `""`     | Which logic path produced this result           |
| `reason`          | `string \| null` | `null` | Rejection reason (set when `passed=false`)   |
| `matched_concepts`| `string[]` | `[]`     | Concepts that contributed to a domain match     |

---

## Publication

| Field          | Type             | Default | Description                                            |
|----------------|------------------|---------|--------------------------------------------------------|
| `title`        | `string`         | required| Paper title                                            |
| `year`         | `integer`        | required| Publication year                                       |
| `venue`        | `string \| null` | `null`  | Conference or journal name                             |
| `citation_count`| `integer`       | `0`     | Total citations                                        |
| `url`          | `string \| null` | `null`  | Legacy URL field (use `doi_url` or `openalex_url`)     |
| `doi_url`      | `string \| null` | `null`  | Full DOI URL (e.g. `https://doi.org/10.xxxx/...`)      |
| `openalex_url` | `string \| null` | `null`  | OpenAlex work URL (e.g. `https://openalex.org/Wxxxx`) |
| `concepts`     | `string[]`       | `[]`    | Concept labels attached to this work by OpenAlex       |

---

## Grant

| Field    | Type             | Default  | Description        |
|----------|------------------|----------|--------------------|
| `title`  | `string`         | required | Grant title        |
| `funder` | `string`         | required | Funding agency     |
| `year`   | `integer`        | required | Award year         |
| `amount` | `float \| null`  | `null`   | Amount in USD      |

---

## Audit Fixes

The following corrections were made relative to the previous version of this document.
All fixes align documentation with the current Pydantic models — **no code was changed**.

### FIX-1 — ScoreBreakdown: three fields renamed, weights corrected, `overall_score` added

| Old field (incorrect) | New field (correct)    | Old weight | New weight | Notes |
|-----------------------|------------------------|------------|------------|-------|
| `publication_recency` | `publication_activity` | 20%        | 20%        | Renamed; combines recency + volume |
| `funding_activity`    | `citation_impact`      | 20%        | 15%        | Renamed and weight corrected; log-normalised citations |
| `domain_match`        | `evidence_quality`     | 10%        | 15%        | Renamed and weight corrected; publication depth × concept diversity |
| *(missing)*           | `overall_score`        | —          | —          | Added; was present in model but absent from schema |

### FIX-2 — ShortlistOutput: three fields added

Fields present in `ShortlistOutput` but missing from the schema:

| Added field            | Type      | Default   | Reason omitted previously |
|------------------------|-----------|-----------|---------------------------|
| `student_id`           | `string`  | `""`      | Oversight                 |
| `recommendation_count` | `integer` | `0`       | Oversight                 |
| `pipeline_version`     | `string`  | `"1.0.0"` | Oversight                 |

### FIX-3 — Recommendation: six fields added

Fields present in `Recommendation` but missing from the schema:

| Added field               | Type                  | Notes                              |
|---------------------------|-----------------------|------------------------------------|
| `tier`                    | `string`              | `reach \| target \| safety`       |
| `linked_programs`         | `LinkedProgram[]`     | Stage 8 enrichment output         |
| `contact_email`           | `string \| null`      | Stage 8 enrichment output         |
| `email_source`            | `string \| null`      | Source of extracted email         |
| `historical_success_score`| `float [0,1] \| null` | Feedback loop output              |
| `historical_email_count`  | `integer \| null`     | Feedback loop output              |

### FIX-4 — LinkedProgram: new object added

`LinkedProgram` exists in `recommendation.py` but had no schema entry. Added with all four fields: `program_name`, `institution`, `url`, `status`.

### FIX-5 — Supervisor: thirteen fields added

Fields present in `Supervisor` but missing from the schema:

| Added field                | Section                   |
|----------------------------|---------------------------|
| `works_count`              | Bibliometrics             |
| `cited_by_count`           | Bibliometrics             |
| `job_title`                | PI validation             |
| `pi_metadata`              | PI validation             |
| `publications`             | Evidence-collection       |
| `total_citations`          | Evidence-collection       |
| `recent_publication_count` | Evidence-collection       |
| `latest_publication_year`  | Evidence-collection       |
| `research_concepts`        | Evidence-collection       |
| `evidence_collected`       | Evidence-collection       |
| `country_validation`       | Per-gate validation       |
| `domain_validation`        | Per-gate validation       |
| `evidence_validation`      | Per-gate validation       |

### FIX-6 — Publication: three fields added

| Added field    | Notes                                                  |
|----------------|--------------------------------------------------------|
| `doi_url`      | Full DOI URL populated by EvidenceCollector            |
| `openalex_url` | OpenAlex work URL populated by EvidenceCollector       |
| `concepts`     | Concept label list populated by EvidenceCollector      |

### FIX-7 — PIMetadata: new object added

`PIMetadata` is referenced by `Supervisor.pi_metadata` but had no schema entry. Added with all six fields.

### FIX-8 — ValidationResult: new object added

`ValidationResult` is referenced by three `Supervisor` fields but had no schema entry. Added with all five fields.

### FIX-9 — Supervisor notes corrected

- `grants` description updated: noted as always empty (no grant data source currently active).
- `is_accepting_students` description updated: noted as always `true` (not yet populated by any pipeline stage).
- `recent_publications` description clarified: lightweight year-bucket summary from `counts_by_year`, not EvidenceCollector output (`publications` is the enriched list).
