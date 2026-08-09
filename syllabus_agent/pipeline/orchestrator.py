"""Runs the pipeline stages in order, short-circuiting after classification
whenever the route isn't genuine_academic_subject.
"""

import logging

from syllabus_agent.clients.extraction_client import BaseExtractor
from syllabus_agent.clients.llm_client import LLMClient
from syllabus_agent.clients.search_client import SearchClient
from syllabus_agent.pipeline.classifier.classify import classify_subject
from syllabus_agent.pipeline.extraction.extract import Fetcher, extract_sources
from syllabus_agent.pipeline.query_generation.generate import generate_queries
from syllabus_agent.pipeline.relevance.assess import assess_relevance
from syllabus_agent.pipeline.source_collection.collect import collect_sources
from syllabus_agent.pipeline.structuring.structure import semantic_merge, structure_per_source
from syllabus_agent.schemas.enums import PipelineStage, RouteDecision
from syllabus_agent.schemas.extraction import ExtractionResult
from syllabus_agent.schemas.pipeline import PipelineResult
from syllabus_agent.schemas.relevance import RelevanceResult
from syllabus_agent.utils.trust_scoring import relevance_multiplier

logger = logging.getLogger(__name__)


def apply_relevance_filter(
    extraction: ExtractionResult, relevance: list[RelevanceResult]
) -> ExtractionResult:
    """Drop field_level/unrelated sources and record the partial-match demotion.

    The demotion is stored on `relevance_penalty` rather than folded into
    `trust_score`, because the two are consumed for different purposes:
    ranking/selection should demote a partial match, but the merge prompt's
    keep/drop threshold must see the source's true trust. Folding them together
    meant no partial-match source could ever clear `trust >= 0.7`, so its
    single-source topics were dropped.

    Rebuilds an ExtractionResult rather than mutating, so the structuring stage
    keeps its existing signature and stays unaware of this stage.
    """
    verdicts = {result.source_url: result for result in relevance}

    kept_blocks = []
    for block in extraction.blocks:
        result = verdicts.get(str(block.source_url))
        if result is None or not result.is_usable:
            continue
        kept_blocks.append(
            block.model_copy(
                update={"relevance_penalty": relevance_multiplier(result.verdict)}
            )
        )

    return ExtractionResult(
        subject=extraction.subject, blocks=kept_blocks, failures=extraction.failures
    )


async def run_pipeline(
    subject: str,
    *,
    llm: LLMClient,
    search: SearchClient,
    html_extractor: BaseExtractor,
    pdf_text_extractor: BaseExtractor,
    pdf_ocr_extractor: BaseExtractor,
    fetch: Fetcher | None = None,
) -> PipelineResult:
    """Single orchestrator entry point for the whole search-and-structure pipeline."""
    classification = await classify_subject(subject, llm)

    if classification.route != RouteDecision.GENUINE_ACADEMIC_SUBJECT:
        return PipelineResult(
            subject=subject,
            route=classification.route,
            classification=classification,
            stage_reached=PipelineStage.CLASSIFICATION,
        )

    query_result = await generate_queries(subject, llm)
    source_result = await collect_sources(subject, query_result.queries, search)
    extraction_result = await extract_sources(
        subject,
        source_result.sources,
        html_extractor=html_extractor,
        pdf_text_extractor=pdf_text_extractor,
        pdf_ocr_extractor=pdf_ocr_extractor,
        fetch=fetch,
    )
    relevance = await assess_relevance(subject, extraction_result.as_sources(), llm)
    relevant_extraction = apply_relevance_filter(extraction_result, relevance)

    surviving = len({block.source_url for block in relevant_extraction.blocks})
    if surviving == 0:
        logger.warning(
            "No source survived relevance filtering for %r — %s assessed, none direct/partial. "
            "Returning an error rather than an empty syllabus.",
            subject,
            len(relevance),
        )
        return PipelineResult(
            subject=subject,
            route=classification.route,
            classification=classification,
            stage_reached=PipelineStage.RELEVANCE,
            error=(
                f"No collected source was relevant enough to {subject!r} to structure "
                f"({len(relevance)} assessed, none classified direct_match or partial_match)."
            ),
        )

    logger.info(
        "relevance: %s of %s sources survived filtering.",
        surviving,
        len(relevance),
    )

    structures, skipped, ranking = await structure_per_source(subject, relevant_extraction, llm)
    syllabus = await semantic_merge(
        subject,
        structures,
        llm,
        collected_not_structured=skipped,
        source_ranking=ranking,
    )

    return PipelineResult(
        subject=subject,
        route=classification.route,
        classification=classification,
        stage_reached=PipelineStage.STRUCTURING,
        syllabus=syllabus,
    )
