"""Cache-layer tests.

Two levels, deliberately:

- Through the orchestrator with `FakeCacheClient`, asserting the *behaviour* that
  matters — a hit must cost zero LLM calls, a stale entry must not.
- Directly against `MongoCacheClient` with a stub collection, asserting the
  document shape and error handling, with no Mongo running.
"""

import json
from datetime import datetime, timedelta, timezone

from syllabus_agent.clients.cache_client import (
    MongoCacheClient,
    is_fresh,
    normalize_subject,
)
from syllabus_agent.pipeline.orchestrator import run_pipeline
from syllabus_agent.schemas.classification import ClassificationResult
from syllabus_agent.schemas.enums import PipelineStage, RouteDecision
from syllabus_agent.schemas.pipeline import PipelineResult


def _cached_result(subject: str = "data structures", *, age_days: float = 0.0) -> PipelineResult:
    """A stored result, optionally aged into the past."""
    return PipelineResult(
        subject=subject,
        route=RouteDecision.GENUINE_ACADEMIC_SUBJECT,
        classification=ClassificationResult(
            subject=subject,
            route=RouteDecision.GENUINE_ACADEMIC_SUBJECT,
            confidence=0.9,
            reasoning="cached",
        ),
        stage_reached=PipelineStage.CLASSIFICATION,
        generated_at=datetime.now(timezone.utc) - timedelta(days=age_days),
    )


async def _run(subject, fake_llm, fake_search, extractors, **kwargs):
    return await run_pipeline(
        subject,
        llm=fake_llm,
        search=fake_search,
        html_extractor=extractors,
        pdf_text_extractor=extractors,
        pdf_ocr_extractor=extractors,
        **kwargs,
    )


# --- key normalisation ------------------------------------------------------


def test_normalize_subject_collapses_case_and_whitespace():
    assert normalize_subject("Data Structures") == "data structures"
    assert normalize_subject("  data structures  ") == "data structures"
    assert normalize_subject("Data   Structures") == "data structures"


def test_differently_cased_subjects_hit_the_same_entry(fake_cache):
    fake_cache.seed("Data Structures", _cached_result())

    assert normalize_subject("data structures") in fake_cache.store


# --- freshness --------------------------------------------------------------


def test_is_fresh_within_ttl():
    assert is_fresh(_cached_result(age_days=29), ttl_days=30)


def test_is_fresh_rejects_entry_past_ttl():
    assert not is_fresh(_cached_result(age_days=31), ttl_days=30)


def test_is_fresh_treats_naive_datetimes_as_utc():
    """Mongo hands back naive datetimes; comparing them must not raise."""
    result = _cached_result()
    result.generated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    assert is_fresh(result, ttl_days=30)


def test_zero_ttl_disables_caching():
    assert not is_fresh(_cached_result(age_days=0), ttl_days=0)


# --- orchestrator integration -----------------------------------------------


async def test_cache_miss_runs_full_pipeline_and_writes_the_result(
    fake_llm, fake_search, fake_html_extractor, fake_cache
):
    result = await _run("data structures", fake_llm, fake_search, fake_html_extractor, cache=fake_cache)

    assert fake_cache.get_calls == ["data structures"]
    assert result.from_cache is False
    assert result.syllabus is not None
    assert len(fake_llm.calls) > 0

    # ...and the fresh result is now cached, under the normalised key.
    assert len(fake_cache.set_calls) == 1
    key, written = fake_cache.set_calls[0]
    assert key == "data structures"
    assert written.syllabus is not None
    # Never stored as a cache hit — that flag describes how a response was served.
    assert fake_cache.store[key].from_cache is False


async def test_cache_hit_skips_the_pipeline_entirely(
    fake_llm, fake_search, fake_html_extractor, fake_cache
):
    fake_cache.seed("data structures", _cached_result())

    result = await _run("data structures", fake_llm, fake_search, fake_html_extractor, cache=fake_cache)

    # The whole point: not one LLM call, not one search call.
    assert fake_llm.calls == []
    assert fake_search.calls == []
    assert result.from_cache is True
    assert result.subject == "data structures"
    # A hit is not re-written.
    assert fake_cache.set_calls == []


async def test_cache_hit_matches_regardless_of_casing(
    fake_llm, fake_search, fake_html_extractor, fake_cache
):
    fake_cache.seed("data structures", _cached_result())

    result = await _run("  Data   Structures ", fake_llm, fake_search, fake_html_extractor, cache=fake_cache)

    assert result.from_cache is True
    assert fake_llm.calls == []


