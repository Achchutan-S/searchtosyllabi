"""Shared fakes for stubbing out the client interfaces in stage tests. These
implement the same ABCs as the real clients so stage code under test can't tell
the difference — that's the point of putting external dependencies behind
interfaces in the first place.
"""

import pytest

from syllabus_agent.clients.extraction_client import BaseExtractor
from syllabus_agent.clients.llm_client import ChatMessage, LLMClient
from syllabus_agent.clients.search_client import SearchClient, SearchHit


_DEFAULT_CLASSIFICATION = (
    '{"subject": "data structures", "route": "genuine_academic_subject", '
    '"confidence": 0.95, "reasoning": "Established CS course.", '
    '"clarifying_question": null, "suggested_refinements": []}'
)
_DEFAULT_QUERIES = (
    '{"subject": "data structures", "queries": '
    '[{"query": "data structures syllabus site:.edu", "source_hint": "university_syllabus"}]}'
)
_DEFAULT_RELEVANCE = (
    '{"verdict": "direct_match", "confidence": 0.95, '
    '"reasoning": "A syllabus for exactly this course."}'
)
_DEFAULT_UNITS = (
    '{"units": [{"unit_title": "Introduction", '
    '"topics": ["Arrays", "Linked Lists"], "ltpc": "3 1 0 4"}], "notes": null}'
)
_DEFAULT_MERGE = (
    '{"units": [{"unit_title": "Foundations", "topics": ['
    '{"name": "Arrays", "source_urls": ["https://mit.edu/courses/data-structures"]}, '
    '{"name": "Linked Lists", "source_urls": ["https://mit.edu/courses/data-structures"]}]}], '
    '"total_topics": 2, "merge_notes": "Sources agreed on the fundamentals."}'
)


def _default_response_for(messages: list[ChatMessage]) -> str:
    """Pick a schema-valid canned response based on which stage's system prompt
    is in play, so multi-stage tests (e.g. the orchestrator) work without having
    to queue a response per LLM call.

    Keyed on a distinctive phrase from each prompt file — if a prompt is reworded
    so its marker disappears, the orchestrator test fails loudly rather than
    silently feeding the wrong shape to a stage.
    """
    system = next((m.content for m in messages if m.role == "system"), "")
    if "router for an education pipeline" in system:
        return _DEFAULT_CLASSIFICATION
    if "targeted web search queries" in system:
        return _DEFAULT_QUERIES
    if "academic curriculum analyst" in system:
        return _DEFAULT_RELEVANCE
    if "academic curriculum designer" in system:
        return _DEFAULT_MERGE
    if "academic syllabus parser" in system:
        return _DEFAULT_UNITS
    raise AssertionError(
        "FakeLLMClient has no default response for this system prompt; "
        "add one in conftest._default_response_for."
    )


class FakeLLMClient(LLMClient):
    """Returns queued responses in order, then falls back to a schema-valid
    default for whichever stage is calling. Records every call for assertions.
    """

    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[list[ChatMessage]] = []

    async def chat_completion(
        self,
        messages: list[ChatMessage],
        *,
        response_format: dict | None = None,
        temperature: float = 0.2,
    ) -> str:
        self.calls.append(messages)
        if self.responses:
            return self.responses.pop(0)
        return _default_response_for(messages)


class FakeSearchClient(SearchClient):
    """Returns the same canned hits for every query; records every call."""

    def __init__(self, hits: list[SearchHit] | None = None) -> None:
        # pre_extracted_content mirrors what Tavily actually returns and keeps
        # the extraction stage on its no-fetch fast path, so tests never touch
        # the network.
        self.hits = hits if hits is not None else [
            SearchHit(
                url="https://mit.edu/courses/data-structures",
                title="MIT — Data Structures",
                snippet="Unit-by-unit breakdown of a data structures course.",
                # Long enough to clear MIN_PRE_EXTRACTED_CHARS, so extraction
                # stays on its no-fetch fast path and tests never hit the network.
                pre_extracted_content=(
                    "Unit I - Arrays, linked lists, complexity analysis. "
                    "Unit II - Trees, binary search trees, balanced trees. "
                ) * 40,
            ),
            SearchHit(
                url="https://example.com/blog/data-structures",
                title="Some blog post",
                snippet="Not a trustworthy academic source.",
                pre_extracted_content="A blog post about data structures.",
            ),
        ]
        self.calls: list[str] = []

    async def search(self, query: str, max_results: int = 10) -> list[SearchHit]:
        self.calls.append(query)
        return self.hits[:max_results]


class FakeExtractor(BaseExtractor):
    def __init__(self, blocks: list[str] | None = None) -> None:
        self.blocks = blocks if blocks is not None else [
            "Unit 1: Introduction — arrays, linked lists, and complexity analysis.",
            "Unit 2: Trees — binary trees, BSTs, and balanced trees.",
        ]

    async def extract(self, raw_bytes: bytes) -> list[str]:
        return self.blocks


@pytest.fixture
def fake_llm() -> FakeLLMClient:
    return FakeLLMClient()


@pytest.fixture
def fake_search() -> FakeSearchClient:
    return FakeSearchClient()


@pytest.fixture
def fake_html_extractor() -> FakeExtractor:
    return FakeExtractor()


@pytest.fixture
def fake_pdf_text_extractor() -> FakeExtractor:
    return FakeExtractor()


@pytest.fixture
def fake_pdf_ocr_extractor() -> FakeExtractor:
    return FakeExtractor()
