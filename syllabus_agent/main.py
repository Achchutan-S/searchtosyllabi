"""FastAPI app exposing the search-and-structure pipeline as a single endpoint."""

import logging
import sys

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from syllabus_agent.clients.extraction_client import (
    HtmlExtractor,
    PdfOcrExtractor,
    PdfTextExtractor,
)
from syllabus_agent.clients.llm_client import OpenAICompatibleLLMClient
from syllabus_agent.clients.search_client import TavilySearchClient
from syllabus_agent.config import get_settings, warn_on_missing_keys
from syllabus_agent.logging_setup import configure_logging, register_secret
from syllabus_agent.pipeline.orchestrator import run_pipeline
from syllabus_agent.schemas.pipeline import PipelineResult

logger = logging.getLogger(__name__)

app = FastAPI(
    title="syllabus-agent",
    description="Layer one: search-and-structure pipeline for canonical syllabi.",
)


class SyllabusRequest(BaseModel):
    subject: str


@app.on_event("startup")
async def configure_run_logging() -> None:
    """One JSONL trace per server run, not per request.

    Per-request files would multiply without bound under any real traffic, and
    each line already carries a timestamp and stage, so a single append-only
    file stays greppable. Set LOG_LEVEL=DEBUG for full bodies on the console.
    """
    settings = get_settings()
    log_file = configure_logging(log_dir="logs", run_label="server")
    register_secret(settings.llm_api_key)
    register_secret(settings.tavily_api_key)
    warn_on_missing_keys(settings)
    print(f"Full call trace: {log_file}", file=sys.stderr)


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Return a generic message to the caller; keep the traceback server-side.

    Pipeline errors can carry provider responses and request context, so the
    exception text is logged rather than serialised into the HTTP response.
    """
    logger.exception("Unhandled error serving %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. See server logs for details."},
    )


@app.post("/syllabus", response_model=PipelineResult)
async def build_syllabus(request: SyllabusRequest) -> PipelineResult:
    settings = get_settings()
    llm = OpenAICompatibleLLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
    )
    search = TavilySearchClient(api_key=settings.tavily_api_key)

    return await run_pipeline(
        request.subject,
        llm=llm,
        search=search,
        html_extractor=HtmlExtractor(),
        pdf_text_extractor=PdfTextExtractor(),
        pdf_ocr_extractor=PdfOcrExtractor(),
    )
