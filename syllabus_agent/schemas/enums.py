from enum import Enum


class RouteDecision(str, Enum):
    """Classifier output — determines which downstream path the orchestrator runs."""

    GENUINE_ACADEMIC_SUBJECT = "genuine_academic_subject"
    GENERAL_KNOWLEDGE = "general_knowledge"
    NEEDS_CLARIFICATION = "needs_clarification"
    REJECTED_NON_ACADEMIC = "rejected_non_academic"


class SourceFormat(str, Enum):
    HTML = "html"
    PDF = "pdf"
    UNKNOWN = "unknown"


class ExtractionMethod(str, Enum):
    HTML_PARSER = "html_parser"
    PDF_TEXT = "pdf_text"
    PDF_OCR = "pdf_ocr"


class RelevanceVerdict(str, Enum):
    """How relevant an extracted source is to the target subject."""

    DIRECT_MATCH = "direct_match"
    """A syllabus/outline for exactly this course."""
    PARTIAL_MATCH = "partial_match"
    """Contains a section about this course among other things."""
    FIELD_LEVEL = "field_level"
    """About the broader field, or a related but different course."""
    UNRELATED = "unrelated"
    """Not about this subject at all."""


class PipelineStage(str, Enum):
    CLASSIFICATION = "classification"
    QUERY_GENERATION = "query_generation"
    SOURCE_COLLECTION = "source_collection"
    EXTRACTION = "extraction"
    RELEVANCE = "relevance"
    STRUCTURING = "structuring"
