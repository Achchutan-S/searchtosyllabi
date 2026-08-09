from datetime import datetime, timezone

from pydantic import BaseModel, Field

from syllabus_agent.schemas.classification import ClassificationResult
from syllabus_agent.schemas.enums import PipelineStage, RouteDecision
from syllabus_agent.schemas.syllabus import CanonicalSyllabus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PipelineResult(BaseModel):
    """Top-level response envelope returned by the orchestrator, the FastAPI
    endpoint, and the CLI. Covers all four classifier routes plus the
    full-pipeline success case.
    """

    subject: str
    route: RouteDecision
    classification: ClassificationResult
    stage_reached: PipelineStage
    syllabus: CanonicalSyllabus | None = None
    error: str | None = None

    generated_at: datetime = Field(default_factory=_utcnow)
    """When this result was produced. `CanonicalSyllabus` already carries one,
    but only the full-pipeline route has a syllabus — the cache's TTL check has
    to work for classification-only results too, so freshness is tracked here at
    the top level for every route.
    """
    from_cache: bool = False
    """True only on a result served from the cache without running any stage.
    Never persisted as True — see `MongoCacheClient.set`.
    """
