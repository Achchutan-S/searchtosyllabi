from pydantic import BaseModel, Field, field_validator

from syllabus_agent.schemas.enums import RelevanceVerdict


class RelevanceResult(BaseModel):
    """Relevance assessment for a single extracted source."""

    source_url: str
    verdict: RelevanceVerdict
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str

    @field_validator("verdict", mode="before")
    @classmethod
    def _normalise_verdict(cls, value):
        """The prompt asks the model for UPPERCASE verdicts (DIRECT_MATCH) while
        the enum stores lowercase, so accept either rather than failing
        validation on a correct answer in the wrong case.
        """
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @property
    def is_usable(self) -> bool:
        """Whether this source should reach structuring."""
        return self.verdict in (
            RelevanceVerdict.DIRECT_MATCH,
            RelevanceVerdict.PARTIAL_MATCH,
        )
