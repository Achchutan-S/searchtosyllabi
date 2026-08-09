"""Stage 4: extraction.

Turns each collected source into raw text blocks. HTML prefers the cleaned text
the search provider already returned (no refetch); otherwise the page is fetched
and parsed with BeautifulSoup. PDFs are fetched and read via PyMuPDF, falling
back to OCR when the text layer looks too sparse to be real.

Per the idea doc (§4.3) this stage only *isolates* clean text per source — it
does not attempt to parse unit structure. That is the structuring LLM's job.
"""

import logging
import time
from typing import Awaitable, Callable

from syllabus_agent.clients.extraction_client import (
    MIN_PRE_EXTRACTED_CHARS,
    BaseExtractor,
    cap_blocks,
    detect_format,
    fetch_source,
    pdf_needs_ocr,
)
from syllabus_agent.logging_setup import record_call, stage_context
from syllabus_agent.schemas.enums import ExtractionMethod, SourceFormat
from syllabus_agent.schemas.extraction import (
    ExtractionFailure,
    ExtractionResult,
    RawTextBlock,
)
from syllabus_agent.schemas.source import CandidateSource

logger = logging.getLogger(__name__)

Fetcher = Callable[[str], Awaitable[tuple[bytes, str | None]]]


async def extract_sources(
    subject: str,
    sources: list[CandidateSource],
    *,
    html_extractor: BaseExtractor,
    pdf_text_extractor: BaseExtractor,
    pdf_ocr_extractor: BaseExtractor,
    fetch: Fetcher | None = None,
) -> ExtractionResult:
    """Entry point for stage 4.

    A source that fails is recorded in `failures` and skipped; it never aborts
    the run. Because structuring ranks by trust *after* extraction, a failed
    top-ranked source is simply absent from the pool, so the next-best source
    takes its slot automatically.
    """
    fetch = fetch or fetch_source

    blocks: list[RawTextBlock] = []
    failures: list[ExtractionFailure] = []
    method_counts: dict[str, int] = {}

    for source in sources:
        url = str(source.url)
        started = time.perf_counter()
        used_pre_extracted = False
        method: ExtractionMethod | None = None

        try:
            with stage_context("extraction"):
                texts, method, used_pre_extracted = await _extract_one(
                    source,
                    fetch=fetch,
                    html_extractor=html_extractor,
                    pdf_text_extractor=pdf_text_extractor,
                    pdf_ocr_extractor=pdf_ocr_extractor,
                )
        except Exception as exc:  # noqa: BLE001 - one bad source must not end the run
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.warning("Extraction failed for %s: %r", url, exc)
            failures.append(
                ExtractionFailure(source_url=source.url, error=repr(exc), method_attempted=method)
            )
            record_call(
                call_type="extraction",
                request={"url": url, "format": source.format.value},
                response={"error": repr(exc)},
                status="parse_error",
                duration_ms=elapsed_ms,
                stage="extraction",
            )
            continue

        elapsed_ms = (time.perf_counter() - started) * 1000
        total_chars = sum(len(text) for text in texts)

        if not texts:
            logger.warning("Extraction produced no text for %s (%s).", url, method.value)
            failures.append(
                ExtractionFailure(
                    source_url=source.url,
                    error="extractor returned no text",
                    method_attempted=method,
                )
            )
        else:
            label = "pre_extracted" if used_pre_extracted else method.value
            method_counts[label] = method_counts.get(label, 0) + 1
            logger.info(
                "extraction: %s via %s — %s block(s), %s chars, %.0fms",
                url,
                label,
                len(texts),
                total_chars,
                elapsed_ms,
            )
            for order_index, text in enumerate(texts):
                blocks.append(
                    RawTextBlock(
                        source_url=source.url,
                        method=method,
                        text=text,
                        order_index=order_index,
                        trust_score=source.trust_score,
                        university=source.university,
                        year=source.year,
                    )
                )

        record_call(
            call_type="extraction",
            request={
                "url": url,
                "format": source.format.value,
                "used_pre_extracted": used_pre_extracted,
            },
            response={
                "method": method.value,
                "block_count": len(texts),
                "total_chars": total_chars,
                "preview": texts[0][:500] if texts else "",
            },
            status="success" if texts else "parse_error",
            duration_ms=elapsed_ms,
            stage="extraction",
        )

    logger.info(
        "extraction: %s/%s sources produced text (%s failed). Methods: %s",
        len(sources) - len(failures),
        len(sources),
        len(failures),
        method_counts or "none",
    )

    return ExtractionResult(subject=subject, blocks=blocks, failures=failures)


async def _extract_one(
    source: CandidateSource,
    *,
    fetch: Fetcher,
    html_extractor: BaseExtractor,
    pdf_text_extractor: BaseExtractor,
    pdf_ocr_extractor: BaseExtractor,
) -> tuple[list[str], ExtractionMethod, bool]:
    """Extract one source. Returns (texts, method, used_pre_extracted)."""
    url = str(source.url)
    fmt = source.format if source.format != SourceFormat.UNKNOWN else detect_format(url, None)

    # Fast path: the search provider already returned cleaned page text, so
    # there is nothing to gain from fetching and re-parsing the HTML — but only
    # when that text is substantial. Tavily returns a ~900-char snippet for most
    # results, and accepting it would discard the actual syllabus body.
    pre_extracted = (source.pre_extracted_content or "").strip()
    if fmt == SourceFormat.HTML and len(pre_extracted) >= MIN_PRE_EXTRACTED_CHARS:
        logger.info(
            "used pre-extracted content for %s, skipped fetch (%s chars)", url, len(pre_extracted)
        )
        return cap_blocks([pre_extracted]), ExtractionMethod.HTML_PARSER, True

    if pre_extracted:
        logger.debug(
            "pre-extracted content for %s is only %s chars (< %s), fetching the real page instead",
            url,
            len(pre_extracted),
            MIN_PRE_EXTRACTED_CHARS,
        )

    raw_bytes, content_type = await fetch(url)

    # The URL alone can lie (a .aspx that serves a PDF), so re-check once the
    # server has told us what it actually sent.
    if content_type:
        fmt = detect_format(url, content_type)

    if fmt == SourceFormat.PDF:
        texts = await pdf_text_extractor.extract(raw_bytes)
        if pdf_needs_ocr(texts):
            logger.info("ocr_fallback: %s had a sparse/absent text layer, running OCR", url)
            texts = await pdf_ocr_extractor.extract(raw_bytes)
            return texts, ExtractionMethod.PDF_OCR, False
        return texts, ExtractionMethod.PDF_TEXT, False

    texts = await html_extractor.extract(raw_bytes)
    return texts, ExtractionMethod.HTML_PARSER, False
