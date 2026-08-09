"""Live integration test for the real Tavily client. Skipped automatically unless
TAVILY_API_KEY is set, so machines/CI without keys don't fail.

Run explicitly with:  pytest tests/test_search_client_live.py -v
"""

import os

import pytest

from syllabus_agent.clients.search_client import TavilySearchClient
from syllabus_agent.config import get_settings

_HAS_KEY = bool(os.getenv("TAVILY_API_KEY", "").strip()) or bool(
    get_settings().tavily_api_key.strip()
)

pytestmark = pytest.mark.skipif(_HAS_KEY is False, reason="TAVILY_API_KEY not set")


async def test_live_search_returns_hits_with_content():
    client = TavilySearchClient(api_key=get_settings().tavily_api_key)

    hits = await client.search("data structures syllabus site:.edu", max_results=5)

    assert hits, "Tavily returned no results"
    for hit in hits:
        assert hit.url.startswith("http")
    # Tavily's "content" field is what feeds pre_extracted_content downstream.
    assert any(hit.pre_extracted_content for hit in hits)
