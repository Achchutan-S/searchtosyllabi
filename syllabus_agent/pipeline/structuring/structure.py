"""Stage 5: structuring, then semantic merge.

Two entry points:

- `structure_per_source()` — one LLM call per selected source, extracting
  whatever structure that document actually contains. No target unit count is
  imposed; a source with three units yields three.
- `semantic_merge()` — one LLM call over all per-source structures, grouping
  topics by *meaning* into a canonical syllabus.

The merge is semantic rather than positional by design. Aligning sources on
`unit_number` welded together documents with incompatible numbering, producing a
unit titled "Exploring Quantitative Analysis" that contained "Sorting Algorithms,
Lists, Stacks". Unit identity is thematic, so the merge has to reason about
meaning.
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

from pydantic import HttpUrl, ValidationError

from syllabus_agent.clients.llm_client import ChatMessage, LLMClient
from syllabus_agent.logging_setup import stage_context
from syllabus_agent.prompts import load_prompt
from syllabus_agent.schemas.extraction import ExtractionResult, RawTextBlock
from syllabus_agent.schemas.syllabus import (
    CanonicalSyllabus,
    MergedTopic,
    MergedUnit,
    PerSourceStructure,
    SourceRanking,
    SourceUnit,
)
from syllabus_agent.utils.trust_scoring import blend_scores, score_content_richness

logger = logging.getLogger(__name__)

_STRUCTURING_PROMPT = load_prompt("structuring")
_MERGE_PROMPT = load_prompt("merge")

TOP_N_SOURCES_TO_STRUCTURE = 4
"""Only the N highest-ranked sources get a structuring call. A real subject
collects ~40 sources; structuring all of them would spend ~40 LLM calls for
little gain, and the merge works better over a handful of good syllabi than a
crowd of mediocre ones.
"""


async def structure_per_source(
    subject: str, extraction: ExtractionResult, llm: LLMClient
) -> tuple[list[PerSourceStructure], list[HttpUrl], list[SourceRanking]]:
    """Structure the top-N ranked sources.

    Returns (structures, skipped_source_urls, full_ranking_table).
    """
    blocks_by_source: dict[HttpUrl, list[RawTextBlock]] = defaultdict(list)
    for block in extraction.blocks:
        blocks_by_source[block.source_url].append(block)

    # Rank before spending any LLM call, blending domain reputation with how much
    # the extracted text actually looks like a syllabus.
    scored: list[tuple[float, HttpUrl, list[RawTextBlock], float, float, int, float]] = []
    for source_url, source_blocks in blocks_by_source.items():
        ordered = sorted(source_blocks, key=lambda b: b.order_index)
        domain_score = max(b.trust_score for b in ordered)
        penalty = min(b.relevance_penalty for b in ordered)
        text = "\n".join(b.text for b in ordered)
        richness = score_content_richness(text)
        # The relevance penalty demotes a partial match for *selection*; the
        # unpenalised domain_score is what the merge step is told about.
        blended = blend_scores(domain_score * penalty, richness.score)

        logger.debug(
            "ranking %s -> blended=%.3f (domain=%.2f x penalty=%.2f, content=%.3f) | signals: %s",
            source_url,
            blended,
            domain_score,
            penalty,
            richness.score,
            richness.explain(),
        )
        scored.append(
            (blended, source_url, ordered, domain_score, richness.score, len(text), penalty)
        )

    scored.sort(key=lambda row: row[0], reverse=True)
    chosen = scored[:TOP_N_SOURCES_TO_STRUCTURE]
    rejected = scored[TOP_N_SOURCES_TO_STRUCTURE:]

    ranking = [
        SourceRanking(
            source_url=source_url,
            domain_score=domain_score,
            content_score=content_score,
            blended_score=blended,
            relevance_penalty=penalty,
            extracted_chars=chars,
            structured=index < TOP_N_SOURCES_TO_STRUCTURE,
        )
        for index, (
            blended,
            source_url,
            _,
            domain_score,
            content_score,
            chars,
            penalty,
        ) in enumerate(scored)
    ]

    logger.info(
        "Structuring top %s of %s relevance-approved sources (%s below the blended cutoff).",
        len(chosen),
        len(scored),
        len(rejected),
    )
    for blended, source_url, _, domain_score, content_score, chars, penalty in chosen:
        logger.info(
            "  selected  blended=%.3f (domain=%.2f x%.2f content=%.3f) %6s chars  %s",
            blended,
            domain_score,
            penalty,
            content_score,
            chars,
            source_url,
        )

    structures: list[PerSourceStructure] = []
    for blended, source_url, ordered, domain_score, _, _, _ in chosen:
        structure = await _structure_one(
            source_url=source_url,
            trust_score=domain_score,
            texts=[block.text for block in ordered],
            llm=llm,
        )
        if structure is not None:
            logger.info(
                "  structured %s -> %s units, %s topics",
                source_url,
                len(structure.units),
                structure.topic_count,
            )
            logger.debug("Per-source structure: %s", structure.model_dump_json(indent=2))
            structures.append(structure)

    return structures, [row[1] for row in rejected], ranking


async def _structure_one(
    source_url: HttpUrl, trust_score: float, texts: list[str], llm: LLMClient
) -> PerSourceStructure | None:
    """Structure one source. Returns None if the response is unusable, so a
    single bad source degrades the merge rather than failing the run.
    """
    messages = [
        ChatMessage(role="system", content=_STRUCTURING_PROMPT),
        ChatMessage(role="user", content=f"Subject context: {source_url}\n\n" + "\n".join(texts)),
    ]

    with stage_context("structuring"):
        raw = await llm.chat_completion(messages, response_format={"type": "json_object"})

    try:
        data = json.loads(raw)
        units = [SourceUnit(**unit) for unit in data["units"]]
    except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc:
        logger.error(
            "Structuring could not parse the response for %s (%r). Raw: %s",
            source_url,
            exc,
            raw[:1000],
        )
        return None

    # source_url comes from our own record, never from the model's echo of it.
    return PerSourceStructure(
        source_url=str(source_url),
        trust_score=trust_score,
        units=units,
        notes=data.get("notes"),
    )


async def semantic_merge(
    subject: str,
    structures: list[PerSourceStructure],
    llm: LLMClient,
    *,
    collected_not_structured: list[HttpUrl] | None = None,
    source_ranking: list[SourceRanking] | None = None,
) -> CanonicalSyllabus:
    """Synthesise per-source structures into one canonical syllabus.

    A single LLM call with every structure in context — with the relevance filter
    and the top-N cap upstream this is ~4 sources of 10-30 topics each, well
    inside the context window.
    """
    if not structures:
        logger.warning("semantic_merge called with no structures; returning an empty syllabus.")
        return CanonicalSyllabus(
            subject=subject,
            merge_notes="No source produced a usable structure.",
            collected_not_structured=collected_not_structured or [],
            source_ranking=source_ranking or [],
            generated_at=datetime.now(timezone.utc),
        )

    payload = "\n\n".join(
        f"--- Source {index} (trust_score: {structure.trust_score:.2f}, "
        f"url: {structure.source_url}) ---\n"
        + json.dumps(
            {"units": [unit.model_dump() for unit in structure.units], "notes": structure.notes},
            ensure_ascii=False,
        )
        for index, structure in enumerate(structures, start=1)
    )

    messages = [
        ChatMessage(role="system", content=_MERGE_PROMPT),
        ChatMessage(
            role="user",
            content=(
                f"Subject: {subject}\n\n"
                f"Below are structured syllabi extracted from {len(structures)} university "
                f"sources. Each has its own unit structure and topic list.\n\n{payload}"
            ),
        ),
    ]

    with stage_context("merge"):
        raw = await llm.chat_completion(messages, response_format={"type": "json_object"})

    try:
        data = json.loads(raw)
        units = [
            MergedUnit(
                unit_title=unit["unit_title"],
                topics=[
                    MergedTopic(name=topic["name"], source_urls=topic.get("source_urls", []))
                    for topic in unit.get("topics", [])
                ],
            )
            for unit in data["units"]
        ]
        merge_notes = data.get("merge_notes", "")
    except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc:
        logger.error("Semantic merge could not parse the response (%r). Raw: %s", exc, raw[:1000])
        raise

    # Recomputed rather than trusting the model's own arithmetic.
    total_topics = sum(len(unit.topics) for unit in units)

    logger.info(
        "merge: %s sources -> %s units, %s topics.", len(structures), len(units), total_topics
    )
    logger.debug("merge_notes: %s", merge_notes)

    return CanonicalSyllabus(
        subject=subject,
        units=units,
        total_topics=total_topics,
        merge_notes=merge_notes,
        source_count=len(structures),
        source_urls=[structure.source_url for structure in structures],
        per_source_structures=structures,
        collected_not_structured=collected_not_structured or [],
        source_ranking=source_ranking or [],
        generated_at=datetime.now(timezone.utc),
    )
