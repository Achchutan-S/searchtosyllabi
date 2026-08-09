"""Stage 1: classifier/router.

Classifies the input subject into one of RouteDecision's four routes, which the
orchestrator uses to decide whether (and how) to run the rest of the pipeline.
"""

import json
import logging

from pydantic import ValidationError

from syllabus_agent.clients.llm_client import ChatMessage, LLMClient
from syllabus_agent.logging_setup import stage_context
from syllabus_agent.prompts import load_prompt
from syllabus_agent.schemas.classification import ClassificationResult

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = load_prompt("classifier")


async def classify_subject(subject: str, llm: LLMClient) -> ClassificationResult:
    """Entry point for stage 1."""
    messages = [
        ChatMessage(role="system", content=_SYSTEM_PROMPT),
        ChatMessage(role="user", content=subject),
    ]
    with stage_context("classifier"):
        raw = await llm.chat_completion(messages, response_format={"type": "json_object"})

    try:
        result = ClassificationResult(**json.loads(raw))
        logger.info(
            "classifier: %r -> %s (confidence %.2f)",
            subject,
            result.route.value,
            result.confidence,
        )
        return result
    except (json.JSONDecodeError, ValidationError, TypeError):
        # Deliberately not falling back to a default route: a silent default here
        # would send an unclassifiable subject down the full search pipeline and
        # hide the real problem. Log what the model actually said, then fail.
        logger.error(
            "Classifier could not parse LLM response for subject %r. Raw response: %s",
            subject,
            raw[:2000],
        )
        raise
