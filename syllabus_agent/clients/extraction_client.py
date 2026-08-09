"""Extraction clients: one per source format, each a thin wrapper around a real
extraction library (BeautifulSoup, PyMuPDF, pytesseract) so the extraction
pipeline stage stays swappable/mockable, matching the LLM and search clients.

The extractors are **pure parsers** (`bytes -> list[str]`); fetching lives in
`fetch_source()` so parsing can be tested without a network and the pipeline
stage keeps its existing injected-extractor signature.

Per the design note in the idea doc (§4.3), an extractor's only job is to
isolate clean raw text per source. It deliberately does *not* try to detect unit
structure — absorbing inconsistent unit numbering and run-together topic prose
is the structuring LLM's job.
"""

import asyncio
import io
import ipaddress
import logging
import re
import socket
from abc import ABC, abstractmethod
from urllib.parse import urlparse

import httpx
import pymupdf
import pytesseract
from bs4 import BeautifulSoup
from PIL import Image

from syllabus_agent.schemas.enums import SourceFormat

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 30.0
MAX_ATTEMPTS = 3
MAX_REDIRECTS = 5
BACKOFF_SECONDS = 1.0
_RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}

# A course catalog can run to hundreds of pages; we only need the syllabus part.
MAX_PDF_PAGES = 30
# OCR is far more expensive than text extraction (render + Tesseract per page),
# so it gets a tighter cap of its own.
MAX_OCR_PAGES = 10
OCR_DPI = 200

# Below this many characters per page, a "text" PDF is almost certainly scanned.
MIN_CHARS_PER_PAGE = 100

# Guard rails so one enormous page can't blow up the structuring prompt (and its
# token cost) later in the pipeline.
MAX_BLOCK_CHARS = 20_000
MAX_TOTAL_CHARS_PER_SOURCE = 40_000

# Tavily's `content` is a search-result *snippet*, not full page text — measured
# at a ~918-char median and ~1500-char ceiling across a real run, versus ~37k for
# a fetched PDF. Below this threshold the "saved" fetch costs us the entire
# syllabus body, so only treat pre-extracted text as usable when it is
# substantial enough to plausibly be a real page.
MIN_PRE_EXTRACTED_CHARS = 2_000

_WHITESPACE_RE = re.compile(r"\s+")

# Chrome/Firefox blocks are common on university sites for unknown agents.
_USER_AGENT = "Mozilla/5.0 (compatible; syllabus-agent/1.0; +https://example.invalid/bot)"

_STRIP_TAGS = [
    "script",
    "style",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
    "noscript",
    "iframe",
    "svg",
    "button",
]


class BaseExtractor(ABC):
    @abstractmethod
    async def extract(self, raw_bytes: bytes) -> list[str]:
        """Return a list of raw text blocks, in document order."""
        raise NotImplementedError


