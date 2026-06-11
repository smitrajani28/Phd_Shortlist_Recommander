# Output Schema

The pipeline exports a single JSON file conforming to the `ShortlistOutput` Pydantic model.

## Top-Level Object

| Field                       | Type            | Description                                        |
|-----------------------------|-----------------|----------------------------------------------------|
| `student_name`              | `string`        | Name from the input student profile                |
| `total_candidates_evaluated`| `integer`       | Total supervisors retrieved before filtering        |
| `generated_at`              | `string`        | ISO 8601 UTC timestamp of generation               |
| `recommendations`           | `Recommendation[]` | Ranked list of 50–200 recommendations           |

## Recommendation Object

| Field            | Type             | Description                                         |
|------------------|------------------|-----------------------------------------------------|
| `rank`           | `integer`        | 1-based rank (1 = best match)                       |
| `score`          | `float [0,1]`    | Composite match score                               |
| `score_breakdown`| `ScoreBreakdown` | Per-component score detail                          |
| `why_match`      | `string`         | LLM-generated personalised explanation (2–3 sentences) |
| `program_url`    | `string \| null` | Direct link to PhD program or supervisor's lab page |
| `supervisor`     | `Supervisor`     | Full supervisor profile                             |

## ScoreBreakdown Object

| Field                  | Type          | Weight | Description                                   |
|------------------------|---------------|--------|-----------------------------------------------|
| `research_alignment`   | `float [0,1]` | 40%    | Semantic similarity of interests vs research areas |
| `publication_recency`  | `float [0,1]` | 20%    | Recency of most recent publications           |
| `funding_activity`     | `float [0,1]` | 20%    | Presence of active grants                     |
| `country_preference`   | `float [0,1]` | 10%    | Match with student's preferred countries      |
| `domain_match`         | `float [0,1]` | 10%    | Overlap with student's preferred domains      |

## Supervisor Object

| Field                   | Type              | Description                          |
|-------------------------|-------------------|--------------------------------------|
| `name`                  | `string`          | Full name                            |
| `institution`           | `string`          | University or research institute     |
| `department`            | `string \| null`  | Academic department                  |
| `country`               | `string`          | ISO 3166-1 alpha-2 country code      |
| `email`                 | `string \| null`  | Contact email                        |
| `profile_url`           | `string \| null`  | Faculty profile or personal page     |
| `research_areas`        | `string[]`        | Keywords describing research focus   |
| `h_index`               | `integer \| null` | Hirsch index from academic database  |
| `funding_status`        | `enum`            | `active \| inactive \| unknown`      |
| `is_accepting_students` | `boolean`         | Whether the supervisor takes students|
| `openalex_id`           | `string \| null`  | OpenAlex author identifier           |
| `semantic_scholar_id`   | `string \| null`  | Semantic Scholar author identifier   |
| `recent_publications`   | `Publication[]`   | Publications within recency window   |
| `grants`                | `Grant[]`         | Known research grants                |

## Publication Object

| Field           | Type             | Description              |
|-----------------|------------------|--------------------------|
| `title`         | `string`         | Paper title              |
| `year`          | `integer`        | Publication year         |
| `venue`         | `string \| null` | Conference or journal    |
| `citation_count`| `integer`        | Total citations          |
| `url`           | `string \| null` | Link to paper            |

## Grant Object

| Field    | Type             | Description         |
|----------|------------------|---------------------|
| `title`  | `string`         | Grant title         |
| `funder` | `string`         | Funding agency      |
| `year`   | `integer`        | Award year          |
| `amount` | `float \| null`  | Amount in USD       |
