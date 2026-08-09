"""Live integration test for the real LLM client. Skipped automatically unless
GEMINI_API_KEY is set, so machines/CI without keys don't fail.

Run explicitly with:  pytest tests/test_llm_client_live.py -v
"""

import json
import os

import pytest

from syllabus_agent.clients.llm_client import (
    ChatMessage,
    OpenAICompatibleLLMClient,
    strip_code_fences,
)
from syllabus_agent.config import get_settings

_HAS_KEY = bool(os.getenv("GEMINI_API_KEY", "").strip()) or bool(
    get_settings().gemini_api_key.strip()
)

pytestmark = pytest.mark.skipif(_HAS_KEY is False, reason="GEMINI_API_KEY not set")


def test_strip_code_fences_unwraps_json_block():
    """Runs without a key — models wrap JSON in fences even when told not to."""
    assert strip_code_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_code_fences('```\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_code_fences('{"a": 1}') == '{"a": 1}'


async def test_live_chat_completion_returns_parseable_json():
    settings = get_settings()
    client = OpenAICompatibleLLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
    )

    raw = await client.chat_completion(
        [
            ChatMessage(
                role="system",
                content='Return one raw JSON object and nothing else: {"ok": true}',
            ),
            ChatMessage(role="user", content="go"),
        ],
        response_format={"type": "json_object"},
    )

    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
