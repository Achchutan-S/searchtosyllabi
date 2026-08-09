"""Stage 3: source_collection.

Runs each generated query through the search client, filters candidate URLs down
to trustworthy sources, and attaches a trust score + metadata to each survivor.
"""

import logging

from syllabus_agent.clients.extraction_client import detect_format
from syllabus_agent.clients.search_client import SearchClient
from syllabus_agent.logging_setup import stage_context
from syllabus_agent.schemas.query import SearchQuery
from syllabus_agent.schemas.source import CandidateSource, SourceCollectionResult
from syllabus_agent.utils.trust_scoring import (
    KNOWN_UNIVERSITY_SUFFIXES,
    extract_domain,
    score_source_with_reason,
)

logger = logging.getLogger(__name__)

MIN_TRUST_SCORE = 0.5


async def collect_sources(
    subject: str, queries: list[SearchQuery], search: SearchClient
) -> SourceCollectionResult:
    """Entry point for stage 3."""
    sources: list[CandidateSource] = []
    seen_urls: set[str] = set()
    total_hits = 0
    dropped_duplicate = 0
    dropped_low_trust = 0

    for query in queries:
        with stage_context("source_collection"):
            hits = await search.search(query.query)
        total_hits += len(hits)

        for hit in hits:
            if hit.url in seen_urls:
                dropped_duplicate += 1
                logger.debug("Dropped %s — duplicate of an already-collected URL.", hit.url)
                continue

            trust_score, reason = score_source_with_reason(hit.url)
            if trust_score < MIN_TRUST_SCORE:
                dropped_low_trust += 1
                logger.debug(
                    "Dropped %s — trust %.2f below threshold %.2f (%s).",
                    hit.url,
                    trust_score,
                    MIN_TRUST_SCORE,
                    reason,
                )
                continue

            logger.debug("Kept %s — trust %.2f (%s).", hit.url, trust_score, reason)
            seen_urls.add(hit.url)
            domain = extract_domain(hit.url)
            sources.append(
                CandidateSource(
                    url=hit.url,
                    title=hit.title,
                    query=query.query,
                    format=detect_format(hit.url, None),
                    trust_score=trust_score,
                    domain=domain,
                    university=domain if domain.endswith(KNOWN_UNIVERSITY_SUFFIXES) else None,
                    pre_extracted_content=hit.pre_extracted_content,
                )
            )

    logger.info(
        "source_collection: %s hits from %s queries -> %s kept "
        "(%s duplicates, %s below trust threshold %.2f).",
        total_hits,
        len(queries),
        len(sources),
        dropped_duplicate,
        dropped_low_trust,
        MIN_TRUST_SCORE,
    )

    return SourceCollectionResult(subject=subject, sources=sources)
