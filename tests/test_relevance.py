"""Relevance-stage tests.

The fixtures are modelled on the documents that actually caused the failure this
stage exists to fix: a real data-structures syllabus, an RMC-style full CS degree
catalog, and an unrelated page.
"""

import json
import os

import pytest

from syllabus_agent.config import get_settings
from syllabus_agent.pipeline.orchestrator import apply_relevance_filter
from syllabus_agent.pipeline.relevance.assess import assess_relevance
from syllabus_agent.schemas.enums import ExtractionMethod, RelevanceVerdict
from syllabus_agent.schemas.extraction import ExtractedSource, ExtractionResult, RawTextBlock
from syllabus_agent.schemas.relevance import RelevanceResult

DS_SYLLABUS_TEXT = (
    "CS 2280 Data Structures. Unit I: arrays, linked lists, stacks, queues, "
    "complexity analysis. Unit II: trees, binary search trees, AVL trees, heaps. "
    "Unit III: hashing, hash functions, collision resolution. Unit IV: graphs, "
    "shortest paths, minimum spanning trees. Credits: 4."
)

CS_CATALOG_TEXT = (
    "COMPUTER SCIENCE Faculty: Necaise, Chair; Elouni, Givens, Henry. The computer "
    "science curriculum integrates theory and practice. Courses: Introduction to "
    "Robotics, Web Development, Machine Learning, Operating Systems, Database "
    "Systems, Computer Graphics, Theory of Computation, Data Structures."
)

UNRELATED_TEXT = "Campus dining hours and meal plan options for the fall semester."


def _source(url: str, text: str, trust: float = 0.8) -> ExtractedSource:
    return ExtractedSource(
        source_url=url,
        text=text,
        trust_score=trust,
        method=ExtractionMethod.HTML_PARSER,
    )


def _verdict_json(verdict: str, confidence: float = 0.9, reasoning: str = "because") -> str:
    return json.dumps({"verdict": verdict, "confidence": confidence, "reasoning": reasoning})


# --- prompt / parsing -------------------------------------------------------


async def test_parses_a_canned_verdict_into_a_relevance_result(fake_llm):
    fake_llm.responses.append(
        _verdict_json("direct_match", 0.95, "This is a data structures course outline.")
    )

    results = await assess_relevance(
        "data structures", [_source("https://x.edu/cs2280", DS_SYLLABUS_TEXT)], fake_llm
    )

    assert len(results) == 1
    assert results[0].verdict == RelevanceVerdict.DIRECT_MATCH
    assert results[0].confidence == 0.95
    assert results[0].source_url == "https://x.edu/cs2280"
    assert results[0].is_usable


async def test_accepts_uppercase_verdicts_from_the_model(fake_llm):
    """The prompt shows lowercase, but models routinely echo the enum in caps."""
    fake_llm.responses.append(_verdict_json("DIRECT_MATCH"))

    results = await assess_relevance(
        "data structures", [_source("https://x.edu/a", DS_SYLLABUS_TEXT)], fake_llm
    )

    assert results[0].verdict == RelevanceVerdict.DIRECT_MATCH


async def test_source_text_is_truncated_before_being_sent(fake_llm):
    fake_llm.responses.append(_verdict_json("direct_match"))
    huge = "x" * 50_000

    await assess_relevance("data structures", [_source("https://x.edu/a", huge)], fake_llm)

    user_msg = next(m for m in fake_llm.calls[0] if m.role == "user")
    assert len(user_msg.content) < 5_000


async def test_unparseable_response_defaults_to_partial_match_not_exclusion(fake_llm):
    """Dropping a source because the model returned prose would silently lose
    possibly-good material; keep it and record why.
    """
    fake_llm.responses.append("I think this is probably a syllabus!")

    results = await assess_relevance(
        "data structures", [_source("https://x.edu/a", DS_SYLLABUS_TEXT)], fake_llm
    )

    assert results[0].verdict == RelevanceVerdict.PARTIAL_MATCH
    assert results[0].confidence == 0.0
    assert "unparseable" in results[0].reasoning


async def test_a_failing_llm_call_marks_the_source_unrelated_without_raising(fake_llm):
    class Boom(type(fake_llm)):
        async def chat_completion(self, messages, **kwargs):
            raise RuntimeError("provider exploded")

    results = await assess_relevance(
        "data structures", [_source("https://x.edu/a", DS_SYLLABUS_TEXT)], Boom()
    )

    assert results[0].verdict == RelevanceVerdict.UNRELATED
    assert "provider exploded" in results[0].reasoning


async def test_one_llm_call_per_source(fake_llm):
    sources = [_source(f"https://x.edu/{i}", DS_SYLLABUS_TEXT) for i in range(5)]

    results = await assess_relevance("data structures", sources, fake_llm)

    assert len(results) == 5
    assert len(fake_llm.calls) == 5


async def test_empty_source_list_makes_no_calls(fake_llm):
    assert await assess_relevance("data structures", [], fake_llm) == []
    assert fake_llm.calls == []


# --- filtering --------------------------------------------------------------


def _extraction_with(urls: list[str]) -> ExtractionResult:
    return ExtractionResult(
        subject="data structures",
        blocks=[
            RawTextBlock(
                source_url=url,
                method=ExtractionMethod.HTML_PARSER,
                text=DS_SYLLABUS_TEXT,
                order_index=0,
                trust_score=0.8,
            )
            for url in urls
        ],
    )


