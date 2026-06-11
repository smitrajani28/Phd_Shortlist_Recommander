"""
Central pipeline orchestrator for the PhD Shortlist Builder.

Implemented stages:
    ↓  Stage 1 — Interest Extraction   (StudentParser)
    ↓  Stage 2 — Professor Retrieval   (OpenAlexRetriever)
    ↓  Stage 3 — PI Validation         (PIValidator + FacultyProfileResolver)
    ↓  Stage 4 — Evidence Collection   (EvidenceCollector)
    ↓  Stage 5 — Validation Layer      (Country + Domain + Evidence validators)
    ↓  Stage 6 — Scoring               (RecommendationScorer)
    ↓  Stage 7 — Why Match Generation  (WhyMatchGenerator)
    ↓  Stage 8 — JSON Export           (JsonExporter)
"""

from dataclasses import dataclass, field
from pathlib import Path
import time

from ..models.student import StudentProfile
from ..models.supervisor import Supervisor
from ..models.recommendation import Recommendation, ShortlistOutput, ScoreBreakdown
from ..parsers.student_parser import StudentParser
from ..retrievers.openalex_retriever import OpenAlexRetriever
from ..retrievers.semantic_scholar_retriever import SemanticScholarRetriever
from ..retrievers.faculty_scraper import FacultyScraper
from ..validators.pi_validator import PIValidator
from ..validators.country_validator import CountryValidator
from ..validators.domain_validator import DomainValidator
from ..validators.evidence_validator import EvidenceValidator
from ..validators.faculty_profile_resolver import FacultyProfileResolver
from ..services.openalex_client import OpenAlexClient
from ..services.evidence_collector import EvidenceCollector
from ..scorers.recommendation_scorer import RecommendationScorer
from ..generators.why_match_generator import WhyMatchGenerator
from ..exporters.json_exporter import JsonExporter
from ..linkers.program_linker import ProgramLinker, LinkedProgram as LinkerLinkedProgram
from ..extractors.email_extractor import EmailExtractor
from ..models.recommendation import LinkedProgram
from ..evaluation.pipeline_report import (
    PipelineReport, ValidationStageMetrics, RuntimeMetrics, ReportExporter
)
from ..feedback.outcome_learner import OutcomeLearner
from ..utils.logger import get_logger
from ..utils.config import Settings

logger = get_logger(__name__)


@dataclass
class ValidationCounts:
    """Per-gate pass/fail tallies produced by _validate()."""
    retrieved: int = 0
    pi_passed: int = 0
    country_passed: int = 0
    domain_passed: int = 0
    evidence_passed: int = 0

    def log_summary(self) -> None:
        logger.info(
            "Validation summary — retrieved: %d | PI: %d | country: %d | "
            "domain: %d | evidence: %d",
            self.retrieved, self.pi_passed, self.country_passed,
            self.domain_passed, self.evidence_passed,
        )
        logger.info(
            "Rejections — PI: %d | country: %d | domain: %d | evidence: %d",
            self.retrieved - self.pi_passed,
            self.pi_passed - self.country_passed,
            self.country_passed - self.domain_passed,
            self.domain_passed - self.evidence_passed,
        )


