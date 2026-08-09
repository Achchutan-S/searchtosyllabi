"""Diagnostics tests. All network calls are injected, so nothing here touches a
real provider — the point is that each failure mode maps to the right verdict.
"""

import httpx

from syllabus_agent.config import Settings
from syllabus_agent.diagnostics import (
    check_env,
    check_model_listing,
    check_mongodb,
    check_tavily,
    probe_model,
    run_doctor,
)

_PROBE_URL = "https://example.test/v1beta/openai/chat/completions"


def _settings(**overrides) -> Settings:
    values = {
        "gemini_api_key": "AIzaSyTESTKEY0123456789abcd",
        "gemini_base_url": "https://example.test/v1beta/openai",
        "gemini_model": "gemini-3.5-flash",
        "tavily_api_key": "tvly-dev-TESTKEY0123456789",
    }
    values.update(overrides)
    return Settings(**values)


def _response(status: int, payload=None) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload if payload is not None else {},
        request=httpx.Request("POST", _PROBE_URL),
    )


def _quota_response(quota_id: str, retry_seconds: float) -> httpx.Response:
    return _response(
        429,
        [
            {
                "error": {
                    "code": 429,
                    "message": (
                        "You exceeded your current quota. "
                        f"Please retry in {retry_seconds}s."
                    ),
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                            "violations": [{"quotaId": quota_id, "quotaValue": "20"}],
                        }
                    ],
                }
            }
        ],
    )


def _poster(response: httpx.Response):
    async def post(url, **kwargs):
        return response

    return post


# --- env sanity -------------------------------------------------------------


def test_check_env_passes_with_a_healthy_config():
    results = check_env(_settings())

    assert all(r.status == "pass" for r in results)
    # The key must never be printed in full.
    api_key_line = next(r for r in results if r.label == "GEMINI_API_KEY")
    assert "AIzaSyTESTKEY0123456789abcd" not in api_key_line.detail
    assert api_key_line.detail == "AIza...abcd"


def test_check_env_fails_on_missing_key():
    results = check_env(_settings(tavily_api_key=""))

    tavily = next(r for r in results if r.label == "TAVILY_API_KEY")
    assert tavily.status == "fail"


def test_check_env_fails_on_unreplaced_placeholder():
    results = check_env(_settings(gemini_api_key="your-gemini-api-key"))

    gemini = next(r for r in results if r.label == "GEMINI_API_KEY")
    assert gemini.status == "fail"


def test_check_env_warns_when_base_url_is_not_the_openai_compatible_one():
    """The native /interactions endpoint 404s against /chat/completions."""
    results = check_env(
        _settings(gemini_base_url="https://generativelanguage.googleapis.com/v1beta/interactions")
    )

    base = next(r for r in results if r.label == "GEMINI_BASE_URL")
    assert base.status == "warn"


# --- configured-model probe -------------------------------------------------


async def test_probe_model_passes_on_200():
    result = await probe_model(_settings(), "gemini-3.5-flash", post=_poster(_response(200)))

    assert result.status == "pass"


async def test_probe_model_reports_404_as_not_available_to_this_key():
    result = await probe_model(_settings(), "gemini-2.5-flash", post=_poster(_response(404)))

    assert result.status == "fail"
    assert "not available to this key" in result.detail


async def test_probe_model_distinguishes_per_minute_rate_limit():
    response = _quota_response("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", 17.0)

    result = await probe_model(_settings(), "gemini-3.5-flash", post=_poster(response))

    # Recoverable within the minute, so a warning rather than a hard failure.
    assert result.status == "warn"
    assert "rate-limited" in result.detail
    assert "17s" in result.detail


async def test_probe_model_distinguishes_daily_quota_exhaustion():
    response = _quota_response("GenerateRequestsPerDayPerProjectPerModel-FreeTier", 17.0)

    result = await probe_model(_settings(), "gemini-3.5-flash", post=_poster(response))

    assert result.status == "fail"
    assert "daily quota exhausted" in result.detail
    assert "will not recover today" in result.detail


# --- tavily -----------------------------------------------------------------


async def test_check_tavily_passes_on_200():
    result = await check_tavily(
        _settings(), post=_poster(_response(200, {"results": [{"url": "https://x.edu"}]}))
    )

    assert result.status == "pass"


async def test_check_tavily_fails_on_401():
    result = await check_tavily(_settings(), post=_poster(_response(401)))

    assert result.status == "fail"
    assert "key rejected" in result.detail


# --- mongodb cache ----------------------------------------------------------


async def _ok_ping() -> None:
    return None


async def _dead_ping() -> None:
    raise ConnectionError("connection refused")


async def test_check_mongodb_passes_when_the_ping_succeeds():
    result = await check_mongodb(_settings(), ping=_ok_ping)

    assert result.status == "pass"
    assert "TTL 30d" in result.detail


async def test_check_mongodb_warns_rather_than_fails_when_unreachable():
    """Caching is an optimisation — an unreachable Mongo costs quota, not
    correctness, so it must not turn a healthy config into a FAIL.
    """
    result = await check_mongodb(_settings(), ping=_dead_ping)

    assert result.status == "warn"
    assert "cost full quota" in result.detail


async def test_check_mongodb_masks_credentials_in_an_atlas_uri():
    result = await check_mongodb(
        _settings(mongodb_uri="mongodb+srv://admin:hunter2@cluster0.abcde.mongodb.net"),
        ping=_dead_ping,
    )

    assert "hunter2" not in result.detail
    assert "admin" not in result.detail
    assert "cluster0.abcde.mongodb.net" in result.detail


async def test_unreachable_mongo_does_not_change_the_doctor_exit_code():
    async def get(url, **kwargs):
        return _response(200, {"data": [{"id": "models/gemini-3.5-flash"}]})

    exit_code = await run_doctor(
        _settings(),
        post=_poster(_response(200, {"results": []})),
        get=get,
        ping=_dead_ping,
    )

    assert exit_code == 0


# --- model listing ----------------------------------------------------------


async def test_check_model_listing_flags_configured_model_absent():
    async def get(url, **kwargs):
        return _response(200, {"data": [{"id": "models/gemini-2.0-flash"}]})

    result = await check_model_listing(_settings(), get=get)

    assert result.status == "warn"
    assert "is NOT in the list" in result.detail


# --- exit codes -------------------------------------------------------------


async def test_run_doctor_returns_zero_when_everything_healthy():
    async def get(url, **kwargs):
        return _response(200, {"data": [{"id": "models/gemini-3.5-flash"}]})

    exit_code = await run_doctor(
        _settings(),
        post=_poster(_response(200, {"results": []})),
        get=get,
        ping=_ok_ping,
    )

    assert exit_code == 0


async def test_run_doctor_returns_one_when_configured_model_is_404():
    async def get(url, **kwargs):
        return _response(200, {"data": []})

    exit_code = await run_doctor(
        _settings(), post=_poster(_response(404)), get=get, ping=_ok_ping
    )

    assert exit_code == 1


async def test_run_doctor_returns_one_when_env_is_incomplete():
    async def get(url, **kwargs):
        return _response(200, {"data": []})

    exit_code = await run_doctor(
        _settings(gemini_api_key=""), post=_poster(_response(200)), get=get, ping=_ok_ping
    )

    assert exit_code == 1
