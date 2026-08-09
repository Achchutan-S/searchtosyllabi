from collections import defaultdict

from pydantic import BaseModel, Field, HttpUrl

from syllabus_agent.schemas.enums import ExtractionMethod


class RawTextBlock(BaseModel):
    """A raw, untouched-but-isolated unit-level text block pulled from a source."""

    source_url: HttpUrl
    method: ExtractionMethod
    text: str
    order_index: int
    trust_score: float = Field(ge=0.0, le=1.0)
    relevance_penalty: float = Field(default=1.0, ge=0.0, le=1.0)
    """Demotion factor from the relevance verdict, applied to *ranking only*.

    Kept separate from `trust_score` deliberately: folding it into the score
    meant a partial-match source could never clear the merge prompt's
    `trust >= 0.7` bar, so its unique topics were silently dropped.
    """
    university: str | None = None
    year: int | None = None


class ExtractionFailure(BaseModel):
    """A source that could not be extracted. Kept with its error so a failure is
    diagnosable without re-running, rather than just a missing URL.
    """

    source_url: HttpUrl
    error: str
    method_attempted: ExtractionMethod | None = None


class ExtractedSource(BaseModel):
    """One source's blocks collapsed into a single text body.

    `ExtractionResult.blocks` is deliberately flat (per page / per section), but
    relevance assessment and ranking both reason about a *source* as a whole, so
    this is the per-source view over the same data.
    """

    source_url: HttpUrl
    text: str
    trust_score: float = Field(ge=0.0, le=1.0)
    method: ExtractionMethod
    university: str | None = None
    year: int | None = None

    @property
    def char_count(self) -> int:
        return len(self.text)


class ExtractionResult(BaseModel):
    subject: str
    blocks: list[RawTextBlock] = Field(default_factory=list)
    failures: list[ExtractionFailure] = Field(default_factory=list)

    def as_sources(self) -> list[ExtractedSource]:
        """Group blocks by source, preserving document order within each."""
        grouped: dict[HttpUrl, list[RawTextBlock]] = defaultdict(list)
        for block in self.blocks:
            grouped[block.source_url].append(block)

        sources: list[ExtractedSource] = []
        for source_url, blocks in grouped.items():
            ordered = sorted(blocks, key=lambda b: b.order_index)
            sources.append(
                ExtractedSource(
                    source_url=source_url,
                    text="\n".join(block.text for block in ordered),
                    trust_score=max(block.trust_score for block in ordered),
                    method=ordered[0].method,
                    university=ordered[0].university,
                    year=ordered[0].year,
                )
            )
        return sources
