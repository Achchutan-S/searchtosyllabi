"""Pre-flight diagnostics for the `doctor` CLI subcommand.

Answers "why is the pipeline failing?" without spending a full pipeline run:
is the env configured, is the base URL the OpenAI-compatible one, is the
configured model actually callable by this key, and is the search key alive.

Every network check here is deliberately a *single* attempt — the retry logic in
`OpenAICompatibleLLMClient` is right for the pipeline but wrong for a
diagnostic, where three attempts against a per-minute 429 would mean three
billable calls and a two-minute wait for information we already have after one.

Quota parsing is reused from `clients.llm_client` rather than reimplemented.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

import httpx

from syllabus_agent.clients.llm_client import (
    is_daily_quota_exhausted,
    parse_retry_after,
    quota_ids,
)
from syllabus_agent.config import Settings, get_settings
from syllabus_agent.logging_setup import mask_secret

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 30.0

# Known good as of the last session (2026-08-07). Provider availability drifts —
# models get retired to new users without warning (gemini-2.5-flash did exactly
# that), so treat this as a starting point, not a guarantee. Override with
# --candidates.
DEFAULT_CANDIDATE_MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite"]

Status = Literal["pass", "fail", "warn"]

_SYMBOLS: dict[Status, str] = {"pass": "✓", "fail": "✗", "warn": "⚠"}

Poster = Callable[..., Awaitable[httpx.Response]]
Getter = Callable[..., Awaitable[httpx.Response]]


@dataclass
class CheckResult:
    status: Status
    label: str
    detail: str = ""

    def render(self, width: int = 22) -> str:
        line = f"  {_SYMBOLS[self.status]} {self.label.ljust(width)} {self.detail}".rstrip()
        return line


async def _default_post(url: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
        return await client.post(url, **kwargs)


async def _default_get(url: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
        return await client.get(url, **kwargs)


def _is_placeholder(value: str) -> bool:
    return not value.strip() or value.strip().startswith("your-")


# --- a) env sanity ----------------------------------------------------------


def check_env(settings: Settings) -> list[CheckResult]:
    results = [
        CheckResult(
            "fail" if _is_placeholder(settings.gemini_api_key) else "pass",
            "GEMINI_API_KEY",
            mask_secret(settings.gemini_api_key)
            if not _is_placeholder(settings.gemini_api_key)
            else "missing or still the .env.example placeholder",
        ),
        CheckResult(
            "fail" if _is_placeholder(settings.tavily_api_key) else "pass",
            "TAVILY_API_KEY",
            mask_secret(settings.tavily_api_key)
            if not _is_placeholder(settings.tavily_api_key)
            else "missing or still the .env.example placeholder",
        ),
        CheckResult(
            "fail" if not settings.gemini_model.strip() else "pass",
            "GEMINI_MODEL",
            settings.gemini_model or "not set",
        ),
    ]

    base_url = settings.gemini_base_url.strip()
    if not base_url:
        results.append(CheckResult("fail", "GEMINI_BASE_URL", "not set"))
    elif "/openai" not in base_url:
        # The native /v1beta/interactions endpoint is a different API shape and
        # will 404 against /chat/completions — a real mistake made before.
        results.append(
            CheckResult(
                "warn",
                "GEMINI_BASE_URL",
                f"{base_url} — expected an OpenAI-compatible path ending in /openai",
            )
        )
    else:
        results.append(CheckResult("pass", "GEMINI_BASE_URL", base_url))

    return results


# --- b) model listing -------------------------------------------------------


async def check_model_listing(settings: Settings, *, get: Getter | None = None) -> CheckResult:
    get = get or _default_get
    url = f"{settings.gemini_base_url.rstrip('/')}/models"

    try:
        response = await get(url, headers={"Authorization": f"Bearer {settings.gemini_api_key}"})
    except httpx.HTTPError as exc:
        return CheckResult("warn", "Model listing", f"could not reach {url}: {exc!r}")

    if response.status_code != 200:
        return CheckResult("warn", "Model listing", f"HTTP {response.status_code}")

    try:
        models = [entry["id"].replace("models/", "") for entry in response.json()["data"]]
    except (ValueError, KeyError, TypeError) as exc:
        return CheckResult("warn", "Model listing", f"unexpected body: {exc!r}")

    configured = settings.gemini_model.strip()
    present = configured in models
    return CheckResult(
        "pass" if present else "warn",
        "Model listing",
        f"{len(models)} models listed; configured {configured!r} "
        f"{'is present' if present else 'is NOT in the list'}",
    )


# --- c) configured-model probe / e) candidate probes ------------------------


def _classify_probe_response(model: str, response: httpx.Response) -> CheckResult:
    """Turn a probe response into a diagnosis. 429s are ambiguous until you read
    the quotaId — per-minute clears in seconds, per-day does not clear today.
    """
    if response.status_code == 200:
        return CheckResult("pass", model, "200 OK — callable")

    if response.status_code == 404:
        return CheckResult(
            "fail", model, "404 — model not available to this key (listed != callable)"
        )

    if response.status_code == 401 or response.status_code == 403:
        return CheckResult("fail", model, f"HTTP {response.status_code} — key rejected")

    if response.status_code == 429:
        if is_daily_quota_exhausted(response):
            return CheckResult(
                "fail",
                model,
                "429 daily quota exhausted — will not recover today; "
                "switch models or wait for reset",
            )
        delay = parse_retry_after(response)
        ids = ", ".join(q for q in quota_ids(response) if q) or "unspecified quota"
        suffix = f", resets in ~{delay:.0f}s" if delay is not None else ""
        return CheckResult("warn", model, f"429 rate-limited ({ids}){suffix}")

    return CheckResult("fail", model, f"HTTP {response.status_code}")


async def probe_model(
    settings: Settings, model: str, *, post: Poster | None = None
) -> CheckResult:
    """One minimal chat_completion call against `model`. Costs one request."""
    post = post or _default_post
    url = f"{settings.gemini_base_url.rstrip('/')}/chat/completions"

    try:
        response = await post(
            url,
            headers={"Authorization": f"Bearer {settings.gemini_api_key}"},
            json={"model": model, "messages": [{"role": "user", "content": "say ok"}]},
        )
    except httpx.HTTPError as exc:
        return CheckResult("fail", model, f"transport error: {exc!r}")

    return _classify_probe_response(model, response)


async def probe_models(
    settings: Settings, candidates: list[str], *, post: Poster | None = None
) -> list[CheckResult]:
    return [await probe_model(settings, model, post=post) for model in candidates]


# --- d) tavily --------------------------------------------------------------


async def check_tavily(settings: Settings, *, post: Poster | None = None) -> CheckResult:
    post = post or _default_post

    try:
        response = await post(
            "https://api.tavily.com/search",
            json={
                "api_key": settings.tavily_api_key,
                "query": "test",
                "search_depth": "basic",
                "max_results": 1,
            },
        )
    except httpx.HTTPError as exc:
        return CheckResult("fail", "Tavily search", f"transport error: {exc!r}")

    if response.status_code == 200:
        try:
            count = len(response.json().get("results", []))
        except ValueError:
            count = 0
        return CheckResult("pass", "Tavily search", f"200 OK — {count} result(s)")

    if response.status_code in (401, 403):
        return CheckResult("fail", "Tavily search", f"HTTP {response.status_code} — key rejected")

    return CheckResult("fail", "Tavily search", f"HTTP {response.status_code}")


# --- orchestration ----------------------------------------------------------


async def run_doctor(
    settings: Settings | None = None,
    *,
    do_probe_models: bool = False,
    candidates: list[str] | None = None,
    post: Poster | None = None,
    get: Getter | None = None,
) -> int:
    """Run all checks, print a report, return a process exit code.

    Exit code is 0 only when env sanity, the configured-model probe, and the
    Tavily check all pass. Model listing is informational and never decides the
    exit code, because a model can be absent from the list and still work.
    """
    settings = settings or get_settings()

    print("syllabus-agent doctor")
    print("=" * 60)

    print("\nEnvironment")
    env_results = check_env(settings)
    for result in env_results:
        print(result.render())
    env_ok = all(r.status != "fail" for r in env_results)

    print("\nModel listing")
    listing = await check_model_listing(settings, get=get)
    print(listing.render())
    print("    note: a listed model can still 404 on use — listing is not proof it")
    print("    is callable by this key (gemini-2.5-flash behaved exactly this way).")

    print(f"\nConfigured model probe ({settings.gemini_model})")
    configured = await probe_model(settings, settings.gemini_model, post=post)
    print(configured.render(width=30))

    print("\nSearch provider")
    tavily = await check_tavily(settings, post=post)
    print(tavily.render())

    probe_results: list[CheckResult] = []
    if do_probe_models:
        chosen = candidates or DEFAULT_CANDIDATE_MODELS
        print(f"\nCandidate model probes ({len(chosen)} calls)")
        probe_results = await probe_models(settings, chosen, post=post)
        width = max((len(r.label) for r in probe_results), default=20) + 2
        for result in probe_results:
            print(result.render(width=width))

    ok = env_ok and configured.status == "pass" and tavily.status == "pass"

    print("\n" + "=" * 60)
    print("PASS — configuration looks healthy." if ok else "FAIL — see above.")

    if configured.status == "fail" and not do_probe_models:
        print(
            "\nSuggestion: run `python -m syllabus_agent.cli doctor --probe-models` to "
            "find a working alternative, then update GEMINI_MODEL in .env."
        )
    elif probe_results:
        working = [r.label for r in probe_results if r.status == "pass"]
        if working:
            print(f"\nSuggestion: set GEMINI_MODEL in .env to one of: {', '.join(working)}")
        else:
            print("\nNo candidate model responded OK. Try --candidates with other model names.")

    return 0 if ok else 1


def run_doctor_sync(**kwargs) -> int:
    return asyncio.run(run_doctor(**kwargs))