def _normalise(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def cap_blocks(blocks: list[str]) -> list[str]:
    """Truncate per block and in total, so a pathological page can't dominate."""
    capped: list[str] = []
    running = 0

    for block in blocks:
        if running >= MAX_TOTAL_CHARS_PER_SOURCE:
            logger.warning(
                "Source exceeded %s chars; dropped %s remaining block(s).",
                MAX_TOTAL_CHARS_PER_SOURCE,
                len(blocks) - len(capped),
            )
            break

        if len(block) > MAX_BLOCK_CHARS:
            block = block[:MAX_BLOCK_CHARS] + " …[truncated]"

        remaining = MAX_TOTAL_CHARS_PER_SOURCE - running
        if len(block) > remaining:
            block = block[:remaining] + " …[truncated]"

        capped.append(block)
        running += len(block)

    return capped


class HtmlExtractor(BaseExtractor):
    """BeautifulSoup extraction, preferring semantic containers over div soup.

    Tables get their own blocks with cell separators preserved, because syllabus
    pages routinely put the unit breakdown and the LTPC row in a table, where
    plain `get_text()` would smear the cells together.
    """

    async def extract(self, raw_bytes: bytes) -> list[str]:
        return await asyncio.to_thread(self._extract_sync, raw_bytes)

    def _extract_sync(self, raw_bytes: bytes) -> list[str]:
        soup = BeautifulSoup(raw_bytes, "html.parser")

        for tag in soup(_STRIP_TAGS):
            tag.decompose()

        blocks: list[str] = []

        for container in soup.find_all(["main", "article"]):
            text = _normalise(container.get_text(" ", strip=True))
            if text:
                blocks.append(text)

        for table in soup.find_all("table"):
            rows: list[str] = []
            for row in table.find_all("tr"):
                cells = [
                    _normalise(cell.get_text(" ", strip=True))
                    for cell in row.find_all(["td", "th"])
                ]
                cells = [cell for cell in cells if cell]
                if cells:
                    rows.append(" | ".join(cells))
            table_text = "\n".join(rows)
            # Skip tables already swept up by a <main>/<article> block above.
            if table_text and not any(rows and rows[0] in block for block in blocks):
                blocks.append(table_text)

        if not blocks:
            body = soup.body or soup
            text = _normalise(body.get_text(" ", strip=True))
            if text:
                blocks.append(text)

        return cap_blocks(blocks)


class PdfTextExtractor(BaseExtractor):
    """PyMuPDF text-layer extraction, one block per page with a page marker so
    structuring can still see rough boundaries.
    """

    async def extract(self, raw_bytes: bytes) -> list[str]:
        return await asyncio.to_thread(self._extract_sync, raw_bytes)

    def _extract_sync(self, raw_bytes: bytes) -> list[str]:
        blocks: list[str] = []
        with pymupdf.open(stream=raw_bytes, filetype="pdf") as doc:
            page_count = doc.page_count
            limit = min(page_count, MAX_PDF_PAGES)
            if page_count > MAX_PDF_PAGES:
                logger.warning(
                    "PDF has %s pages; extracting only the first %s.", page_count, MAX_PDF_PAGES
                )

            for index in range(limit):
                text = _normalise(doc[index].get_text())
                if text:
                    blocks.append(f"[page {index + 1}]\n{text}")

        return cap_blocks(blocks)


class PdfOcrExtractor(BaseExtractor):
    """Fallback for scanned PDFs with no text layer: render each page via
    PyMuPDF, then OCR with pytesseract.
    """

    async def extract(self, raw_bytes: bytes) -> list[str]:
        return await asyncio.to_thread(self._extract_sync, raw_bytes)

    def _extract_sync(self, raw_bytes: bytes) -> list[str]:
        blocks: list[str] = []
        with pymupdf.open(stream=raw_bytes, filetype="pdf") as doc:
            page_count = doc.page_count
            limit = min(page_count, MAX_OCR_PAGES)
            if page_count > MAX_OCR_PAGES:
                logger.warning(
                    "Scanned PDF has %s pages; OCR limited to the first %s.",
                    page_count,
                    MAX_OCR_PAGES,
                )

            for index in range(limit):
                pixmap = doc[index].get_pixmap(dpi=OCR_DPI)
                image = Image.open(io.BytesIO(pixmap.tobytes("png")))
                text = _normalise(pytesseract.image_to_string(image))
                if text:
                    blocks.append(f"[page {index + 1} ocr]\n{text}")

        return cap_blocks(blocks)


def detect_format(url: str, content_type: str | None) -> SourceFormat:
    """Decide HTML vs PDF from URL/content-type."""
    lowered = (content_type or "").lower()
    if "pdf" in lowered or url.lower().split("?")[0].endswith(".pdf"):
        return SourceFormat.PDF
    if "html" in lowered or url.lower().startswith(("http://", "https://")):
        return SourceFormat.HTML
    return SourceFormat.UNKNOWN


def pdf_needs_ocr(extracted_text_blocks: list[str]) -> bool:
    """Decide whether a text-layer extraction was too sparse to be real text.

    Because PdfTextExtractor emits one block per non-empty page, `len(blocks)`
    approximates the pages that yielded text, so mean chars-per-block is a usable
    density signal. Caveat: a mostly-scanned PDF with one good text page reads as
    dense and will not trigger OCR.
    """
    if not extracted_text_blocks:
        return True

    total_chars = sum(len(block) for block in extracted_text_blocks)
    if total_chars < 40:
        return True

    return (total_chars / len(extracted_text_blocks)) < MIN_CHARS_PER_PAGE


class UnsafeSourceURL(ValueError):
    """Raised for a URL this pipeline refuses to fetch."""


def assert_safe_url(url: str) -> None:
    """Reject URLs that shouldn't be fetched.

    Extraction follows URLs supplied by an external search API, so it is an SSRF
    sink: a poisoned or compromised result could point at `file://`, at
    `localhost`, or at a cloud metadata endpoint such as 169.254.169.254. Allow
    only http(s) to public addresses.

    This resolves DNS to catch hostnames that point inward. It does not close the
    DNS-rebinding (TOCTOU) window between this check and the socket connect —
    proportionate for a portfolio project, not a hardened production guard.
    """
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise UnsafeSourceURL(f"refusing non-http(s) scheme: {parsed.scheme or '(none)'}")

    host = parsed.hostname
    if not host:
        raise UnsafeSourceURL("refusing URL with no host")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeSourceURL(f"could not resolve host {host!r}: {exc}") from exc

    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise UnsafeSourceURL(
                f"refusing to fetch {host!r} — resolves to non-public address {address}"
            )


async def fetch_source(url: str, timeout: float = FETCH_TIMEOUT_SECONDS) -> tuple[bytes, str | None]:
    """GET a source URL, returning (body, content_type).

    Mirrors the retry/timeout/logging shape used by the LLM and search clients:
    retry only what is worth retrying, surface everything else immediately.

    Redirects are followed manually so every hop is safety-checked; letting httpx
    auto-follow would let a public URL redirect straight to an internal one.
    """
    assert_safe_url(url)
    last_error: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=False, headers={"User-Agent": _USER_AGENT}
            ) as client:
                response = await client.get(url)

                hops = 0
                while response.is_redirect and hops < MAX_REDIRECTS:
                    target = str(response.next_request.url)
                    assert_safe_url(target)
                    response = await client.get(target)
                    hops += 1

                if response.is_redirect:
                    raise UnsafeSourceURL(f"too many redirects (>{MAX_REDIRECTS}) from {url}")

                response.raise_for_status()
                return response.content, response.headers.get("content-type")

        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            logger.warning("Fetch of %s failed with HTTP %s.", url, status)
            if status not in _RETRYABLE_STATUS_CODES:
                raise
            last_error = exc

        except (httpx.TimeoutException, httpx.TransportError) as exc:
            logger.warning("Fetch of %s failed at transport level: %r", url, exc)
            last_error = exc

        if attempt < MAX_ATTEMPTS:
            await asyncio.sleep(BACKOFF_SECONDS * attempt)

    assert last_error is not None
    raise last_error

