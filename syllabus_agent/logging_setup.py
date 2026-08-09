"""Logging setup and structured per-call tracing.

Two independent channels:

- **Console** — concise one line per external call at INFO (the default), full
  request/response bodies at DEBUG (``--verbose`` or ``LOG_LEVEL=DEBUG``).
- **File** — ``logs/run_<UTC-timestamp>.jsonl``, one JSON line per external call,
  always at full detail regardless of console verbosity.

API keys are redacted from the file and from any DEBUG output, both by key name
(``api_key``, ``Authorization``, ...) and by scrubbing the literal secret values
wherever they appear.
"""

import contextvars
import json
import logging
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

logger = logging.getLogger(__name__)

REDACTED = "***REDACTED***"
DEFAULT_LOG_DIR = Path("logs")

# Key names whose values are secrets, matched case-insensitively as substrings.
_SENSITIVE_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "x-goog-api-key",
    "x-api-key",
    "access_token",
    "secret",
)

CallType = Literal["llm", "search", "extraction"]
CallStatus = Literal["success", "http_error", "parse_error", "rate_limited"]

# Set by each pipeline stage so clients can attribute a call without the stage
# having to be threaded through the LLMClient/SearchClient interfaces.
_current_stage: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_stage", default="unknown"
)

_secret_values: set[str] = set()
_run_file: Path | None = None


@contextmanager
def stage_context(stage: str) -> Iterator[None]:
    """Tag every external call made inside this block with `stage`."""
    token = _current_stage.set(stage)
    try:
        yield
    finally:
        _current_stage.reset(token)


def current_stage() -> str:
    return _current_stage.get()


def register_secret(value: str | None) -> None:
    """Register an API key so it is scrubbed from logs even if it leaks into a
    string (e.g. an error message echoing the request URL).
    """
    if value and len(value.strip()) >= 8:
        _secret_values.add(value.strip())


def _scrub_text(text: str) -> str:
    for secret in _secret_values:
        text = text.replace(secret, REDACTED)
    return text


def mask_secret(value: str | None, keep: int = 4) -> str:
    """Partially mask a secret for human-facing output, e.g. "AIza...9x2Q".

    Lives here alongside `redact()` so all secret handling has a single home.
    `redact()` removes a value entirely (for machine-read logs); this keeps
    enough to confirm *which* key is configured without exposing it.
    """
    if value is None or not value.strip():
        return "(not set)"
    stripped = value.strip()
    if len(stripped) <= keep * 2:
        return REDACTED
    return f"{stripped[:keep]}...{stripped[-keep:]}"


def redact(value: Any) -> Any:
    """Recursively replace secret-bearing values with REDACTED."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS):
                out[key] = REDACTED
            else:
                out[key] = redact(item)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _scrub_text(value)
    return value


def configure_logging(
    verbose: bool = False,
    log_dir: Path | str = DEFAULT_LOG_DIR,
    run_label: str = "run",
) -> Path:
    """Configure console + per-run file logging. Returns the JSONL trace path.

    Console level is DEBUG when `verbose` is set or LOG_LEVEL=DEBUG in the env,
    otherwise INFO. The JSONL file always receives full detail.
    """
    global _run_file

    env_level = os.getenv("LOG_LEVEL", "").strip().upper()
    console_level = logging.DEBUG if (verbose or env_level == "DEBUG") else logging.INFO

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter("%(levelname)-7s %(name)s | %(message)s"))
    root.addHandler(console)

    # httpx logs a line per request that duplicates our own INFO summary; keep it
    # only when the user actually asked for verbose output.
    logging.getLogger("httpx").setLevel(
        logging.INFO if console_level == logging.DEBUG else logging.WARNING
    )
    # httpcore/asyncio emit per-packet TLS and selector chatter at DEBUG that buries
    # the request/response bodies --verbose exists to show. Never useful here.
    for noisy in ("httpcore", "asyncio", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    _run_file = directory / f"{run_label}_{stamp}.jsonl"
    _run_file.touch()

    return _run_file



def record_call(
    *,
    call_type: CallType,
    request: dict,
    response: Any,
    status: CallStatus,
    duration_ms: float,
    attempt: int = 1,
    stage: str | None = None,
) -> None:
    """Append one structured JSON line describing an external call.

    Never raises — a logging failure must not break the pipeline.
    """
    if _run_file is None:
        return

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "call_type": call_type,
        "stage": stage or current_stage(),
        "request": redact(request),
        "response": redact(response),
        "status": status,
        "duration_ms": round(duration_ms, 2),
        "attempt": attempt,
    }

    try:
        line = json.dumps(entry, default=str, ensure_ascii=False)
        # json.dumps leaves U+2028/U+2029 unescaped under ensure_ascii=False, but
        # Python's str.splitlines() (and many JSONL readers) treat them as line
        # breaks — so a single one in scraped page content makes a valid record
        # look like two broken ones. Escape them so any reader sees one line.
        line = line.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
        with _run_file.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception as exc:  # noqa: BLE001 - observability must never break a run
        logger.warning("Could not write call trace: %r", exc)
