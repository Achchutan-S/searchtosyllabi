"""LLM client interface, built against the OpenAI-compatible chat-completions
shape rather than any vendor SDK. The default provider is Gemini, called via its
OpenAI-compatible endpoint — swapping to Groq, OpenRouter, local Ollama, or Claude
later only requires pointing Settings.gemini_base_url/gemini_model/gemini_api_key
at the new provider; no code changes.
"""

import asyncio
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from typing import Literal

import httpx
from pydantic import BaseModel

from syllabus_agent.logging_setup import current_stage, record_call

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3  # initial attempt + 2 retries
BACKOFF_SECONDS = 1.0
DEFAULT_TIMEOUT_SECONDS = 30.0
# Rate-limit waits come from the provider and can be ~60s on free tiers. Cap so a
# hostile/garbled value can't hang the pipeline indefinitely.
MAX_RETRY_AFTER_SECONDS = 90.0

# Matches a whole response wrapped in ```json ... ``` or plain ``` ... ``` fences.
_CODE_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)

# Retrying these is worthwhile — the request itself was well-formed.
_RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMClient(ABC):
    @abstractmethod
    async def chat_completion(
        self,
        messages: list[ChatMessage],
        *,
        response_format: dict | None = None,
        temperature: float = 0.2,
    ) -> str:
        """Send a chat-completions request, return the assistant message content."""
        raise NotImplementedError


class DailyQuotaExhausted(RuntimeError):
    """Raised when the provider's per-day request cap is spent. Distinct from a
    per-minute 429 because no amount of backoff will clear it today.
    """


def quota_ids(response: httpx.Response) -> list[str]:
    try:
        payload = response.json()
    except ValueError:
        return []
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        return []

    return [
        violation.get("quotaId", "")
        for detail in payload.get("error", {}).get("details", [])
        if isinstance(detail, dict)
        for violation in detail.get("violations", [])
        if isinstance(violation, dict)
    ]


def is_daily_quota_exhausted(response: httpx.Response) -> bool:
    """Gemini reports both per-minute and per-day caps as 429, and sends a short
    retryDelay even for the daily one — so retrying a daily exhaustion just
    wastes wall-clock time. Tell them apart via quotaId.
    """
    return any("PerDay" in quota_id for quota_id in quota_ids(response))


def parse_retry_after(response: httpx.Response) -> float | None:
    """Extract how long the provider wants us to wait before retrying.

    Prefers the standard Retry-After header; falls back to the delay Gemini
    embeds in its 429 body ("Please retry in 49.4s").
    """
    header = response.headers.get("Retry-After")
    if header:
        try:
            return min(float(header), MAX_RETRY_AFTER_SECONDS)
        except ValueError:
            pass

    match = re.search(r"retry in ([\d.]+)s", response.text, re.IGNORECASE)
    if match:
        try:
            return min(float(match.group(1)), MAX_RETRY_AFTER_SECONDS)
        except ValueError:
            pass

    return None


def strip_code_fences(content: str) -> str:
    """Unwrap a ```json ... ``` fenced response. Models add these even when told
    not to, and the fence breaks json.loads() at the call site.
    """
    match = _CODE_FENCE_RE.match(content)
    return match.group(1) if match else content.strip()


