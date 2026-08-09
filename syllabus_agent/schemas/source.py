from pydantic import BaseModel, Field, HttpUrl

from syllabus_agent.schemas.enums import SourceFormat


class CandidateSource(BaseModel):
    """A single URL surfaced by source_collection, filtered and trust-scored."""

    url: HttpUrl
    title: str | None = None
    query: str
    format: SourceFormat = SourceFormat.UNKNOWN
    trust_score: float = Field(ge=0.0, le=1.0)
    domain: str
    university: str | None = None
    year: int | None = None
    pre_extracted_content: str | None = None
    """Cleaned page text the search provider already returned, if any. Lets the
    extraction stage skip re-fetching this URL. Populated but not yet consumed.
    """


class SourceCollectionResult(BaseModel):
    subject: str
    sources: list[CandidateSource] = Field(default_factory=list)
