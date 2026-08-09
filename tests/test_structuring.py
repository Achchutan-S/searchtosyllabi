"""Structuring + semantic-merge tests.

The old positional merge (align sources by `unit_number`) is gone, so the tests
that asserted its behaviour are replaced by ones asserting the organic
extraction and semantic merge that took its place.
"""

import json
import os

import pytest

from syllabus_agent.config import get_settings
from syllabus_agent.pipeline.structuring.structure import (
    TOP_N_SOURCES_TO_STRUCTURE,
    semantic_merge,
    structure_per_source,
)
from syllabus_agent.schemas.enums import ExtractionMethod
from syllabus_agent.schemas.extraction import ExtractionResult, RawTextBlock
from syllabus_agent.schemas.syllabus import PerSourceStructure, SourceUnit


def _extraction(source_count: int = 1) -> ExtractionResult:
    return ExtractionResult(
        subject="data structures",
        blocks=[
            RawTextBlock(
                source_url=f"https://example{i}.edu/ds",
                method=ExtractionMethod.HTML_PARSER,
                text=(
                    "Unit I Introduction - arrays, linked lists, complexity analysis. "
                    "Unit II Trees - binary trees, AVL trees, heaps. Credits: 4. "
                ) * 10,
                order_index=0,
                trust_score=round(0.99 - (i * 0.01), 2),
            )
            for i in range(source_count)
        ],
    )


def _units_json(unit_count: int) -> str:
    return json.dumps(
        {
            "units": [
                {
                    "unit_title": f"Unit Title {n}",
                    "topics": [f"topic {n}.{t}" for t in range(3)],
                    "ltpc": "3 1 0 4" if n == 1 else None,
                }
                for n in range(1, unit_count + 1)
            ],
            "notes": "extracted as-is",
        }
    )


def _merge_json(unit_count: int = 4) -> str:
    return json.dumps(
        {
            "units": [
                {
                    "unit_title": f"Thematic Unit {n}",
                    "topics": [
                        {
                            "name": f"canonical topic {n}.{t}",
                            "source_urls": ["https://example0.edu/ds"],
                        }
                        for t in range(5)
                    ],
                }
                for n in range(1, unit_count + 1)
            ],
            "total_topics": 999,  # deliberately wrong; must be recomputed
            "merge_notes": "Consensus was strong on trees.",
        }
    )


# --- organic per-source structuring -----------------------------------------


@pytest.mark.parametrize("unit_count", [3, 5, 8])
async def test_structuring_accepts_whatever_unit_count_a_source_has(fake_llm, unit_count):
    """No target shape is imposed — a source with 8 units yields 8."""
    fake_llm.responses.append(_units_json(unit_count))

    structures, _, _ = await structure_per_source("data structures", _extraction(), fake_llm)

    assert len(structures) == 1
    assert len(structures[0].units) == unit_count
    assert structures[0].notes == "extracted as-is"


async def test_ltpc_is_kept_verbatim_as_a_string(fake_llm):
    fake_llm.responses.append(_units_json(2))

    structures, _, _ = await structure_per_source("data structures", _extraction(), fake_llm)

    assert structures[0].units[0].ltpc == "3 1 0 4"
    assert structures[0].units[1].ltpc is None


async def test_source_url_comes_from_our_record_not_the_model(fake_llm):
    """The model is never trusted to echo back which document it was given."""
    fake_llm.responses.append(
        json.dumps({"units": [{"unit_title": "X", "topics": ["a"]}], "source_url": "https://evil.example/hallucinated"})
    )

    structures, _, _ = await structure_per_source("data structures", _extraction(), fake_llm)

    assert structures[0].source_url == "https://example0.edu/ds"


async def test_only_top_n_sources_are_structured(fake_llm):
    structures, skipped, ranking = await structure_per_source(
        "data structures", _extraction(source_count=12), fake_llm
    )

    assert len(fake_llm.calls) == TOP_N_SOURCES_TO_STRUCTURE
    assert len(structures) == TOP_N_SOURCES_TO_STRUCTURE
    assert len(skipped) == 12 - TOP_N_SOURCES_TO_STRUCTURE
    assert len(ranking) == 12
    assert sum(1 for row in ranking if row.structured) == TOP_N_SOURCES_TO_STRUCTURE


async def test_an_unparseable_source_is_skipped_without_failing_the_run(fake_llm):
    fake_llm.responses.append("not json at all")
    fake_llm.responses.append(_units_json(3))

    structures, _, _ = await structure_per_source(
        "data structures", _extraction(source_count=2), fake_llm
    )

    assert len(structures) == 1


# --- semantic merge ---------------------------------------------------------


