from pydantic import BaseModel, Field


class SearchQuery(BaseModel):
    """A single targeted search query produced by the query_generation stage."""

    query: str
    source_hint: str | None = None
    # e.g. "university_syllabus" | "nptel" | "ocw" | "textbook_toc"


class QueryGenerationResult(BaseModel):
    subject: str
    queries: list[SearchQuery] = Field(default_factory=list)