class OpenAICompatibleLLMClient(LLMClient):
    """Concrete client for any OpenAI-compatible /chat/completions endpoint."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        # rstrip so a trailing slash in .env doesn't produce a //chat/completions URL.
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    async def chat_completion(
        self,
        messages: list[ChatMessage],
        *,
        response_format: dict | None = None,
        temperature: float = 0.2,
    ) -> str:
        url = f"{self.base_url}/chat/completions"
        body: dict = {
            "model": self.model,
            "messages": [m.model_dump() for m in messages],
            "temperature": temperature,
        }
        if response_format is not None:
            body["response_format"] = response_format

        last_error: Exception | None = None
        retry_after: float | None = None
        prompt_chars = sum(len(m.content) for m in messages)
        request_record = {"url": url, "headers": {"Authorization": "Bearer <key>"}, "body": body}

        for attempt in range(1, MAX_ATTEMPTS + 1):
            stage = current_stage()
            logger.debug(
                "[%s] LLM request (attempt %s/%s) model=%s temperature=%s messages=%s",
                stage,
                attempt,
                MAX_ATTEMPTS,
                self.model,
                temperature,
                json.dumps([m.model_dump() for m in messages], indent=2, ensure_ascii=False),
            )
            started = time.perf_counter()

            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        url,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=body,
                    )
                    response.raise_for_status()
                    payload = response.json()

                content = payload["choices"][0]["message"]["content"]
                cleaned = strip_code_fences(content)
                elapsed_ms = (time.perf_counter() - started) * 1000

                logger.debug("[%s] LLM raw response body: %s", stage, content)
                if cleaned != content:
                    logger.debug("[%s] LLM response after fence-stripping: %s", stage, cleaned)

                if response_format is not None:
                    # Validate here so malformed JSON is retried rather than
                    # exploding in the calling stage.
                    parsed = json.loads(cleaned)
                    logger.debug(
                        "[%s] LLM parsed JSON: %s",
                        stage,
                        json.dumps(parsed, indent=2, ensure_ascii=False),
                    )

                logger.info(
                    "[%s] llm ok model=%s prompt=%schars status=200 %.0fms attempt=%s",
                    stage,
                    self.model,
                    prompt_chars,
                    elapsed_ms,
                    attempt,
                )
                record_call(
                    call_type="llm",
                    request=request_record,
                    response={
                        "http_status": 200,
                        "raw": content,
                        "cleaned": cleaned,
                        "usage": payload.get("usage"),
                    },
                    status="success",
                    duration_ms=elapsed_ms,
                    attempt=attempt,
                )
                return cleaned

            except httpx.HTTPStatusError as exc:
                elapsed_ms = (time.perf_counter() - started) * 1000
                status = exc.response.status_code
                logger.error(
                    "LLM call to %s failed with HTTP %s: %s",
                    url,
                    status,
                    exc.response.text[:1000],
                )
                record_call(
                    call_type="llm",
                    request=request_record,
                    response={"http_status": status, "body": exc.response.text},
                    status="rate_limited" if status == 429 else "http_error",
                    duration_ms=elapsed_ms,
                    attempt=attempt,
                )
                if status not in _RETRYABLE_STATUS_CODES:
                    # A 401/403/400 will fail identically on retry — surface it now.
                    raise
                if status == 429 and is_daily_quota_exhausted(exc.response):
                    raise DailyQuotaExhausted(
                        f"Daily free-tier request quota is exhausted for model "
                        f"{self.model!r}. Retrying will not help until the quota "
                        f"resets. Switch GEMINI_MODEL to a model with a larger free "
                        f"tier, or switch provider via GEMINI_BASE_URL."
                    ) from exc
                # Rate limiters tell you how long to wait; a fixed 1s backoff would
                # burn every remaining attempt well before the window reopens.
                retry_after = parse_retry_after(exc.response)
                last_error = exc

            except (httpx.TimeoutException, httpx.TransportError) as exc:
                elapsed_ms = (time.perf_counter() - started) * 1000
                logger.error("LLM call to %s failed at transport level: %r", url, exc)
                record_call(
                    call_type="llm",
                    request=request_record,
                    response={"error": repr(exc)},
                    status="http_error",
                    duration_ms=elapsed_ms,
                    attempt=attempt,
                )
                last_error = exc

            except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                elapsed_ms = (time.perf_counter() - started) * 1000
                logger.error(
                    "LLM call to %s returned an unusable body (attempt %s/%s): %r",
                    url,
                    attempt,
                    MAX_ATTEMPTS,
                    exc,
                )
                # The body that failed to parse is the whole point of this trace —
                # without it you cannot tell a truncation from a schema mismatch.
                unparsed = locals().get("cleaned", locals().get("content"))
                logger.debug("[%s] LLM unparseable body was: %s", stage, unparsed)
                record_call(
                    call_type="llm",
                    request=request_record,
                    response={"error": repr(exc), "raw": unparsed},
                    status="parse_error",
                    duration_ms=elapsed_ms,
                    attempt=attempt,
                )
                last_error = exc

            if attempt < MAX_ATTEMPTS:
                delay = retry_after if retry_after is not None else BACKOFF_SECONDS * attempt
                logger.warning(
                    "Retrying LLM call in %.1fs (attempt %s/%s, retry_after_parsed=%s).",
                    delay,
                    attempt + 1,
                    MAX_ATTEMPTS,
                    retry_after,
                )
                await asyncio.sleep(delay)
                retry_after = None

        logger.error("LLM call to %s exhausted %s attempts.", url, MAX_ATTEMPTS)
        assert last_error is not None
        raise last_error