def test_only_direct_and_partial_matches_pass_through():
    urls = [
        "https://x.edu/direct",
        "https://x.edu/partial",
        "https://x.edu/field",
        "https://x.edu/unrelated",
    ]
    relevance = [
        RelevanceResult(source_url=urls[0], verdict="direct_match", confidence=0.9, reasoning="r"),
        RelevanceResult(source_url=urls[1], verdict="partial_match", confidence=0.8, reasoning="r"),
        RelevanceResult(source_url=urls[2], verdict="field_level", confidence=0.9, reasoning="r"),
        RelevanceResult(source_url=urls[3], verdict="unrelated", confidence=0.9, reasoning="r"),
    ]

    filtered = apply_relevance_filter(_extraction_with(urls), relevance)

    survivors = {str(block.source_url) for block in filtered.blocks}
    assert survivors == {urls[0], urls[1]}


def test_sources_with_no_verdict_are_dropped():
    filtered = apply_relevance_filter(_extraction_with(["https://x.edu/a"]), [])

    assert filtered.blocks == []


# --- zero survivors ---------------------------------------------------------


async def test_pipeline_returns_an_error_when_nothing_survives_relevance(
    fake_llm, fake_search, fake_html_extractor, fake_pdf_text_extractor, fake_pdf_ocr_extractor
):
    """An empty syllabus would look like a successful run that found nothing."""
    from syllabus_agent.pipeline.orchestrator import run_pipeline
    from syllabus_agent.schemas.enums import PipelineStage

    fake_llm.responses.append(
        json.dumps(
            {
                "subject": "data structures",
                "route": "genuine_academic_subject",
                "confidence": 0.9,
                "reasoning": "test",
                "clarifying_question": None,
                "suggested_refinements": [],
            }
        )
    )
    fake_llm.responses.append(
        json.dumps(
            {
                "subject": "data structures",
                "queries": [{"query": "data structures syllabus", "source_hint": None}],
            }
        )
    )
    # Every relevance verdict says field_level.
    for _ in range(10):
        fake_llm.responses.append(_verdict_json("field_level", 0.9, "whole degree catalog"))

    result = await run_pipeline(
        "data structures",
        llm=fake_llm,
        search=fake_search,
        html_extractor=fake_html_extractor,
        pdf_text_extractor=fake_pdf_text_extractor,
        pdf_ocr_extractor=fake_pdf_ocr_extractor,
    )

    assert result.syllabus is None
    assert result.stage_reached == PipelineStage.RELEVANCE
    assert result.error is not None
    assert "relevant" in result.error


# --- live integration -------------------------------------------------------

_HAS_KEY = bool(os.getenv("GEMINI_API_KEY", "").strip()) or bool(
    get_settings().gemini_api_key.strip()
)


@pytest.mark.skipif(not _HAS_KEY, reason="GEMINI_API_KEY not set")
async def test_live_relevance_separates_syllabus_catalog_and_unrelated():
    from syllabus_agent.clients.llm_client import OpenAICompatibleLLMClient

    settings = get_settings()
    llm = OpenAICompatibleLLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
    )

    results = await assess_relevance(
        "data structures",
        [
            _source("https://x.edu/cs2280-syllabus", DS_SYLLABUS_TEXT),
            _source("https://x.edu/cs-degree-catalog", CS_CATALOG_TEXT),
            _source("https://x.edu/dining", UNRELATED_TEXT),
        ],
        llm,
    )

    by_url = {r.source_url: r.verdict for r in results}
    assert by_url["https://x.edu/cs2280-syllabus"] == RelevanceVerdict.DIRECT_MATCH
    # The catalog mentions data structures among many courses — either verdict is
    # defensible, but it must not be treated as a direct match.
    assert by_url["https://x.edu/cs-degree-catalog"] in (
        RelevanceVerdict.PARTIAL_MATCH,
        RelevanceVerdict.FIELD_LEVEL,
    )
    assert by_url["https://x.edu/dining"] == RelevanceVerdict.UNRELATED


# --- trust separation: penalty affects ranking, not merge input --------------


def test_partial_match_penalty_does_not_reduce_trust_score():
    """Regression: folding the penalty into trust_score meant a partial-match
    source could never clear the merge prompt's `trust >= 0.7` bar, so its
    single-source topics were silently dropped.
    """
    urls = ["https://x.edu/direct", "https://x.edu/partial"]
    relevance = [
        RelevanceResult(source_url=urls[0], verdict="direct_match", confidence=0.9, reasoning="r"),
        RelevanceResult(source_url=urls[1], verdict="partial_match", confidence=0.9, reasoning="r"),
    ]

    filtered = apply_relevance_filter(_extraction_with(urls), relevance)
    by_url = {str(b.source_url): b for b in filtered.blocks}

    # Trust is untouched for both — the merge must see the true value.
    assert by_url[urls[0]].trust_score == 0.8
    assert by_url[urls[1]].trust_score == 0.8
    # The demotion is recorded separately, for ranking only.
    assert by_url[urls[0]].relevance_penalty == 1.0
    assert by_url[urls[1]].relevance_penalty == 0.7
