from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl


class SourceUnit(BaseModel):
    """A unit exactly as one source document presents it.

    Deliberately has no `unit_number`: unit identity is semantic, not positional.
    Merging "everyone's Unit 1" across sources with incompatible numbering was
    what produced a unit titled "Exploring Quantitative Analysis" containing
    "Sorting Algorithms, Lists, Stacks".
    """

    unit_title: str
    topics: list[str] = Field(default_factory=list)
    ltpc: str | None = None
    """Verbatim as written, e.g. "3 1 0 4" — not parsed into components, since
    sources state it in wildly different formats and the merge does not use it
    arithmetically.
    """


class PerSourceStructure(BaseModel):
    """One source's structure, captured before the merge.

    Kept on the final result so a questionable merge can be audited against what
    each source actually contributed, without re-running the pipeline.
    """

    source_url: str
    trust_score: float = Field(ge=0.0, le=1.0)
    units: list[SourceUnit] = Field(default_factory=list)
    notes: str | None = None

    @property
    def topic_count(self) -> int:
        return sum(len(unit.topics) for unit in self.units)


class SourceRanking(BaseModel):
    """Why a source was or wasn't structured, with both signals broken out so the
    blend can be audited and retuned.
    """

    source_url: HttpUrl
    domain_score: float = Field(ge=0.0, le=1.0)
    content_score: float = Field(ge=0.0, le=1.0)
    blended_score: float = Field(ge=0.0, le=1.0)
    relevance_penalty: float = Field(default=1.0, ge=0.0, le=1.0)
    """Applied to `blended_score` for selection only. `domain_score` here and the
    trust passed to the merge step are both unpenalised.
    """
    extracted_chars: int = 0
    structured: bool = False


class MergedTopic(BaseModel):
    """A single topic in the merged syllabus, with provenance."""

    name: str
    source_urls: list[str] = Field(default_factory=list)


class MergedUnit(BaseModel):
    """A thematic unit in the merged syllabus."""

    unit_title: str
    topics: list[MergedTopic] = Field(default_factory=list)


class CanonicalSyllabus(BaseModel):
    """The merged, canonical syllabus.

    Unit count is whatever the material required — no target is imposed.
    """

    subject: str
    units: list[MergedUnit] = Field(default_factory=list)
    total_topics: int = 0
    merge_notes: str = ""
    source_count: int = 0
    source_urls: list[str] = Field(default_factory=list)

    per_source_structures: list[PerSourceStructure] = Field(default_factory=list)
    """Raw per-source output before merging, for auditability."""
    collected_not_structured: list[HttpUrl] = Field(default_factory=list)
    """Extracted and relevance-approved, but below the top-N cutoff."""
    source_ranking: list[SourceRanking] = Field(default_factory=list)
    """Full blended-ranking table, best first."""

    generated_at: datetime
