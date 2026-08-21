"""Phase 0 hypothesis test: does adaptive, domain-grounded questioning beat a
static questionnaire at helping someone with a vague goal work out what to
investigate?

Three arms, one identical opening persona, blind-comparable transcripts:

    a  static            a fixed list of questions, asked in order, no adaptivity
    b  generic adaptive  one question per turn, chosen from the whole conversation
    c  india adaptive    same mechanics as b, plus an Indian opportunity vocabulary
                         — which arm C also gets at synthesis time, not just while
                         asking questions

Arms B and C tag every question with the decision dimension it addresses, so the
breadth of an interview is checked in code rather than taken on the model's word.

Usage:
    python phase0/run.py --arm a
    python phase0/run.py --arm b
    python phase0/run.py --arm c
    python phase0/run.py --arm b --smoke     # mechanics check only, see --smoke help

Deliberately additive: nothing under syllabus_agent/ is imported for anything
other than the LLM client, its settings, the shared base prompt, and the
optional call trace. No pipeline stage is involved — Phase 0 does no searching.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Running this as `python phase0/run.py` puts phase0/ on sys.path, not the repo
# root, so syllabus_agent would not be importable. Add the root explicitly so the
# documented invocation works from a plain checkout with no install step and no
# PYTHONPATH fiddling.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from syllabus_agent.clients.llm_client import ChatMessage, OpenAICompatibleLLMClient
from syllabus_agent.config import get_settings, warn_on_missing_keys
from syllabus_agent.logging_setup import configure_logging, register_secret, stage_context
from syllabus_agent.prompts import load_prompt

PHASE0_DIR = Path(__file__).parent
PROMPTS_DIR = PHASE0_DIR / "prompts"
TRANSCRIPTS_DIR = PHASE0_DIR / "transcripts"
STATIC_QUESTIONS_FILE = PHASE0_DIR / "questions_static.txt"

# Built into the script rather than entered at the prompt, so it cannot drift
# between arms — the three transcripts are only comparable if the opening is
# byte-identical across all of them.
OPENING_STATEMENT = (
    "I'm a 24-year-old BTech Computer Science graduate. I graduated in 2024. I "
    "don't really know what options I have for my career. I want to understand "
    "what directions are available to me."
)

DIMENSIONS = (
    "current_status",
    "interest_type",
    "further_study",
    "stability_vs_risk",
    "geography",
    "timeline",
    "values",
)
"""The decision dimensions an interview is expected to span. Listed identically
in both adaptive prompts; tagging each question with one of these is what makes
coverage checkable in code instead of a property the model merely asserts."""

UNTAGGED_DIMENSION = "untagged"
"""Recorded when a turn carries no usable tag. Never earns breadth credit."""

MIN_QUESTIONS = 4
MIN_DIMENSIONS = 5
"""Breadth floor. Both real adaptive runs stopped at exactly the old count-only
floor of 4, and one covered a single cluster of related dimensions — the model
anchored to the minimum rather than reasoning about coverage. A count floor
cannot detect that; a distinct-dimension floor can."""

MAX_QUESTIONS = 10
"""Absolute backstop, enforced in code whatever the model signals and whatever
the dimension floor says. Raised from 8 because satisfying a real breadth
requirement can legitimately need a couple more turns than a count floor did."""

MAX_INVALID_TURNS = 3
"""Consecutive contract violations tolerated before abandoning the run, so a
model that will not emit the JSON envelope cannot burn the whole question cap."""

# The client defaults to 0.2, which is tuned for classification. Follow-up
# questions want variety — a deterministic interviewer asks the same interview.
CONVERSATION_TEMPERATURE = 0.8

ARMS = {
    "a": ("static", None),
    "b": ("generic_adaptive", "interviewer_generic"),
    "c": ("india_adaptive", "interviewer_india"),
}

INDIA_ARM = "c"
INDIA_VOCABULARY_PLACEHOLDER = "{{INDIA_VOCABULARY}}"


def read_static_questions() -> list[str]:
    lines = STATIC_QUESTIONS_FILE.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def _read_phase0_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8").strip()


def india_vocabulary() -> str:
    """The Indian opportunity vocabulary — single source of truth.

    Consumed twice: spliced into the arm C interview prompt at its placeholder,
    and appended to the arm C summary prompt. It previously lived only inside
    interviewer_india.txt, which meant the synthesis step never saw it and arm C
    was grounded at question-asking time but generic at conclusion time.
    """
    return _read_phase0_prompt("india_vocabulary")


def interview_system_prompt(prompt_name: str) -> str:
    """Load an adaptive-arm prompt, resolving the vocabulary placeholder."""
    return _read_phase0_prompt(prompt_name).replace(
        INDIA_VOCABULARY_PLACEHOLDER, india_vocabulary()
    )


def summary_system_prompt(arm: str) -> str:
    """Compose the summary task on top of the pipeline's shared base prompt.

    `load_prompt("base")` returns base.txt alone (it short-circuits the prefixing
    for its own name), so this reproduces exactly what `load_prompt(name)` would
    build — without adding a file under syllabus_agent/prompts/.

    Arm C additionally gets the vocabulary and a note on how to use it here.
    Arms A and B are left exactly as they were, so the B-vs-C comparison stays a
    one-variable change.
    """
    parts = [load_prompt("base"), _read_phase0_prompt("summary")]
    if arm == INDIA_ARM:
        parts += [india_vocabulary(), _read_phase0_prompt("summary_india_addendum")]
    return "\n\n".join(parts)


def ask_human(question: str, dimension: str | None = None) -> str | None:
    """Print a question and read the answer. None means the human ended the run."""
    tag = f" \033[2m[{dimension}]\033[0m" if dimension else ""
    print(f"\n\033[1mQ: {question}\033[0m{tag}")
    try:
        answer = input("A: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n[input ended — stopping the interview here]", file=sys.stderr)
        return None
    return answer or "(no answer given)"


async def run_static_arm() -> tuple[list[dict], str]:
    """Arm A. Zero LLM calls during the Q&A phase — the questions are fixed."""
    questions = read_static_questions()
    qa: list[dict] = []

    for question in questions:
        answer = ask_human(question)
        if answer is None:
            return qa, "interrupted"
        qa.append({"question": question, "answer": answer})

    return qa, "question list exhausted"


_CONTRACT_REMINDER = (
    "That turn did not follow the output contract. Reply with exactly one raw JSON "
    'object and nothing else: either {"dimension": "<tag>", "question": "<text>"} to '
    'ask your next question, or {"done": true} to finish. No prose, no code fences.'
)


def _parse_turn(reply: str) -> dict | None:
    """Validate one turn against the JSON contract.

    Returns `{"done": True}`, or `{"dimension": ..., "question": ...}`, or None
    when the reply satisfies neither shape.
    """
    try:
        payload = json.loads(reply)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    if payload.get("done") is True:
        return {"done": True}

    question = payload.get("question")
    if not isinstance(question, str) or not question.strip():
        return None

    # An unrecognised or missing tag is not fatal — the question is still worth
    # asking. It simply earns no breadth credit, because only members of
    # DIMENSIONS are ever added to the covered set.
    dimension = payload.get("dimension")
    if not isinstance(dimension, str) or not dimension.strip():
        dimension = UNTAGGED_DIMENSION

    return {"dimension": dimension.strip(), "question": question.strip()}


def _done_pushback(
    asked: int, covered: set[str], min_questions: int, min_dimensions: int
) -> str:
    """Explain why an early `{"done": true}` was refused, and what to do instead."""
    shortfalls = []
    if asked < min_questions:
        shortfalls.append(
            f"you have asked only {asked} question(s), and at least {min_questions} are required"
        )
    if len(covered) < min_dimensions:
        shortfalls.append(
            f"you have covered only {len(covered)} distinct dimension(s) "
            f"({', '.join(sorted(covered)) or 'none'}), and at least "
            f"{min_dimensions} are required"
        )
    untouched = [name for name in DIMENSIONS if name not in covered]

    return (
        "Not yet — "
        + "; ".join(shortfalls)
        + f". Dimensions still untouched: {', '.join(untouched)}. "
        "Your next turn must be a question tagged with one of those untouched "
        'dimensions. Do not send {"done": true} again yet.'
    )


async def run_adaptive_arm(
    client: OpenAICompatibleLLMClient,
    system_prompt: str,
    *,
    min_questions: int,
    max_questions: int,
    min_dimensions: int,
) -> tuple[list[dict], str]:
    """Arms B and C. One LLM call per turn.

    The accumulated `messages` list is the entire state of this experiment —
    the existing pipeline has no session concept, and for three throwaway
    conversations it does not need one.

    Stopping is not left to the model's own account of its confidence. Both real
    runs stopped at the bare count floor having probed a single cluster, so
    completion is granted only when the question floor *and* the distinct
    dimension floor are both satisfied.
    """
    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content=OPENING_STATEMENT),
    ]
    qa: list[dict] = []
    covered: set[str] = set()
    invalid_streak = 0

    while len(qa) < max_questions:
        with stage_context("phase0_interview"):
            reply = (
                await client.chat_completion(
                    messages,
                    response_format={"type": "json_object"},
                    temperature=CONVERSATION_TEMPERATURE,
                )
            ).strip()

        turn = _parse_turn(reply)

        if turn is None:
            invalid_streak += 1
            if invalid_streak >= MAX_INVALID_TURNS:
                return qa, (
                    f"abandoned — {MAX_INVALID_TURNS} consecutive turns broke the "
                    f"JSON contract"
                )
            messages.append(ChatMessage(role="assistant", content=reply))
            messages.append(ChatMessage(role="user", content=_CONTRACT_REMINDER))
            continue
        invalid_streak = 0

        if turn.get("done"):
            if len(qa) >= min_questions and len(covered) >= min_dimensions:
                return qa, (
                    f"model signalled completion — {len(covered)}/{len(DIMENSIONS)} "
                    f"dimensions covered"
                )
            messages.append(ChatMessage(role="assistant", content=reply))
            messages.append(
                ChatMessage(
                    role="user",
                    content=_done_pushback(
                        len(qa), covered, min_questions, min_dimensions
                    ),
                )
            )
            continue

        answer = ask_human(turn["question"], turn["dimension"])
        if answer is None:
            return qa, "interrupted"

        qa.append(
            {
                "dimension": turn["dimension"],
                "question": turn["question"],
                "answer": answer,
            }
        )
        # A repeat on an already-covered dimension is legitimate depth, but a set
        # means it buys no additional breadth.
        if turn["dimension"] in DIMENSIONS:
            covered.add(turn["dimension"])

        messages.append(ChatMessage(role="assistant", content=reply))
        messages.append(ChatMessage(role="user", content=answer))

    return qa, (
        f"hard cap of {max_questions} questions reached — "
        f"{len(covered)}/{len(DIMENSIONS)} dimensions covered"
    )


async def summarise(
    client: OpenAICompatibleLLMClient, qa: list[dict], arm: str
) -> tuple[dict | None, str]:
    """One structured call over the finished conversation. Returns (parsed, raw)."""
    parts = [f"Opening statement from the person:\n{OPENING_STATEMENT}"]
    for index, item in enumerate(qa, start=1):
        parts.append(f"Q{index}: {item['question']}\nA{index}: {item['answer']}")

    messages = [
        ChatMessage(role="system", content=summary_system_prompt(arm)),
        ChatMessage(role="user", content="\n\n".join(parts)),
    ]

    with stage_context("phase0_summary"):
        raw = await client.chat_completion(
            messages, response_format={"type": "json_object"}
        )

    try:
        return json.loads(raw), raw
    except json.JSONDecodeError:
        # The client already retries a parse failure; if it still lands here the
        # raw body is the useful artefact, so keep it in the transcript.
        return None, raw


def write_transcript(
    arm_key: str,
    arm_label: str,
    model: str,
    qa: list[dict],
    stop_reason: str,
    summary: dict | None,
    summary_raw: str | None,
) -> Path:
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = TRANSCRIPTS_DIR / f"{arm_key}_{arm_label}_{stamp}.md"
    # A transcript is the entire output of this experiment, so never overwrite
    # one — two runs of the same arm inside one second would otherwise collide
    # silently and destroy the earlier result.
    suffix = 2
    while path.exists():
        path = TRANSCRIPTS_DIR / f"{arm_key}_{arm_label}_{stamp}_{suffix}.md"
        suffix += 1

    lines = [
        f"# Phase 0 — arm {arm_key.upper()} ({arm_label.replace('_', ' ')})",
        "",
        f"- **Run (UTC):** {datetime.now(timezone.utc).isoformat()}",
        f"- **Model:** `{model}`" if arm_key != "a" else "- **Model:** none for the Q&A phase (fixed question list)",
        f"- **Questions asked:** {len(qa)}",
        f"- **Stop reason:** {stop_reason}",
    ]

    # Coverage is the point of the dimension tags, so it belongs in the header
    # where a reader comparing transcripts sees it before the conversation.
    tagged = {item.get("dimension") for item in qa} & set(DIMENSIONS)
    if tagged:
        untouched = [name for name in DIMENSIONS if name not in tagged]
        lines.append(
            f"- **Dimensions covered:** {len(tagged)}/{len(DIMENSIONS)} — "
            f"{', '.join(name for name in DIMENSIONS if name in tagged)}"
        )
        lines.append(
            f"- **Dimensions untouched:** {', '.join(untouched) if untouched else 'none'}"
        )

    lines += [
        "",
        "## Opening statement",
        "",
        f"> {OPENING_STATEMENT}",
        "",
        "## Conversation",
        "",
    ]

    for index, item in enumerate(qa, start=1):
        dimension = item.get("dimension")
        tag = f" _[{dimension}]_" if dimension else ""
        lines += [
            f"**Q{index}.**{tag} {item['question']}",
            "",
            f"**A{index}.** {item['answer']}",
            "",
        ]

    lines += ["## Structured summary", ""]

    if summary is not None:
        headings = {
            "what_it_learned": "What it learned",
            "directions_to_explore": "Directions to explore",
            "what_to_search_next": "What to search next",
            "what_might_be_overlooked": "What might be overlooked",
        }
        for field, heading in headings.items():
            lines += [f"### {heading}", ""]
            entries = summary.get(field) or []
            if isinstance(entries, list) and entries:
                lines += [f"- {entry}" for entry in entries]
            else:
                lines.append("_(nothing returned for this field)_")
            lines.append("")
        lines += ["<details><summary>Raw JSON</summary>", "", "```json",
                  json.dumps(summary, indent=2, ensure_ascii=False), "```", "", "</details>", ""]
    elif summary_raw is not None:
        lines += ["_Summary call returned an unparseable body; raw response below._", "",
                  "```", summary_raw, "```", ""]
    else:
        lines += ["_Not produced — the interview did not complete._", ""]

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


async def main_async(args: argparse.Namespace) -> int:
    arm_label, prompt_name = ARMS[args.arm]
    settings = get_settings()

    log_file = configure_logging(log_dir="logs", run_label="phase0")
    register_secret(settings.llm_api_key)
    warn_on_missing_keys(settings)
    print(f"Full call trace: {log_file}", file=sys.stderr)

    client = OpenAICompatibleLLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
    )

    min_questions, max_questions, min_dimensions = (
        MIN_QUESTIONS,
        MAX_QUESTIONS,
        MIN_DIMENSIONS,
    )
    if args.smoke:
        min_questions, max_questions, min_dimensions = 1, 2, 1
        print(
            "[smoke mode: floors lowered to 1 question / 1 dimension, cap 2 "
            "— mechanics check only]",
            file=sys.stderr,
        )

    print(f"\n=== Phase 0 — arm {args.arm.upper()} ({arm_label.replace('_', ' ')}) ===")
    print(f"\nOpening statement (fixed for every arm):\n\n  {OPENING_STATEMENT}\n")
    print("Answer as the persona. Ctrl-D or Ctrl-C ends the interview early.")

    if args.arm == "a":
        qa, stop_reason = await run_static_arm()
    else:
        qa, stop_reason = await run_adaptive_arm(
            client,
            interview_system_prompt(prompt_name),
            min_questions=min_questions,
            max_questions=max_questions,
            min_dimensions=min_dimensions,
        )

    if not qa:
        print("\nNo questions were answered — nothing to summarise.", file=sys.stderr)
        return 1

    print(f"\n[{len(qa)} questions answered — {stop_reason}. Producing the summary...]")
    summary, summary_raw = await summarise(client, qa, args.arm)

    path = write_transcript(
        args.arm, arm_label, settings.llm_model, qa, stop_reason, summary, summary_raw
    )
    print(f"\nTranscript written to {path}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one arm of the Phase 0 question-discovery experiment."
    )
    parser.add_argument(
        "--arm",
        required=True,
        choices=sorted(ARMS),
        help="a = static list, b = generic adaptive, c = India-grounded adaptive.",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Mechanics check only: lower the floors to 1 question / 1 dimension "
        "and the cap to 2, so the loop, JSON turn contract, stopping condition, "
        "summary call and transcript writing can be proven cheaply. Never use "
        "this for a real experimental run — the arms are only comparable at the "
        "real 4-10 question / 5-dimension settings.",
    )
    sys.exit(asyncio.run(main_async(parser.parse_args())))


if __name__ == "__main__":
    main()