class ShortlistPipeline:
    """
    Orchestrates all pipeline stages to produce a ranked PhD supervisor shortlist.
    Dependencies are injected via the constructor (DIP).
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.parser = StudentParser()

        openalex_client = OpenAlexClient(
            email=settings.openalex_email,
            timeout=settings.request_timeout,
            max_retries=settings.max_retries,
        )
        self.openalex = OpenAlexRetriever(
            email=settings.openalex_email,
            timeout=settings.request_timeout,
            max_retries=settings.max_retries,
            per_topic_works=settings.openalex_per_topic,
            authors_per_topic=settings.openalex_authors_per_topic,
            max_topics=settings.openalex_max_topics,
        )
        self.retrievers = [
            self.openalex,
            SemanticScholarRetriever(api_key=settings.semantic_scholar_api_key),
            FacultyScraper(target_urls=settings.faculty_urls),
        ]
        self.pi_validator = PIValidator(
            resolver=FacultyProfileResolver(min_confidence=settings.pi_min_confidence),
            min_confidence=settings.pi_min_confidence,
            use_network=settings.pi_use_network,
        )
        self.evidence_collector = EvidenceCollector(
            client=openalex_client,
            max_works=settings.evidence_max_works,
            recency_years=settings.evidence_recency_years,
        )
        self.scorer = RecommendationScorer(
            reach_threshold=settings.score_reach_threshold,
            safety_threshold=settings.score_safety_threshold,
            feedback_weight=settings.feedback_weight,
            # outcome_learner injected later via load_outcomes()
        )
        # Build LLM client based on configured provider
        llm_client = None
        if settings.llm_provider == "gemini" and settings.gemini_api_key:
            from ..generators.llm_client import GeminiLLMClient
            llm_client = GeminiLLMClient(
                api_key=settings.gemini_api_key,
                model=settings.gemini_model,
            )
            logger.info("LLM provider: Gemini (%s)", settings.gemini_model)
        elif settings.llm_provider == "openai" and settings.openai_api_key:
            from ..generators.llm_client import OpenAILLMClient
            llm_client = OpenAILLMClient(
                api_key=settings.openai_api_key,
                model=settings.openai_model,
            )
            logger.info("LLM provider: OpenAI (%s)", settings.openai_model)
        else:
            logger.info("No LLM API key configured — using deterministic fallback for why_match")
        self.generator = WhyMatchGenerator(
            llm_client=llm_client,
            top_n=settings.why_match_top_n,
            max_words=settings.why_match_max_words,
        )
        self.exporter = JsonExporter(output_dir=Path("sample_output"))
        self.report_exporter = ReportExporter(output_dir=Path("sample_output"))
        self.program_linker = ProgramLinker()
        self.email_extractor = EmailExtractor()

    # ------------------------------------------------------------------ #
    # Optional: outcome learning                                          #
    # ------------------------------------------------------------------ #

    def load_outcomes(self, csv_path: Path) -> OutcomeLearner:
        """
        Load historical email outcome data and wire it into the scorer.

        Safe to call at any time before run() / run_retrieval().
        If the file doesn't exist, logs a warning and returns without
        changing scorer behaviour.

        Args:
            csv_path: Path to outcomes CSV.

        Returns:
            Populated OutcomeLearner instance.
        """
        learner = OutcomeLearner(
            outcome_weights=self.settings.outcome_weights,
            wrong_person_threshold=self.settings.wrong_person_threshold,
        )
        learner.load_csv(csv_path)
        learner.build_supervisor_stats()
        self.scorer.outcome_learner = learner
        logger.info(
            "Outcome learning active: %d supervisors with history",
            len(learner._stats),
        )
        return learner

    # ------------------------------------------------------------------ #
    # Stages 1-5: working end-to-end slice                                #
    # ------------------------------------------------------------------ #

    def run_retrieval(self, input_path: Path) -> dict:
        """
        Run stages 1–7 and return a plain dict for CLI/JSON output.

        Returns:
            Dict with per-gate counts and why_match-annotated recommendations.
        """
        profile = self._parse(input_path)
        logger.info("Parsed profile for '%s' — %d topics", profile.name, len(profile.extracted_topics))

        raw = self.openalex.retrieve(profile)
        logger.info("Retrieved %d candidates from OpenAlex", len(raw))

        validated, counts = self._validate_with_counts(raw, profile)
        counts.log_summary()

        recommendations = self._score_and_rank(validated, profile)
        recommendations = self._generate_why_match(recommendations, profile)
        top_n = recommendations[: self.settings.retrieval_top_n]

        return {
            "topics": profile.extracted_topics,
            "retrieved_count": counts.retrieved,
            "pi_validated_count": counts.pi_passed,
            "country_validated_count": counts.country_passed,
            "domain_validated_count": counts.domain_passed,
            "evidence_validated_count": counts.evidence_passed,
            "recommendation_count": len(recommendations),
            "recommendations": [r.model_dump() for r in top_n],
        }

    # ------------------------------------------------------------------ #
    # Full pipeline (stages 6-8 not yet implemented)                      #
    # ------------------------------------------------------------------ #

    def run(self, input_path: Path, output_path: Path | None = None) -> ShortlistOutput:
        """Full end-to-end pipeline — all 8 stages."""
        profile = self._parse(input_path)
        candidates = self._retrieve(profile)
        validated, counts = self._validate_with_counts(candidates, profile)
        counts.log_summary()
        ranked = self._score_and_rank(validated, profile)
        shortlist = self._generate_why_match(ranked, profile)
        shortlist = self._enrich_recommendations(shortlist)
        output = self._build_output(profile, counts.retrieved, shortlist)
        written = self._export(output, output_path)
        logger.info(
            "Done. %d recommendations → %s", len(output.recommendations), written
        )
        return output

    def run_with_report(
        self,
        input_path: Path,
        output_path: Path | None = None,
    ) -> tuple[ShortlistOutput, PipelineReport]:
        """
        Run the full pipeline and capture per-stage timing + validation counts.

        Returns:
            (ShortlistOutput, PipelineReport) — both fully populated.
        """
        total_start = time.perf_counter()

        # Stage 1: parse (fast — not timed separately)
        profile = self._parse(input_path)

        # Stage 2: retrieval
        t0 = time.perf_counter()
        candidates = self._retrieve(profile)
        retrieval_s = time.perf_counter() - t0

        # Stages 3–5: validation + evidence collection
        t0 = time.perf_counter()
        validated, counts = self._validate_with_counts(candidates, profile)
        counts.log_summary()
        validation_s = time.perf_counter() - t0

        # Stage 6: scoring
        t0 = time.perf_counter()
        ranked = self._score_and_rank(validated, profile)
        scoring_s = time.perf_counter() - t0

        # Stage 7: why match
        t0 = time.perf_counter()
        shortlist = self._generate_why_match(ranked, profile)
        why_match_s = time.perf_counter() - t0

        # Stage 8: enrichment (program linking + email extraction) + build + export
        t0 = time.perf_counter()
        shortlist = self._enrich_recommendations(shortlist)
        output = self._build_output(profile, counts.retrieved, shortlist)
        written = self._export(output, output_path)
        export_s = time.perf_counter() - t0

        total_s = time.perf_counter() - total_start

        logger.info("Done. %d recommendations → %s", len(output.recommendations), written)

        # Assemble report
        tier_counts: dict[str, int] = {}
        for rec in shortlist:
            tier_counts[rec.tier] = tier_counts.get(rec.tier, 0) + 1

        report = PipelineReport(
            student_name=profile.name,
            student_id=profile.name,
            validation=ValidationStageMetrics(
                retrieved_count=counts.retrieved,
                pi_validated_count=counts.pi_passed,
                country_validated_count=counts.country_passed,
                domain_validated_count=counts.domain_passed,
                evidence_validated_count=counts.evidence_passed,
                recommendation_count=len(shortlist),
                emails_found_count=sum(1 for r in shortlist if r.contact_email),
                program_links_found_count=sum(len(r.linked_programs) for r in shortlist),
            ),
            runtime=RuntimeMetrics(
                retrieval_seconds=round(retrieval_s, 3),
                validation_seconds=round(validation_s, 3),
                scoring_seconds=round(scoring_s, 3),
                why_match_seconds=round(why_match_s, 3),
                export_seconds=round(export_s, 3),
                total_runtime_seconds=round(total_s, 3),
            ),
            tier_counts=tier_counts,
        )
        return output, report

    # ------------------------------------------------------------------ #
    # Stage implementations                                               #
    # ------------------------------------------------------------------ #

    def _parse(self, input_path: Path) -> StudentProfile:
        """Stage 1 — Parse and validate student profile from JSON file."""
        return self.parser.parse_file(input_path)

    def _retrieve(self, profile: StudentProfile) -> list[Supervisor]:
        """Stage 2 — Retrieve from all sources; deduplicate by (name, institution)."""
        seen: set[tuple[str, str]] = set()
        results: list[Supervisor] = []
        for retriever in self.retrievers:
            for s in retriever.retrieve(profile):
                key = (s.name.lower(), s.institution.lower())
                if key not in seen:
                    seen.add(key)
                    results.append(s)
        return results

    def _validate_with_counts(
        self, candidates: list[Supervisor], profile: StudentProfile
    ) -> tuple[list[Supervisor], ValidationCounts]:
        """
        Run the full 4-gate validation chain and return (passed, counts).

        Order: PI → Evidence Collection → Country → Domain → Evidence.
        Evidence collection is interleaved after PI so that Country, Domain
        and Evidence validators have access to enriched data.
        """
        counts = ValidationCounts(retrieved=len(candidates))

        # Gate 1: PI
        after_pi = [s for s in candidates if self.pi_validator.validate(s)]
        counts.pi_passed = len(after_pi)
        logger.info("PI rejected: %d", counts.retrieved - counts.pi_passed)

        # Evidence collection (between PI and remaining gates)
        after_evidence_collection = self.evidence_collector.enrich_all(after_pi)

        # Gate 2: Country
        country_validator = CountryValidator(profile)
        after_country = [
            s for s in after_evidence_collection if country_validator.validate(s)
        ]
        counts.country_passed = len(after_country)
        logger.info("Country rejected: %d", counts.pi_passed - counts.country_passed)

        # Gate 3: Domain
        embedding_service = None
        domain_mode = "keyword_only"
        if self.settings.embedding_domain_enabled:
            try:
                from ..services.embedding_service import get_embedding_service
                embedding_service = get_embedding_service(self.settings.embedding_model_name)
                domain_mode = "hybrid"
            except Exception as exc:
                logger.warning(
                    "Embedding service unavailable (%s) — domain validator using keyword_only", exc
                )
        domain_validator = DomainValidator(
            profile,
            min_score=self.settings.domain_min_score,
            mode=domain_mode,
            embedding_service=embedding_service,
            embedding_min_score=self.settings.embedding_domain_threshold,
        )
        after_domain = [s for s in after_country if domain_validator.validate(s)]
        counts.domain_passed = len(after_domain)
        logger.info("Domain rejected: %d", counts.country_passed - counts.domain_passed)

        # Gate 4: Evidence quality
        evidence_validator = EvidenceValidator(
            min_recent_publications=self.settings.evidence_min_recent_publications,
            min_total_citations=self.settings.evidence_min_total_citations,
            max_publication_gap=self.settings.evidence_max_publication_gap,
        )
        after_evidence_val = [s for s in after_domain if evidence_validator.validate(s)]
        counts.evidence_passed = len(after_evidence_val)
        logger.info("Evidence rejected: %d", counts.domain_passed - counts.evidence_passed)

        return after_evidence_val, counts

    def _collect_evidence(self, supervisors: list[Supervisor]) -> list[Supervisor]:
        """Standalone evidence collection (used when called outside _validate_with_counts)."""
        return self.evidence_collector.enrich_all(supervisors)

    def _score_and_rank(
        self, supervisors: list[Supervisor], profile: StudentProfile
    ) -> list[Recommendation]:
        """Stage 6 — Score, sort, and tier-assign all validated supervisors."""
        return self.scorer.score_all(supervisors, profile)

    def _enrich_recommendations(
        self, recommendations: list[Recommendation]
    ) -> list[Recommendation]:
        """
        Stage 8 enrichment — program linking + email extraction.
        Best-effort: failures are caught per-recommendation so one bad
        HTTP call never aborts the entire batch.
        """
        for rec in recommendations:
            try:
                linker_results = self.program_linker.link(rec.supervisor)
                rec.linked_programs = [
                    LinkedProgram(
                        program_name=lp.program_name,
                        institution=lp.institution,
                        url=lp.url,
                        status=lp.status,
                    )
                    for lp in linker_results
                ]
            except Exception as exc:
                logger.warning("ProgramLinker failed for %s: %s", rec.supervisor.name, exc)

            try:
                email_result = self.email_extractor.extract(rec.supervisor)
                if email_result.email:
                    rec.contact_email = email_result.email
                    rec.email_source = email_result.source
                    # Also write back to supervisor for downstream use
                    rec.supervisor.email = email_result.email
            except Exception as exc:
                logger.warning("EmailExtractor failed for %s: %s", rec.supervisor.name, exc)

        emails = sum(1 for r in recommendations if r.contact_email)
        links = sum(len(r.linked_programs) for r in recommendations)
        logger.info(
            "Enrichment complete — emails: %d/%d | program links: %d",
            emails, len(recommendations), links,
        )
        return recommendations

    def _generate_why_match(
        self,
        recommendations: list[Recommendation],
        profile: StudentProfile,
    ) -> list[Recommendation]:
        """Stage 7 — Populate why_match on top-N recommendations."""
        return self.generator.generate_batch(profile, recommendations)

    def _build_output(
        self,
        profile: StudentProfile,
        total_candidates: int,
        recommendations: list[Recommendation],
    ) -> ShortlistOutput:
        """Stage 8a — Assemble the final ShortlistOutput model."""
        from datetime import datetime, timezone
        from ..validators.country_validator import _normalise as _norm_country

        return ShortlistOutput(
            student_name=profile.name,
            student_id=profile.name,
            total_candidates_evaluated=total_candidates,
            recommendation_count=len(recommendations),
            generated_at=datetime.now(timezone.utc).isoformat(),
            recommendations=recommendations,
        )

    def _export(self, output: ShortlistOutput, path: Path | None = None) -> Path:
        """Stage 8b — Serialise ShortlistOutput to JSON via JsonExporter."""
        return self.exporter.export(output, output_path=path)
