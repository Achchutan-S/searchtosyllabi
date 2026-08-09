"""Heuristic trust scoring for candidate sources.

Two independent signals, deliberately kept separate:

1. **Domain reputation** — cheap, available before anything is fetched, used by
   source_collection to filter obvious junk.
2. **Content richness** — computed on already-extracted text, used by structuring
   to decide which sources are worth an LLM call.

Domain reputation alone proved actively misleading: an ocw.mit.edu *course-admin*
page ("Course Meeting Times... Recitations... Instructors") scored 0.95 and took
an LLM slot, while a 37,000-character PDF containing a real 12-unit breakdown
scored 0.8 and was never structured. The structuring LLM correctly returned
`{"units": []}` for the admin pages, and the run produced nothing. Content
richness exists to catch exactly that case.

All pure logic, no external dependency and no API cost.
"""

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from syllabus_agent.schemas.enums import RelevanceVerdict

logger = logging.getLogger(__name__)

TRUSTED_OCW_DOMAINS = {
    "ocw.mit.edu": 0.95,
    "nptel.ac.in": 0.9,
    "edx.org": 0.85,
    "coursera.org": 0.75,
}

KNOWN_UNIVERSITY_SUFFIXES = (".edu", ".ac.in", ".ac.uk")

# Blend weights. Equal weighting is deliberate: the all-MIT incident showed that a
# top-reputation domain with no syllabus content is *worse* than a mid-tier domain
# with a full unit breakdown, so reputation must not be able to outvote content on
# its own. At 0.5/0.5 the MIT admin page (0.95 domain, ~0.1 content) lands near
# 0.53 while a content-rich .edu PDF (0.8 domain, ~0.9 content) lands near 0.85 —
# the ordering we want, without discarding reputation as a tie-breaker.
DOMAIN_WEIGHT = 0.5
CONTENT_WEIGHT = 0.5

# Text shorter than this is a snippet or a course blurb, not a syllabus body.
# Calibrated against the measured JHU example (170 chars) and Tavily's ~918-char
# median snippet.
MIN_USEFUL_CHARS = 500
# Beyond this, more text is not more evidence — avoid rewarding unbounded length.
RICH_CHARS = 8_000

# Sub-signal weights within content richness.
W_SECTIONING = 0.40
W_LENGTH = 0.25
W_CREDITS = 0.15
W_TOPIC_DENSITY = 0.20
W_ADMIN_PENALTY = 0.45

# "Unit III", "Module 2", "Week 7", "Chapter 4" — the shapes a real syllabus uses
# to delimit its sections. Roman numerals included because they are common in
# Indian university syllabi.
_SECTION_PATTERNS = [
    (r"\bunit\s*[-–]?\s*(?:[ivxIVX]+|\d+)\b", "unit"),
    (r"\bmodule\s*[-–]?\s*(?:[ivxIVX]+|\d+)\b", "module"),
    (r"\bweek\s*[-–]?\s*\d+\b", "week"),
    (r"\bchapter\s*[-–]?\s*(?:[ivxIVX]+|\d+)\b", "chapter"),
]

_CREDIT_PATTERNS = [
    (r"\bl\s*[-:/|]\s*t\s*[-:/|]\s*p\s*[-:/|]\s*c\b", "ltpc"),
    (r"\bcredit\s*hours?\b|\bcredits?\b", "credits"),
    (r"\blecture\s*hours?\b", "lecture_hours"),
]

# Hallmarks of a logistics page rather than a curriculum: exactly what outranked
# real content before.
_ADMIN_PATTERNS = [
    (r"\bmeeting times\b", "meeting_times"),
    (r"\boffice hours\b", "office_hours"),
    (r"\binstructors?\s*:", "instructor_label"),
    (r"\brecitations?\b", "recitation"),
    (r"\bgrading policy\b", "grading_policy"),
    (r"\bacademic (?:integrity|honesty)\b", "academic_integrity"),
    (r"\bprerequisites?\s*:", "prerequisites_label"),
]

# Admin boilerplate clusters at the top of a page; only look there so a long real
# syllabus that happens to mention office hours isn't punished.
_ADMIN_WINDOW_CHARS = 1_500


@dataclass
class ContentRichness:
    """A content-richness verdict plus the signals that produced it, so the
    heuristic stays tunable without guesswork.
    """

    score: float
    signals: dict[str, float] = field(default_factory=dict)

    def explain(self) -> str:
        fired = ", ".join(f"{k}={v}" for k, v in sorted(self.signals.items()) if v)
        return fired or "no signals fired"


def extract_domain(url: str) -> str:
    return urlparse(url).netloc.lower()


def score_source(url: str) -> float:
    """Return a 0-1 trust score for a candidate URL based on domain reputation."""
    return score_source_with_reason(url)[0]


