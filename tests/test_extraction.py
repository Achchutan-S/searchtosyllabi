"""Extraction tests.

Parsing is exercised against real fixtures — a real HTML snippet and a real PDF
built in-memory with PyMuPDF — rather than mocking BeautifulSoup/PyMuPDF, so
these assert the actual parsing behaviour. Network access is injected.
"""

import pymupdf
import pytest

from syllabus_agent.clients.extraction_client import (
    HtmlExtractor,
    PdfTextExtractor,
    UnsafeSourceURL,
    assert_safe_url,
    detect_format,
    pdf_needs_ocr,
)
from syllabus_agent.pipeline.extraction.extract import extract_sources
from syllabus_agent.schemas.enums import ExtractionMethod, SourceFormat
from syllabus_agent.schemas.source import CandidateSource

SYLLABUS_HTML = b"""
<html>
  <head><title>CS201</title><style>.x{color:red}</style></head>
  <body>
    <nav>Home | Admissions | Contact</nav>
    <header>Springfield University</header>
    <main>
      <h1>CS201 Data Structures</h1>
      <p>Unit I - Arrays, linked lists and complexity analysis.</p>
    </main>
    <table>
      <tr><th>L</th><th>T</th><th>P</th><th>C</th></tr>
      <tr><td>4</td><td>1</td><td>0</td><td>5</td></tr>
    </table>
    <footer>Copyright 2026</footer>
    <script>console.log('tracking')</script>
  </body>
</html>
"""


def _pdf_bytes(pages: list[str]) -> bytes:
    """Build a real text-layer PDF in memory."""
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def _source(url: str, fmt: SourceFormat, **kwargs) -> CandidateSource:
    return CandidateSource(
        url=url,
        query="data structures syllabus",
        format=fmt,
        trust_score=0.9,
        domain="springfield.edu",
        university="springfield.edu",
        **kwargs,
    )


def _fetcher(payload: bytes, content_type: str | None = None):
    calls: list[str] = []

    async def fetch(url: str):
        calls.append(url)
        return payload, content_type

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


# --- parser-level -----------------------------------------------------------


async def test_html_extractor_strips_chrome_and_keeps_table_cells():
    blocks = await HtmlExtractor().extract(SYLLABUS_HTML)

    joined = "\n".join(blocks)
    assert "Arrays, linked lists" in joined
    # Nav/header/footer/script must not survive.
    assert "Admissions" not in joined
    assert "tracking" not in joined
    assert "Copyright" not in joined
    # The LTPC row must keep its cell boundaries, not smear into "4 1 0 5".
    assert "4 | 1 | 0 | 5" in joined


async def test_pdf_text_extractor_returns_one_block_per_page_with_markers():
    blocks = await PdfTextExtractor().extract(
        _pdf_bytes(["Unit I Introduction to management", "Unit II Organisational behaviour"])
    )

    assert len(blocks) == 2
    assert blocks[0].startswith("[page 1]")
    assert "Introduction to management" in blocks[0]
    assert blocks[1].startswith("[page 2]")


def test_pdf_needs_ocr_detects_empty_and_sparse_text_layers():
    assert pdf_needs_ocr([]) is True
    assert pdf_needs_ocr(["[page 1]\n."]) is True
    # A dense page is real text.
    assert pdf_needs_ocr([f"[page 1]\n{'topic content ' * 40}"]) is False


def test_detect_format_prefers_content_type_over_url_suffix():
    assert detect_format("https://x.edu/plan.aspx", "application/pdf") == SourceFormat.PDF
    assert detect_format("https://x.edu/syllabus.pdf", None) == SourceFormat.PDF
    assert detect_format("https://x.edu/syllabus", "text/html") == SourceFormat.HTML


# --- stage-level ------------------------------------------------------------


async def test_substantial_pre_extracted_content_is_used_and_no_fetch_happens():
    """The fast path exists to skip a redundant fetch when the search provider
    already returned real page text.
    """
    fetch = _fetcher(b"should never be fetched")
    full_page = "Unit I - Arrays and linked lists. Unit II - Trees. " * 60  # ~3000 chars
    source = _source(
        "https://springfield.edu/cs201",
        SourceFormat.HTML,
        pre_extracted_content=full_page,
    )

    result = await extract_sources(
        "data structures",
        [source],
        html_extractor=HtmlExtractor(),
        pdf_text_extractor=PdfTextExtractor(),
        pdf_ocr_extractor=PdfTextExtractor(),
        fetch=fetch,
    )

    assert fetch.calls == []  # type: ignore[attr-defined]
    assert len(result.blocks) == 1
    assert "Arrays and linked lists" in result.blocks[0].text
    assert not result.failures


