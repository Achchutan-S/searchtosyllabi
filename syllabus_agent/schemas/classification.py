from pydantic import BaseModel, Field

from syllabus_agent.schemas.enums import RouteDecision


class ClassificationResult(BaseModel):
    """Output of the classifier/router stage."""

    subject: str
    route: RouteDecision
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    clarifying_question: str | None = None
    suggested_refinements: list[str] = Field(default_factory=list)
