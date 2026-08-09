import json

import pytest

from syllabus_agent.pipeline.query_generation.generate import generate_queries


async def test_generate_queries_parses_default_valid_response(fake_llm):
    result = await generate_queries("data structures", fake_llm)

    assert result.subject == "data structures"
    assert len(result.queries) >= 1
    assert any("data structures" in q.query for q in result.queries)
    assert len(fake_llm.calls) == 1


async def test_generate_queries_raises_on_unparseable_llm_response(fake_llm):
    fake_llm.responses.append("Here are some queries: 1. data structures syllabus")

    with pytest.raises(json.JSONDecodeError):
        await generate_queries("data structures", fake_llm)


async def test_generate_queries_uses_llm_response_when_valid_json(fake_llm):
    fake_llm.responses.append(
        '{"subject": "data structures", "queries": '
        '[{"query": "data structures nptel", "source_hint": "nptel"}]}'
    )

    result = await generate_queries("data structures", fake_llm)

    assert len(result.queries) == 1
    assert result.queries[0].source_hint == "nptel"