async def test_snippet_length_pre_extracted_content_is_ignored_and_page_is_fetched():
    """Tavily returns a ~900-char snippet for most results. Accepting it would
    trade the real syllabus body for a search blurb, so it must be refetched.
    """
    fetch = _fetcher(SYLLABUS_HTML, "text/html")
    source = _source(
        "https://springfield.edu/cs201",
        SourceFormat.HTML,
        pre_extracted_content="This course investigates abstract data types ...Read more",
    )

    result = await extract_sources(
        "data structures",
        [source],
        html_extractor=HtmlExtractor(),
        pdf_text_extractor=PdfTextExtractor(),
        pdf_ocr_extractor=PdfTextExtractor(),
        fetch=fetch,
    )

    assert fetch.calls == ["https://springfield.edu/cs201"]  # type: ignore[attr-defined]
    assert "Read more" not in "\n".join(b.text for b in result.blocks)
    assert "4 | 1 | 0 | 5" in "\n".join(b.text for b in result.blocks)


async def test_html_source_without_pre_extracted_content_is_fetched_and_parsed():
    fetch = _fetcher(SYLLABUS_HTML, "text/html")
    source = _source("https://springfield.edu/cs201", SourceFormat.HTML)

    result = await extract_sources(
        "data structures",
        [source],
        html_extractor=HtmlExtractor(),
        pdf_text_extractor=PdfTextExtractor(),
        pdf_ocr_extractor=PdfTextExtractor(),
        fetch=fetch,
    )

    assert fetch.calls == ["https://springfield.edu/cs201"]  # type: ignore[attr-defined]
    assert all(block.method == ExtractionMethod.HTML_PARSER for block in result.blocks)
    assert "Arrays, linked lists" in "\n".join(b.text for b in result.blocks)


async def test_pdf_source_uses_text_layer_when_dense():
    pdf = _pdf_bytes([f"Unit I {'management topic ' * 20}"])
    fetch = _fetcher(pdf, "application/pdf")
    source = _source("https://springfield.edu/syllabus.pdf", SourceFormat.PDF)

    result = await extract_sources(
        "business management",
        [source],
        html_extractor=HtmlExtractor(),
        pdf_text_extractor=PdfTextExtractor(),
        pdf_ocr_extractor=PdfTextExtractor(),
        fetch=fetch,
    )

    assert result.blocks
    assert all(block.method == ExtractionMethod.PDF_TEXT for block in result.blocks)
    assert "management topic" in result.blocks[0].text


async def test_scanned_pdf_falls_back_to_ocr_extractor():
    """An image-only PDF has no text layer, so OCR must take over."""

    class SpyOcr(PdfTextExtractor):
        def __init__(self):
            self.called = False

        async def extract(self, raw_bytes: bytes) -> list[str]:
            self.called = True
            return ["[page 1 ocr]\nUnit I Scanned content recovered by OCR"]

    ocr = SpyOcr()
    blank_pdf = _pdf_bytes([""])  # no text layer at all
    fetch = _fetcher(blank_pdf, "application/pdf")

    result = await extract_sources(
        "business management",
        [_source("https://springfield.edu/scan.pdf", SourceFormat.PDF)],
        html_extractor=HtmlExtractor(),
        pdf_text_extractor=PdfTextExtractor(),
        pdf_ocr_extractor=ocr,
        fetch=fetch,
    )

    assert ocr.called
    assert all(block.method == ExtractionMethod.PDF_OCR for block in result.blocks)
    assert "recovered by OCR" in result.blocks[0].text


async def test_a_failing_source_is_recorded_and_does_not_abort_the_run():
    async def flaky_fetch(url: str):
        if "broken" in url:
            raise RuntimeError("connection reset")
        return SYLLABUS_HTML, "text/html"

    sources = [
        _source("https://springfield.edu/broken", SourceFormat.HTML),
        _source("https://springfield.edu/ok", SourceFormat.HTML),
    ]

    result = await extract_sources(
        "data structures",
        sources,
        html_extractor=HtmlExtractor(),
        pdf_text_extractor=PdfTextExtractor(),
        pdf_ocr_extractor=PdfTextExtractor(),
        fetch=flaky_fetch,
    )

    assert len(result.failures) == 1
    assert "connection reset" in result.failures[0].error
    assert str(result.failures[0].source_url).endswith("/broken")
    # The healthy source still produced text.
    assert result.blocks
    assert all("/ok" in str(block.source_url) for block in result.blocks)


# --- SSRF guard -------------------------------------------------------------


def test_assert_safe_url_rejects_non_http_schemes():
    for bad in ("file:///etc/passwd", "ftp://x.edu/a", "gopher://x.edu"):
        with pytest.raises(UnsafeSourceURL, match="non-http"):
            assert_safe_url(bad)


def test_assert_safe_url_rejects_loopback_and_internal_addresses():
    """Extraction follows URLs from an external search API, so it is an SSRF sink."""
    for bad in (
        "http://127.0.0.1/admin",
        "http://localhost:8000/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.0.0.5/internal",
        "http://192.168.1.1/",
    ):
        with pytest.raises(UnsafeSourceURL):
            assert_safe_url(bad)


def test_assert_safe_url_allows_ordinary_public_https():
    assert_safe_url("https://ocw.mit.edu/courses/6-006/syllabus")
