"""Result-cache interface, kept behind an ABC like the LLM and search clients so
it is swappable (Redis, a dict, a file) and mockable in tests.

Scope, deliberately: this caches whole `PipelineResult` envelopes keyed by
normalised subject, so re-running an already-generated subject costs zero LLM
calls instead of ~40. It is a quota shield for repeated demo runs, not the
Phase-2 storage layer — there is no versioning, no partial invalidation, and no
history. Latest write wins; freshness is a single TTL.

An unreachable Mongo must never break a run: every failure here degrades to
"cache miss" and the pipeline proceeds at full cost.
"""

import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import ValidationError

from syllabus_agent.logging_setup import record_call
from syllabus_agent.schemas.pipeline import PipelineResult

logger = logging.getLogger(__name__)

DEFAULT_DB_NAME = "syllabus_agent"
COLLECTION_NAME = "syllabus_cache"

# Keep a dead/wrong URI cheap to discover. Mongo's own default is 30s, which
# would add half a minute to every run when the cache is simply not running.
SERVER_SELECTION_TIMEOUT_MS = 3000


def normalize_subject(subject: str) -> str:
    """Cache key for a subject.

    Lowercased and whitespace-collapsed, so "Data Structures", "data structures"
    and "  data   structures " are one entry rather than three. Nothing cleverer
    (no stemming, no synonyms) — a wrong collision here would serve the wrong
    syllabus, and the miss it saves only costs one regeneration.
    """
    return " ".join(subject.lower().split())


def is_fresh(result: PipelineResult, ttl_days: int, *, now: datetime | None = None) -> bool:
    """Whether a cached result is still within its TTL.

    Shared by every CacheClient implementation (including the test fake) so the
    freshness policy has exactly one definition. A non-positive TTL disables
    caching entirely — every entry reads as stale.
    """
    if ttl_days <= 0:
        return False

    now = now or datetime.now(timezone.utc)
    generated_at = result.generated_at
    if generated_at.tzinfo is None:
        # Mongo returns naive UTC datetimes; treat them as the UTC they are
        # rather than crashing on an aware/naive comparison.
        generated_at = generated_at.replace(tzinfo=timezone.utc)

    return now - generated_at < timedelta(days=ttl_days)


class CacheClient(ABC):
    @abstractmethod
    async def get(self, subject: str) -> PipelineResult | None:
        """Return the cached result for `subject`, or None on a miss.

        An entry older than the configured TTL is a miss, not a hit.
        """
        raise NotImplementedError

    @abstractmethod
    async def set(self, subject: str, response: PipelineResult) -> None:
        """Store `response` under `subject`, replacing any existing entry."""
        raise NotImplementedError


class MongoCacheClient(CacheClient):
    """MongoDB-backed cache, one document per normalised subject.

    Document shape:
        _id            normalised subject (the cache key)
        subject        the subject as originally typed, for readability
        generated_at   copied out of the result so the TTL is queryable in Mongo
        cached_at      when this document was written
        result         the full PipelineResult, JSON-mode dumped

    The whole envelope is stored regardless of route, so repeatedly probing a
    `needs_clarification` or `rejected_non_academic` subject doesn't burn a
    classifier call either.
    """

    def __init__(
        self,
        uri: str = "mongodb://localhost:27017",
        *,
        db_name: str = DEFAULT_DB_NAME,
        ttl_days: int = 30,
        collection: Any | None = None,
    ) -> None:
        self.uri = uri
        self.db_name = db_name
        self.ttl_days = ttl_days
        # Injectable so tests exercise the real document shape and TTL handling
        # against a stub collection, with no Mongo running.
        self._collection = collection

    @property
    def collection(self) -> Any:
        if self._collection is None:
            # Imported lazily: the ABC above must stay importable in an
            # environment without motor installed (and connecting is not free).
            from motor.motor_asyncio import AsyncIOMotorClient

            client = AsyncIOMotorClient(
                self.uri, serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS
            )
            self._collection = client[self.db_name][COLLECTION_NAME]
        return self._collection

    async def get(self, subject: str) -> PipelineResult | None:
        key = normalize_subject(subject)
        started = time.perf_counter()

        try:
            document = await self.collection.find_one({"_id": key})
        except Exception as exc:  # noqa: BLE001 - a dead cache must not fail a run
            logger.warning(
                "Cache lookup for %r failed (%r) — proceeding as a cache miss.", key, exc
            )
            self._record("get", key, {"error": repr(exc)}, started)
            return None

        if document is None:
            logger.info("cache miss for %r — running the full pipeline.", key)
            self._record("get", key, {"hit": False}, started)
            return None

        try:
            result = PipelineResult.model_validate(document["result"])
        except (KeyError, ValidationError) as exc:
            # A schema change since the entry was written. Treat as a miss; the
            # fresh run will overwrite it.
            logger.warning(
                "Cached entry for %r no longer matches PipelineResult (%r) — "
                "treating as a miss.",
                key,
                exc,
            )
            self._record("get", key, {"hit": False, "error": repr(exc)}, started)
            return None

        if not is_fresh(result, self.ttl_days):
            logger.info(
                "cache entry for %r is older than the %s-day TTL (generated_at=%s) — "
                "treating as a miss.",
                key,
                self.ttl_days,
                result.generated_at.isoformat(),
            )
            self._record(
                "get",
                key,
                {"hit": False, "expired": True, "generated_at": result.generated_at},
                started,
            )
            return None

        logger.info(
            "cache hit for %r (generated_at=%s) — returning stored result, 0 LLM calls.",
            key,
            result.generated_at.isoformat(),
        )
        self._record("get", key, {"hit": True, "generated_at": result.generated_at}, started)
        return result

    async def set(self, subject: str, response: PipelineResult) -> None:
        key = normalize_subject(subject)
        started = time.perf_counter()

        # from_cache describes how *this* response was served, not the stored
        # entry — persisting True would make a fresh run look like a cache hit.
        document = {
            "_id": key,
            "subject": subject,
            "generated_at": response.generated_at,
            "cached_at": datetime.now(timezone.utc),
            "result": response.model_copy(update={"from_cache": False}).model_dump(mode="json"),
        }

        try:
            await self.collection.replace_one({"_id": key}, document, upsert=True)
        except Exception as exc:  # noqa: BLE001 - a dead cache must not fail a run
            logger.warning("Could not write cache entry for %r: %r", key, exc)
            self._record("set", key, {"error": repr(exc)}, started)
            return

        logger.info("cached result for %r (route=%s).", key, response.route.value)
        self._record("set", key, {"written": True}, started)

    async def ping(self) -> None:
        """Raise if the server is unreachable. Used by `doctor`."""
        await self.collection.database.client.admin.command("ping")

    def _record(self, operation: str, key: str, response: dict, started: float) -> None:
        record_call(
            call_type="cache",
            request={"operation": operation, "key": key, "collection": COLLECTION_NAME},
            response=response,
            status="success" if "error" not in response else "http_error",
            duration_ms=(time.perf_counter() - started) * 1000,
        )