def score_source_with_reason(url: str) -> tuple[float, str]:
    """Same score, plus a human-readable explanation for logging/debugging."""
    domain = extract_domain(url)

    if domain in TRUSTED_OCW_DOMAINS:
        return TRUSTED_OCW_DOMAINS[domain], f"known OCW platform ({domain})"

    if domain.endswith(KNOWN_UNIVERSITY_SUFFIXES):
        return 0.8, f"university domain suffix ({domain})"

    if url.lower().endswith(".pdf"):
        return 0.55, "PDF from an unrecognised domain"

    return 0.3, f"not a .edu/.ac.in/.ac.uk or known OCW domain ({domain})"


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def score_content_richness(raw_text: str) -> ContentRichness:
    """Score 0-1 on how much the extracted text looks like an actual syllabus.

    Cheap, local and explainable — no model call. Every sub-signal is recorded on
    the result so `--verbose` shows exactly why a source ranked where it did.
    """
    if not raw_text or not raw_text.strip():
        return ContentRichness(0.0, {"empty": 1})

    text = raw_text.lower()
    char_count = len(raw_text)
    signals: dict[str, float] = {"chars": char_count}

    # 1. Length — a snippet cannot contain a unit breakdown.
    length = _clamp((char_count - MIN_USEFUL_CHARS) / (RICH_CHARS - MIN_USEFUL_CHARS))
    signals["length"] = round(length, 3)

    # 2. Sectioning — reward several *different* section vocabularies and repeated
    #    hits, so one stray "chapter 1" doesn't look like a syllabus.
    families = 0
    total_hits = 0
    for pattern, name in _SECTION_PATTERNS:
        hits = len(re.findall(pattern, text))
        if hits:
            families += 1
            total_hits += hits
            signals[f"section:{name}"] = hits
    sectioning = 0.5 * _clamp(families / 2) + 0.5 * _clamp(total_hits / 8)
    signals["sectioning"] = round(sectioning, 3)

    # 3. Credit/LTPC markers — strong evidence of a real course document.
    credits = 0.0
    for pattern, name in _CREDIT_PATTERNS:
        if re.search(pattern, text):
            credits = 1.0
            signals[f"credit:{name}"] = 1
    signals["credits"] = credits

    # 4. Topic-list density — syllabus topic lists are comma/semicolon heavy prose.
    #    Extraction normalises whitespace, so line-based list detection is not
    #    available; separator density is the usable proxy.
    separators = raw_text.count(",") + raw_text.count(";") + raw_text.count("•")
    density = _clamp((separators / max(char_count, 1)) / 0.02)
    signals["topic_density"] = round(density, 3)

    # 5. Admin penalty — scaled by shortness, since a long syllabus mentioning
    #    office hours is fine while a short page of nothing but logistics is not.
    admin_hits = sum(1 for pattern, _ in _ADMIN_PATTERNS if re.search(pattern, text[:_ADMIN_WINDOW_CHARS]))
    for pattern, name in _ADMIN_PATTERNS:
        if re.search(pattern, text[:_ADMIN_WINDOW_CHARS]):
            signals[f"admin:{name}"] = 1
    admin_penalty = W_ADMIN_PENALTY * _clamp(admin_hits / 3) * (1.0 - length)
    signals["admin_penalty"] = round(admin_penalty, 3)

    score = _clamp(
        W_SECTIONING * sectioning
        + W_LENGTH * length
        + W_CREDITS * credits
        + W_TOPIC_DENSITY * density
        - admin_penalty
    )

    return ContentRichness(round(score, 3), signals)


def content_richness_score(raw_text: str) -> float:
    """Convenience wrapper when only the number is needed."""
    return score_content_richness(raw_text).score


def blend_scores(domain_score: float, content_score: float) -> float:
    """Combine domain reputation with content richness into a ranking score."""
    return round(DOMAIN_WEIGHT * domain_score + CONTENT_WEIGHT * content_score, 4)


# A partial match is a document where only a subsection concerns the target
# course (a catalog page, say), so the structuring LLM has to work harder to
# ignore the rest and is likelier to import neighbouring courses' topics. Demote
# it relative to a document that is wholly about the course, without excluding it
# — sometimes the catalog entry is the only real topic list available.
PARTIAL_MATCH_TRUST_MULTIPLIER = 0.7


def relevance_multiplier(verdict: RelevanceVerdict) -> float:
    """Trust adjustment for a source given its relevance verdict.

    Only the two usable verdicts have a multiplier; field_level and unrelated
    sources are filtered out before structuring and never ranked.
    """
    if verdict == RelevanceVerdict.DIRECT_MATCH:
        return 1.0
    if verdict == RelevanceVerdict.PARTIAL_MATCH:
        return PARTIAL_MATCH_TRUST_MULTIPLIER
    return 0.0
