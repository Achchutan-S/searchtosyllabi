"""Standalone CLI entry point: run the pipeline without the FastAPI server.

Usage:
    python -m syllabus_agent.cli "data structures"
    python -m syllabus_agent.cli "data structures" --verbose
    python -m syllabus_agent.cli doctor
    python -m syllabus_agent.cli doctor --probe-models
    python -m syllabus_agent.cli doctor --probe-models --candidates gemini-3.5-flash,gemini-2.0-flash
"""

import argparse
import asyncio
import sys

from syllabus_agent.clients.extraction_client import (
    HtmlExtractor,
    PdfOcrExtractor,
    PdfTextExtractor,
)
from syllabus_agent.clients.llm_client import OpenAICompatibleLLMClient
from syllabus_agent.clients.search_client import TavilySearchClient
from syllabus_agent.config import get_settings, warn_on_missing_keys
from syllabus_agent.diagnostics import run_doctor_sync
from syllabus_agent.logging_setup import configure_logging, register_secret
from syllabus_agent.pipeline.orchestrator import run_pipeline


DOCTOR_COMMAND = "doctor"


def main() -> None:
    """Dispatch to a subcommand, defaulting to the pipeline run.

    Dispatching on argv[0] rather than using argparse subparsers keeps the
    original `cli.py "<subject>"` form working with no `run` prefix. The only
    cost is that a subject literally named "doctor" is unreachable.
    """
    argv = sys.argv[1:]
    if argv and argv[0] == DOCTOR_COMMAND:
        sys.exit(_doctor_command(argv[1:]))
    _pipeline_command(argv)


def _doctor_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="syllabus_agent.cli doctor",
        description="Diagnose configuration and provider connectivity.",
    )
    parser.add_argument(
        "--probe-models",
        action="store_true",
        help="Also probe candidate models. Each probe is one billable call, so "
        "reach for this only when the configured model is actually failing.",
    )
    parser.add_argument(
        "--candidates",
        default="",
        help="Comma-separated model names to probe instead of the built-in list.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Console DEBUG logging."
    )
    args = parser.parse_args(argv)

    configure_logging(verbose=args.verbose, run_label="doctor")

    settings = get_settings()
    register_secret(settings.llm_api_key)
    register_secret(settings.tavily_api_key)

    candidates = [name.strip() for name in args.candidates.split(",") if name.strip()]

    return run_doctor_sync(
        settings=settings,
        do_probe_models=args.probe_models,
        candidates=candidates or None,
    )


def _pipeline_command(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        description="Run the syllabus-agent search-and-structure pipeline for a subject."
    )
    parser.add_argument("subject", help='Subject to build a syllabus for, e.g. "data structures"')
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Log full request/response bodies to the console (DEBUG). "
        "The JSONL trace file always contains full detail regardless.",
    )
    args = parser.parse_args(argv)

    log_file = configure_logging(verbose=args.verbose)
    print(f"Full call trace: {log_file}", file=sys.stderr)

    result = asyncio.run(_run(args.subject))
    print(result.model_dump_json(indent=2))


async def _run(subject: str):
    settings = get_settings()
    warn_on_missing_keys(settings)
    # Registered so the literal key values are scrubbed from logs even if they
    # leak into an error string.
    register_secret(settings.llm_api_key)
    register_secret(settings.tavily_api_key)

    llm = OpenAICompatibleLLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
    )
    search = TavilySearchClient(api_key=settings.tavily_api_key)

    return await run_pipeline(
        subject,
        llm=llm,
        search=search,
        html_extractor=HtmlExtractor(),
        pdf_text_extractor=PdfTextExtractor(),
        pdf_ocr_extractor=PdfOcrExtractor(),
    )


if __name__ == "__main__":
    main()
