import json

import pytest

from syllabus_agent.pipeline.classifier.classify import classify_subject
from syllabus_agent.schemas.enums import RouteDecision


async def test_classify_parses_default_valid_response(fake_llm):
    result = await classify_subject("data structures", fake_llm)

    assert result.subject == "data structures"
    assert result.route == RouteDecision.GENUINE_ACADEMIC_SUBJECT
    assert len(fake_llm.calls) == 1


async def test_classify_raises_on_unparseable_llm_response(fake_llm):
    """Malformed LLM output must surface, not silently default to a route —
    a wrong default would send an unclassifiable subject down the full pipeline.
    """
    fake_llm.responses.append("Sure! Here's the classification you asked for.")

    with pytest.raises(json.JSONDecodeError):
        await classify_subject("data structures", fake_llm)


async def test_classify_uses_llm_response_when_valid_json(fake_llm):
    fake_llm.responses.append(
        '{"subject": "law", "route": "needs_clarification", "confidence": 0.9, '
        '"reasoning": "Too broad/jurisdiction-specific.", '
        '"clarifying_question": "Which jurisdiction and program?", '
        '"suggested_refinements": ["US JD", "UK LLB"]}'
    )

    result = await classify_subject("law", fake_llm)

    assert result.route == RouteDecision.NEEDS_CLARIFICATION
    assert result.clarifying_question == "Which jurisdiction and program?"
    assert result.suggested_refinements == ["US JD", "UK LLB"]
