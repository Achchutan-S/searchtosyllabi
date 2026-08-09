"""Trust-scoring tests.

The content-richness fixtures are modelled on text actually measured in live
runs: the MIT OCW course-admin page that wrongly outranked real content, and a
textbook-style unit breakdown that was wrongly skipped.
"""

from syllabus_agent.utils.trust_scoring import (
    DOMAIN_WEIGHT,
    blend_scores,
    content_richness_score,
    score_content_richness,
    score_source,
    score_source_with_reason,
)

# Shortened from the real ocw.mit.edu/courses/5-12-organic-chemistry-i page that
# caused the incident — logistics only, no curriculum.
ADMIN_PAGE = (
    "Course Meeting Times Lectures: 3 sessions / week, 1 hour / session "
    "Recitations: 2 sessions / week, 1 hour / session Instructors: Dr. Sarah Tabacco "
    "Prof. Barbara Imperiali Office hours by appointment. Grading policy: problem "
    "sets 20%, exams 80%. Academic integrity is expected of all students."
)

# The shape of a real syllabus/textbook TOC that should win.
UNIT_PAGE = (
    "Unit I Introduction to Data Structures: arrays, linked lists, stacks, queues, "
    "complexity analysis, asymptotic notation, recursion. L-T-P-C 4 1 0 5. "
    "Unit II Trees: binary trees, binary search trees, AVL trees, red-black trees, "
    "tree traversals, expression trees, heaps. Credits: 4. "
    "Unit III Hashing: hash functions, separate chaining, linear probing, quadratic "
    "probing, double hashing, rehashing, perfect hashing, cuckoo hashing. "
    "Unit IV Graphs: representations, topological sort, shortest paths, Dijkstra, "
    "minimum spanning trees, network flow, depth first search, breadth first search. "
) * 4

SNIPPET = (
    "81 - Data Structures. Computer Science Spring 2025. Description. This course "
    "investigates abstract data types (ADTs), recursion, algorithms ...Read more"
)


# --- domain reputation (unchanged behaviour) --------------------------------


def test_domain_reputation_still_ranks_ocw_and_edu_above_the_rest():
    assert score_source("https://ocw.mit.edu/courses/x") == 0.95
    assert score_source("https://cs.stanford.edu/syllabus") == 0.8
    assert score_source("https://randomblog.com/x") == 0.3
    assert "known OCW platform" in score_source_with_reason("https://ocw.mit.edu/x")[1]


# --- content richness -------------------------------------------------------


def test_admin_page_scores_low():
    """The exact false positive that motivated this signal."""
    result = score_content_richness(ADMIN_PAGE)

    assert result.score < 0.25, result.explain()
    assert result.signals["admin_penalty"] > 0


def test_unit_breakdown_scores_high():
    result = score_content_richness(UNIT_PAGE)

    assert result.score > 0.7, result.explain()
    assert result.signals["section:unit"] >= 4
    assert result.signals["credits"] == 1.0


def test_search_snippet_scores_near_zero():
    """A ~170-char blurb cannot contain a unit breakdown."""
    assert content_richness_score(SNIPPET) < 0.15


def test_empty_text_scores_zero():
    assert content_richness_score("") == 0.0
    assert content_richness_score("   ") == 0.0


def test_content_richness_orders_unit_page_above_admin_page():
    assert content_richness_score(UNIT_PAGE) > content_richness_score(ADMIN_PAGE)


def test_long_syllabus_is_not_punished_for_mentioning_office_hours():
    """The admin penalty scales with shortness, so real syllabi survive it."""
    with_admin = UNIT_PAGE + " Office hours: Tuesdays. Instructors: Prof. X."

    assert content_richness_score(with_admin) > 0.7


def test_signals_are_recorded_for_tuning():
    signals = score_content_richness(UNIT_PAGE).signals

    assert "length" in signals
    assert "sectioning" in signals
    assert "topic_density" in signals
    assert score_content_richness(UNIT_PAGE).explain()


# --- the blend --------------------------------------------------------------


def test_blend_demotes_high_reputation_admin_page_below_content_rich_pdf():
    """The whole point: reputation must not outvote content on its own.

    Mirrors the live incident — ocw.mit.edu admin page vs a .edu PDF of units.
    """
    mit_admin = blend_scores(0.95, content_richness_score(ADMIN_PAGE))
    edu_pdf = blend_scores(0.8, content_richness_score(UNIT_PAGE))

    assert edu_pdf > mit_admin


def test_blend_keeps_reputation_as_a_tiebreaker_at_equal_content():
    content = content_richness_score(UNIT_PAGE)

    assert blend_scores(0.95, content) > blend_scores(0.8, content)


def test_blend_weights_sum_to_one_so_the_result_stays_in_range():
    assert blend_scores(1.0, 1.0) == 1.0
    assert blend_scores(0.0, 0.0) == 0.0
    assert blend_scores(1.0, 0.0) == DOMAIN_WEIGHT
