import json

from syllabus_agent.pipeline.orchestrator import run_pipeline
from syllabus_agent.schemas.enums import PipelineStage, RouteDecision


def _classification_json(route: str, **overrides) -> str:
    payload = {
        "subject": "x",
        "route": route,
        "confidence": 0.9,
        "reasoning": "test",
        "clarifying_question": None,
        "suggested_refinements": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


async def _run(subject, fake_llm, fake_search, fake_html_extractor, fake_pdf_text_extractor, fake_pdf_ocr_extractor):
    return await run_pipeline(
        subject,
        llm=fake_llm,
        search=fake_search,
        html_extractor=fake_html_extractor,
        pdf_text_extractor=fake_pdf_text_extractor,
        pdf_ocr_extractor=fake_pdf_ocr_extractor,
    )


async def test_run_pipeline_short_circuits_on_needs_clarification(
    fake_llm, fake_search, fake_html_extractor, fake_pdf_text_extractor, fake_pdf_ocr_extractor
):
    fake_llm.responses.append(
        _classification_json("needs_clarification", clarifying_question="Which jurisdiction?")
    )

    result = await _run("law", fake_llm, fake_search, fake_html_extractor, fake_pdf_text_extractor, fake_pdf_ocr_extractor)

    assert result.route == RouteDecision.NEEDS_CLARIFICATION
    assert result.stage_reached == PipelineStage.CLASSIFICATION
    assert result.syllabus is None
    assert result.classification.clarifying_question == "Which jurisdiction?"
    assert fake_search.calls == []  # short-circuited before query generation/search


async def test_run_pipeline_short_circuits_on_general_knowledge(
    fake_llm, fake_search, fake_html_extractor, fake_pdf_text_extractor, fake_pdf_ocr_extractor
):
    fake_llm.responses.append(_classification_json("general_knowledge"))

    result = await _run("how to ride a bike", fake_llm, fake_search, fake_html_extractor, fake_pdf_text_extractor, fake_pdf_ocr_extractor)

    assert result.route == RouteDecision.GENERAL_KNOWLEDGE
    assert result.syllabus is None


async def test_run_pipeline_short_circuits_on_rejected_non_academic(
    fake_llm, fake_search, fake_html_extractor, fake_pdf_text_extractor, fake_pdf_ocr_extractor
):
    fake_llm.responses.append(_classification_json("rejected_non_academic"))

    result = await _run("taylor swift", fake_llm, fake_search, fake_html_extractor, fake_pdf_text_extractor, fake_pdf_ocr_extractor)

    assert result.route == RouteDecision.REJECTED_NON_ACADEMIC
    assert result.syllabus is None


async def test_run_pipeline_runs_full_pipeline_on_genuine_academic_subject(
    fake_llm, fake_search, fake_html_extractor, fake_pdf_text_extractor, fake_pdf_ocr_extractor
):
    fake_llm.responses.append(_classification_json("genuine_academic_subject"))

    result = await _run(
        "data structures", fake_llm, fake_search, fake_html_extractor, fake_pdf_text_extractor, fake_pdf_ocr_extractor
    )

    assert result.route == RouteDecision.GENUINE_ACADEMIC_SUBJECT
    assert result.stage_reached == PipelineStage.STRUCTURING
    assert result.syllabus is not None
    assert len(result.syllabus.units) >= 1
    assert len(fake_search.calls) >= 1
