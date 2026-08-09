"""Search client interface. Tavily is the concrete implementation for now, kept
behind the same abstract interface so any other search API (Bing, SerpAPI, ...)
is a drop-in replacement.
"""

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod

import httpx
from pydantic import BaseModel

from syllabus_agent.logging_setup import current_stage, record_call

logger = logging.getLogger(__name__)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"

MAX_ATTEMPTS = 3  # initial attempt + 2 retries
BACKOFF_SECONDS = 1.0
DEFAULT_TIMEOUT_SECONDS = 30.0

_RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


class SearchHit(BaseModel):
    url: str
    title: str
    snippet: str
    pre_extracted_content: str | None = None
    """Cleaned page text the search provider already extracted, when it offers
    one. Carried through to CandidateSource so the extraction stage can later
    skip re-fetching and re-parsing the page. Not consumed yet.
    """


class SearchClient(ABC):
    @abstractmethod
    async def search(self, query: str, max_results: int = 10) -> list[SearchHit]:
        raise NotImplementedError


class TavilySearchClient(SearchClient):
    """Concrete Tavily implementation.

    Uses search_depth="basic" deliberately — "advanced" costs multiple free-tier
    credits per call, and this pipeline issues one call per generated query.
    """

    def __init__(self, api_key: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.api_key = api_key
        self.timeout = timeout

    async def search(self, query: str, max_results: int = 10) -> list[SearchHit]:
        body = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": "basic",
            "include_domains": [],
            "max_results": max_results,
        }

        last_error: Exception | None = None
        request_record = {"url": TAVILY_SEARCH_URL, "body": body}

        for attempt in range(1, MAX_ATTEMPTS + 1):
            stage = current_stage()
            logger.debug(
                "[%s] Tavily request (attempt %s/%s): query=%r search_depth=%s max_results=%s",
                stage,
                attempt,
                MAX_ATTEMPTS,
                query,
                body["search_depth"],
                max_results,
            )
            started = time.perf_counter()

            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(TAVILY_SEARCH_URL, json=body)
                    response.raise_for_status()
                    payload = response.json()

                results = payload["results"]
                elapsed_ms = (time.perf_counter() - started) * 1000

                logger.debug(
                    "[%s] Tavily raw results (%s, pre-filtering): %s",
                    stage,
                    len(results),
                    json.dumps(results, indent=2, ensure_ascii=False),
                )
                logger.info(
                    "[%s] search ok query=%r results=%s %.0fms attempt=%s",
                    stage,
                    query,
                    len(results),
                    elapsed_ms,
                    attempt,
                )
                record_call(
                    call_type="search",
                    request=request_record,
                    response={"http_status": 200, "result_count": len(results), "results": results},
                    status="success",
                    duration_ms=elapsed_ms,
                    attempt=attempt,
                )
                return [_to_hit(result) for result in results]

            except httpx.HTTPStatusError as exc:
                elapsed_ms = (time.perf_counter() - started) * 1000
                status = exc.response.status_code
                logger.error(
                    "Tavily search for %r failed with HTTP %s: %s",
                    query,
                    status,
                    exc.response.text[:1000],
                )
                record_call(
                    call_type="search",
                    request=request_record,
                    response={"http_status": status, "body": exc.response.text},
                    status="rate_limited" if status == 429 else "http_error",
                    duration_ms=elapsed_ms,
                    attempt=attempt,
                )
                if status not in _RETRYABLE_STATUS_CODES:
                    raise
                last_error = exc

            except (httpx.TimeoutException, httpx.TransportError) as exc:
                elapsed_ms = (time.perf_counter() - started) * 1000
                logger.error("Tavily search for %r failed at transport level: %r", query, exc)
                record_call(
                    call_type="search",
                    request=request_record,
                    response={"error": repr(exc)},
                    status="http_error",
                    duration_ms=elapsed_ms,
                    attempt=attempt,
                )
                last_error = exc

            except (ValueError, KeyError, TypeError) as exc:
                elapsed_ms = (time.perf_counter() - started) * 1000
                logger.error(
                    "Tavily search for %r returned an unusable body (attempt %s/%s): %r",
                    query,
                    attempt,
                    MAX_ATTEMPTS,
                    exc,
                )
                record_call(
                    call_type="search",
                    request=request_record,
                    response={"error": repr(exc)},
                    status="parse_error",
                    duration_ms=elapsed_ms,
                    attempt=attempt,
                )
                last_error = exc

            if attempt < MAX_ATTEMPTS:
                logger.warning(
                    "Retrying Tavily search in %.1fs (attempt %s/%s).",
                    BACKOFF_SECONDS * attempt,
                    attempt + 1,
                    MAX_ATTEMPTS,
                )
                await asyncio.sleep(BACKOFF_SECONDS * attempt)

        logger.error("Tavily search for %r exhausted %s attempts.", query, MAX_ATTEMPTS)
        assert last_error is not None
        raise last_error


def _to_hit(result: dict) -> SearchHit:
    content = result.get("content") or None
    return SearchHit(
        url=result["url"],
        title=result.get("title") or "",
        snippet=content or "",
        pre_extracted_content=content,
    )
