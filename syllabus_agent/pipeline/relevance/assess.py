"""Stage 4.5: relevance assessment.

Runs after extraction and before structuring. Answers a question neither domain
reputation nor content richness can: is this document about *this course*, or
about the whole field?

The motivating failure: a university's full CS degree catalog is `.edu`, long,
densely sectioned and credit-bearing, so it outranked real syllabi and produced
a "data structures" syllabus containing units named "Introduction to Robotics"
and "Web Development" — other courses from the same catalog.
"""

import asyncio
import json
import logging

from pydantic import ValidationError

from syllabus_agent.clients.llm_client import ChatMessage, LLMClient
from syllabus_agent.logging_setup import stage_context
from syllabus_agent.prompts import load_prompt
from syllabus_agent.schemas.enums import RelevanceVerdict
from syllabus_agent.schemas.extraction import ExtractedSource
from syllabus_agent.schemas.relevance import RelevanceResult

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = load_prompt("relevance")

# Enough to identify what a document is (title, headers, first unit) without
# spending tokens on the body of a 40,000-char PDF.
MAX_TEXT_CHARS = 3_000

# Concurrency for the per-source calls. Kept low deliberately: free-tier
# providers cap at ~5 requests/minute, so a wide fan-out just converts into 429s
# and retry backoff rather than finishing faster.
MAX_CONCURRENT_ASSESSMENTS = 4

# One LLM call per source, so a 30-source run costs 30 calls. Set to None to
# assess everything; a cap keeps a run affordable on a free tier, at the risk of
# discarding a relevant source that ranked poorly on the pre-relevance signals.
MAX_SOURCES_TO_ASSESS: int | None = None


def _build_user_prompt(subject: str, source: ExtractedSource) -> str:
    return (
        f"Target subject: {subject}\n\n"
        f"Source URL: {source.source_url}\n"
        f"Extracted text (first {MAX_TEXT_CHARS} characters):\n"
        f'"""\n{source.text[:MAX_TEXT_CHARS]}\n"""'
    )


async def assess_relevance(
    subject: str,
    sources: list[ExtractedSource],
    llm_client: LLMClient,
) -> list[RelevanceResult]:
    """Entry point for the relevance stage. One LLM call per source."""
    if not sources:
        return []

    considered = sources
    if MAX_SOURCES_TO_ASSESS is not None and len(sources) > MAX_SOURCES_TO_ASSESS:
        considered = sorted(sources, key=lambda s: s.trust_score, reverse=True)[
            :MAX_SOURCES_TO_ASSESS
        ]
        logger.warning(
            "Assessing only the %s highest-trust of %s sources (MAX_SOURCES_TO_ASSESS).",
            len(considered),
            len(sources),
        )

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_ASSESSMENTS)

    async def guarded(source: ExtractedSource) -> RelevanceResult:
        async with semaphore:
            return await _assess_one(subject, source, llm_client)

    results = await asyncio.gather(*(guarded(source) for source in considered))

    counts: dict[str, int] = {}
    for result in results:
        counts[result.verdict.value] = counts.get(result.verdict.value, 0) + 1
    logger.info("relevance: assessed %s sources — %s", len(results), counts)

    return list(results)


async def _assess_one(
    subject: str, source: ExtractedSource, llm_client: LLMClient
) -> RelevanceResult:
    messages = [
        ChatMessage(role="system", content=_SYSTEM_PROMPT),
        ChatMessage(role="user", content=_build_user_prompt(subject, source)),
    ]

    url = str(source.source_url)

    try:
        with stage_context("relevance"):
            raw = await llm_client.chat_completion(
                messages, response_format={"type": "json_object"}
            )
    except Exception as exc:  # noqa: BLE001 - one bad source must not end the run
        logger.warning("Relevance call failed for %s: %r — treating as unrelated.", url, exc)
        return RelevanceResult(
            source_url=url,
            verdict=RelevanceVerdict.UNRELATED,
            confidence=0.0,
            reasoning=f"relevance call failed: {exc!r}",
        )

    try:
        payload = json.loads(raw)
        result = RelevanceResult(
            source_url=url,
            verdict=payload["verdict"],
            confidence=payload["confidence"],
            reasoning=payload["reasoning"],
        )
    except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc:
        logger.error(
            "Relevance response for %s was unparseable (%r). Raw: %s", url, exc, raw[:500]
        )
        # Excluding on a parse failure would silently drop a possibly-good
        # source; keeping it as a partial match lets structuring decide, and the
        # log records why.
        return RelevanceResult(
            source_url=url,
            verdict=RelevanceVerdict.PARTIAL_MATCH,
            confidence=0.0,
            reasoning=f"unparseable relevance response, defaulting to partial_match: {exc!r}",
        )

    logger.info(
        "[RELEVANCE] %s → %s (%.2f): %s",
        url,
        result.verdict.value,
        result.confidence,
        result.reasoning,
    )
    return result
