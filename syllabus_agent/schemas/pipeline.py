from pydantic import BaseModel

from syllabus_agent.schemas.classification import ClassificationResult
from syllabus_agent.schemas.enums import PipelineStage, RouteDecision
from syllabus_agent.schemas.syllabus import CanonicalSyllabus


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