def _structures(count: int = 2) -> list[PerSourceStructure]:
    return [
        PerSourceStructure(
            source_url=f"https://example{i}.edu/ds",
            trust_score=0.9 - i * 0.1,
            units=[SourceUnit(unit_title=f"Src{i} Unit", topics=["Arrays", "Trees"])],
        )
        for i in range(count)
    ]


async def test_merge_produces_thematic_units_with_provenance(fake_llm):
    fake_llm.responses.append(_merge_json(4))

    syllabus = await semantic_merge("data structures", _structures(), fake_llm)

    assert len(syllabus.units) == 4
    assert syllabus.units[0].unit_title == "Thematic Unit 1"
    topic = syllabus.units[0].topics[0]
    assert topic.name == "canonical topic 1.0"
    assert topic.source_urls == ["https://example0.edu/ds"]
    assert syllabus.merge_notes == "Consensus was strong on trees."


async def test_total_topics_is_recomputed_not_taken_from_the_model(fake_llm):
    """The model reported 999; the real count is 4 units x 5 topics."""
    fake_llm.responses.append(_merge_json(4))

    syllabus = await semantic_merge("data structures", _structures(), fake_llm)

    assert syllabus.total_topics == 20


async def test_merge_is_a_single_llm_call(fake_llm):
    fake_llm.responses.append(_merge_json())

    await semantic_merge("data structures", _structures(count=4), fake_llm)

    assert len(fake_llm.calls) == 1


async def test_per_source_structures_are_preserved_on_the_result(fake_llm):
    """Auditability: inspect what each source contributed without a re-run."""
    fake_llm.responses.append(_merge_json())

    syllabus = await semantic_merge("data structures", _structures(count=3), fake_llm)

    assert len(syllabus.per_source_structures) == 3
    assert syllabus.source_count == 3
    assert syllabus.source_urls == [
        "https://example0.edu/ds",
        "https://example1.edu/ds",
        "https://example2.edu/ds",
    ]
    assert syllabus.per_source_structures[0].units[0].topics == ["Arrays", "Trees"]


async def test_merge_with_no_structures_returns_an_empty_syllabus_not_a_crash(fake_llm):
    syllabus = await semantic_merge("data structures", [], fake_llm)

    assert syllabus.units == []
    assert syllabus.total_topics == 0
    assert fake_llm.calls == []


async def test_merge_raises_on_unparseable_response(fake_llm):
    fake_llm.responses.append("the merge went well!")

    with pytest.raises(json.JSONDecodeError):
        await semantic_merge("data structures", _structures(), fake_llm)


# --- live integration -------------------------------------------------------

_HAS_KEY = bool(os.getenv("GEMINI_API_KEY", "").strip()) or bool(
    get_settings().gemini_api_key.strip()
)

_OTHER_COURSES = [
    "operating system",
    "web development",
    "machine learning",
    "computer graphics",
    "robotics",
    "database system",
    "network",
]


@pytest.mark.skipif(not _HAS_KEY, reason="GEMINI_API_KEY not set")
async def test_live_merge_produces_a_plausible_syllabus():
    """End-to-end shape check on the merge, using realistic per-source input."""
    from syllabus_agent.clients.llm_client import OpenAICompatibleLLMClient

    settings = get_settings()
    llm = OpenAICompatibleLLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
    )

    structures = [
        PerSourceStructure(
            source_url="https://a.edu/ds",
            trust_score=0.9,
            units=[
                SourceUnit(
                    unit_title="Fundamentals",
                    topics=["Arrays", "Linked Lists", "Complexity Analysis", "Big Oh Notation"],
                ),
                SourceUnit(unit_title="Trees", topics=["Binary Trees", "BST", "AVL Trees"]),
            ],
        ),
        PerSourceStructure(
            source_url="https://b.edu/ds",
            trust_score=0.8,
            units=[
                SourceUnit(
                    unit_title="Linear Structures",
                    topics=["Stacks", "Queues", "Linked List Operations"],
                ),
                SourceUnit(
                    unit_title="Sorting and Hashing",
                    topics=["Quick Sort", "Merge Sort", "Hash Tables", "Collision Resolution"],
                ),
            ],
        ),
    ]

    syllabus = await semantic_merge("data structures", structures, llm)

    assert 2 <= len(syllabus.units) <= 10
    assert syllabus.total_topics >= 5
    assert syllabus.merge_notes
    # Every topic must carry provenance.
    for unit in syllabus.units:
        for topic in unit.topics:
            assert topic.source_urls, f"{topic.name} has no provenance"
    # No unit may be named after a different course.
    titles = " ".join(unit.unit_title.lower() for unit in syllabus.units)
    for other in _OTHER_COURSES:
        assert other not in titles, f"unit named after another course: {other}"