async def test_expired_cache_entry_is_treated_as_a_miss(
    fake_llm, fake_search, fake_html_extractor, fake_cache
):
    fake_cache.seed("data structures", _cached_result(age_days=31))

    result = await _run("data structures", fake_llm, fake_search, fake_html_extractor, cache=fake_cache)

    assert result.from_cache is False
    assert len(fake_llm.calls) > 0  # regenerated
    assert result.syllabus is not None
    # The stale entry is replaced rather than left to expire again.
    assert len(fake_cache.set_calls) == 1
    assert fake_cache.store["data structures"].syllabus is not None


async def test_force_refresh_bypasses_a_valid_cache_hit(
    fake_llm, fake_search, fake_html_extractor, fake_cache
):
    fake_cache.seed("data structures", _cached_result())

    result = await _run(
        "data structures",
        fake_llm,
        fake_search,
        fake_html_extractor,
        cache=fake_cache,
        force_refresh=True,
    )

    assert fake_cache.get_calls == []  # lookup skipped entirely
    assert len(fake_llm.calls) > 0
    assert result.from_cache is False
    # ...but the fresh result still lands in the cache.
    assert len(fake_cache.set_calls) == 1
    assert fake_cache.store["data structures"].syllabus is not None


async def test_classification_only_routes_are_cached_too(
    fake_llm, fake_search, fake_html_extractor, fake_cache
):
    """Probing a rejected subject repeatedly must not keep costing a classifier
    call — that route is exactly the one a demo audience likes to poke at.
    """
    fake_llm.responses.append(
        json.dumps(
            {
                "subject": "taylor swift",
                "route": "rejected_non_academic",
                "confidence": 0.99,
                "reasoning": "Not an academic subject.",
                "clarifying_question": None,
                "suggested_refinements": [],
            }
        )
    )

    first = await _run("taylor swift", fake_llm, fake_search, fake_html_extractor, cache=fake_cache)
    assert first.route == RouteDecision.REJECTED_NON_ACADEMIC
    calls_after_first = len(fake_llm.calls)
    assert calls_after_first == 1

    second = await _run("taylor swift", fake_llm, fake_search, fake_html_extractor, cache=fake_cache)

    assert second.from_cache is True
    assert second.route == RouteDecision.REJECTED_NON_ACADEMIC
    assert len(fake_llm.calls) == calls_after_first  # no further classifier call


async def test_pipeline_runs_normally_without_a_cache(
    fake_llm, fake_search, fake_html_extractor
):
    """Caching is opt-in; omitting the client must change nothing."""
    result = await _run("data structures", fake_llm, fake_search, fake_html_extractor)

    assert result.syllabus is not None
    assert result.from_cache is False


# --- MongoCacheClient against a stub collection -----------------------------


class StubCollection:
    """The three motor calls MongoCacheClient makes, and nothing else."""

    def __init__(self, document: dict | None = None, *, raises: Exception | None = None) -> None:
        self.document = document
        self.raises = raises
        self.replaced: list[dict] = []

    async def find_one(self, query):
        if self.raises:
            raise self.raises
        if self.document is None or self.document["_id"] != query["_id"]:
            return None
        return self.document

    async def replace_one(self, query, document, upsert=False):
        if self.raises:
            raise self.raises
        self.replaced.append(document)
        self.document = document


async def test_mongo_client_round_trips_a_result():
    collection = StubCollection()
    cache = MongoCacheClient(collection=collection, ttl_days=30)

    await cache.set("Data Structures", _cached_result())
    stored = collection.replaced[0]

    assert stored["_id"] == "data structures"
    assert stored["subject"] == "Data Structures"  # original casing kept for readability
    assert stored["result"]["from_cache"] is False

    hit = await cache.get("data structures")
    assert hit is not None
    assert hit.route == RouteDecision.GENUINE_ACADEMIC_SUBJECT


async def test_mongo_client_returns_none_on_expired_document():
    collection = StubCollection()
    cache = MongoCacheClient(collection=collection, ttl_days=30)
    await cache.set("data structures", _cached_result(age_days=31))

    assert await cache.get("data structures") is None


async def test_mongo_client_degrades_to_a_miss_when_the_server_is_down():
    """An unreachable cache must never break a run."""
    cache = MongoCacheClient(collection=StubCollection(raises=RuntimeError("no server")))

    assert await cache.get("data structures") is None
    await cache.set("data structures", _cached_result())  # must not raise


async def test_mongo_client_treats_an_unparseable_document_as_a_miss():
    """A schema change since the entry was written is a miss, not a crash."""
    collection = StubCollection({"_id": "data structures", "result": {"subject": "x"}})
    cache = MongoCacheClient(collection=collection)

    assert await cache.get("data structures") is None
