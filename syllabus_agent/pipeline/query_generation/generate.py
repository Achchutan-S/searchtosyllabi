"""Stage 2: query_generation.

Expands a genuine academic subject into multiple targeted search queries aimed at
university syllabi, NPTEL, OpenCourseWare, and textbook tables of contents.
"""

import json
import logging

from pydantic import ValidationError

from syllabus_agent.clients.llm_client import ChatMessage, LLMClient
from syllabus_agent.logging_setup import stage_context
from syllabus_agent.prompts import load_prompt
from syllabus_agent.schemas.query import QueryGenerationResult

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = load_prompt("query_generation")


async def generate_queries(subject: str, llm: LLMClient) -> QueryGenerationResult:
    """Entry point for stage 2."""
    messages = [
        ChatMessage(role="system", content=_SYSTEM_PROMPT),
        ChatMessage(role="user", content=subject),
    ]
    with stage_context("query_generation"):
        raw = await llm.chat_completion(messages, response_format={"type": "json_object"})

    try:
        result = QueryGenerationResult(**json.loads(raw))
        logger.info("query_generation: %r -> %s queries", subject, len(result.queries))
        for query in result.queries:
            logger.debug("  query [%s] %s", query.source_hint, query.query)
        return result
    except (json.JSONDecodeError, ValidationError, TypeError):
        logger.error(
            "Query generation could not parse LLM response for subject %r. Raw response: %s",
            subject,
            raw[:2000],
        )
        raise
