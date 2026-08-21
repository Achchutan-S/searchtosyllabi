---
noteId: "e47b96509d8211f1a1588b479177f642"
tags: []

---

# PIVOT_STATUS — question discovery

**Living status document.** Distinct from [RECON_REPORT.md](RECON_REPORT.md), which is
a point-in-time audit of the old pipeline, and from [README.md](README.md), which
describes the archived syllabus-agent system rather than this pivot.

Last updated: 2026-08-21.

---

## The thesis

Search engines are excellent *inside* a possibility space you already know exists,
and useless for the space itself — you cannot query your way to an option you have
never heard of, because the query is made of the vocabulary you already have. A
24-year-old engineering graduate who says "I don't know what my options are" is not
short of search results; they are short of a well-formed question, and every
retrieval system downstream inherits that deficit. This pivot's job is the layer
*before* retrieval: an adaptive conversation that helps someone with a vague goal
construct the space of things worth investigating, ending in a structured
`ResolvedIntent` rather than an answer. It sits **in front of** syllabus-agent's
existing evidence pipeline, not instead of it — stages 2 through 6 (query
generation → source collection → extraction → relevance → structuring/merge) are
expected to be reused largely as-is once Phase 2 begins, with new domain-specific
relevance and structuring prompts. The existing stage 1 classifier is *not* the
seed of this layer: it is one-shot, and confirmed to have no session concept
anywhere in the schema layer (see [RECON_REPORT.md](RECON_REPORT.md) §1).

## Hypothesis status

These are deliberately stated at the confidence the evidence actually supports. Two
of the three rest on a single post-fix run per arm. Nothing here is settled.

### H1 — question discovery beats no question discovery

**No finding either way. Not tested, by design.**

Phase 0 compares three *questioning strategies* against each other; it has no arm
in which no discovery happens at all. Establishing that this layer beats going
straight to retrieval is Phase 2's job, once `ResolvedIntent` actually feeds the
evidence pipeline. Do not read any Phase 0 result as bearing on H1.

### H2 — adaptive questioning beats a static questionnaire

**Mixed, and genuinely open. n=1 per arm since the fix.**

*For static:* arm A gathers broad raw signal reliably. Replicated across both arm A
runs: the fact that the person had enjoyed mechanical engines and was merely *good
at* rather than interested in their CS coursework surfaced both times. But both
times it landed only in `what_might_be_overlooked` and never became a direction —
which is structural, not bad luck. A fixed list cannot act on a signal mid-
conversation; it can only record it and move to the next scripted question.

*For adaptive:* arms B and C can and do act on signals mid-conversation. But the
post-fix round raises a competing concern (see the checklist pattern below): the
adaptive arms may now be trading depth-seeking for breadth-seeking.

The honest summary is that adaptive buys the *ability* to act on a signal, and it
is not yet demonstrated that it reliably *uses* it.

### H3 — India-specific grounding matters

**First real supporting evidence. n=1 since the fix. Encouraging, not established.**

In the post-fix round, arm C named a government-examination-shaped direction, and
did so off a legitimate conversational signal — a stated preference for planning
over debugging — rather than off a demographic guess. Arm B, given comparable
material in its own run, never left tech-corporate territory. Notably, arm C did
this **without fabricating** a specific examination or organisation name, which is
the failure mode the synthesis guardrail was written to prevent.

One run per arm. The obvious confound — that arm C simply got a more forthcoming
conversation — is not yet ruled out.

### The open concern: a possible checklist pattern

In the post-fix round, arms B and C **independently** stopped at exactly 7/7
dimensions, with zero repeat questions, hitting the final three dimensions in the
same order. Arm B also let an evasive answer pass unchallenged ("predictability is
a natural trait everyone likes") rather than following up.

That is consistent with breadth-seeking crowding out depth-seeking — the coverage
requirement being satisfied as a checklist rather than used as a floor. It is also
consistent with two runs of the same model on the same persona simply converging.
**One run per arm is not a pattern.** See the decision rule below.

## Build log

### What `phase0/` is

A standalone, fully additive three-arm CLI experiment. Nothing under
`syllabus_agent/` was modified at any point; the only reuse is
`OpenAICompatibleLLMClient` imported directly, plus `load_prompt("base")` for the
shared JSON-output discipline and the optional redacted call trace.

```
phase0/
  run.py                              python phase0/run.py --arm {a,b,c}
  questions_static.txt                arm A — 11 fixed questions
  prompts/interviewer_generic.txt     arm B system prompt
  prompts/interviewer_india.txt       arm C — generic + {{INDIA_VOCABULARY}} splice
  prompts/india_vocabulary.txt        the vocabulary, single source of truth
  prompts/summary.txt                 shared synthesis task (all arms)
  prompts/summary_india_addendum.txt  arm C only — how to use the vocabulary here
  transcripts/                        gitignored, see Inventory
```

- **Arm A (static):** 11 fixed questions, asked in order. Zero LLM calls during the
  Q&A phase; one call for the final synthesis.
- **Arm B (generic adaptive):** one question per turn, chosen from the whole
  conversation so far.
- **Arm C (India-grounded adaptive):** mechanically identical to B, plus the
  opportunity vocabulary. Verified in code that removing the vocabulary splice from
  C's composed prompt yields B's *exactly* — the vocabulary is the only variable.

All arms end with one structured call producing `what_it_learned`,
`directions_to_explore`, `what_to_search_next`, `what_might_be_overlooked`.

### Bug 1 — the vocabulary never reached synthesis

The vocabulary lived inside `interviewer_india.txt` only, so it was in context while
*asking* questions and absent while *drawing conclusions*. Arm C was half-grounded,
and H3 was not actually being tested end to end: the person mentioned self-directed
quantum research and arm C's synthesis produced generic "quantum computing R&D",
never anything reflecting Indian research bodies.

**Fix.** Extracted the block into `phase0/prompts/india_vocabulary.txt` as the single
source of truth. `interviewer_india.txt` now carries a `{{INDIA_VOCABULARY}}`
placeholder resolved at load time by `interview_system_prompt()` in `run.py`; the
synthesis prompt is composed by `summary_system_prompt(arm)`, which appends the
vocabulary **plus** `summary_india_addendum.txt` for arm C only. The addendum
explicitly forbids inventing specifics — no deadlines, no eligibility cut-offs, no
organisation names absent from the vocabulary — routing anything needing
verification into `what_to_search_next` instead. Arms A and B are byte-identical to
before, so B-vs-C stays a one-variable comparison.

### Bug 2 — the stopping signal was an unverified self-report

Both pre-fix adaptive runs stopped at exactly the bare 4-question floor with narrow,
clustered coverage. Arm B's four questions never left one cluster (stress tolerance,
runway, routine, collaboration style) — never touching further study, government vs
private, or what the person was actually interested in. The prompt asked the model to
stop "once confident across the major dimensions", and nothing checked whether that
confidence was earned.

**Fix.** Replaced the bare-text sentinel with a JSON turn contract. Every turn is now
either `{"dimension": "<tag>", "question": "<text>"}` or `{"done": true}`, sent with
`response_format={"type": "json_object"}` so the existing client's parse-and-retry
covers it. Seven fixed dimensions, listed identically in both adaptive prompts:
`current_status`, `interest_type`, `further_study`, `stability_vs_risk`, `geography`,
`timeline`, `values`. `run_adaptive_arm()` tracks distinct dimensions covered and
accepts `{"done": true}` only when **both** floors are met — at least 4 questions and
at least 5 of 7 distinct dimensions (`MIN_QUESTIONS`, `MIN_DIMENSIONS`). Otherwise it
pushes back naming the untouched dimensions. Repeat questions on an already-covered
dimension are allowed and count as depth, never as breadth. Hard cap raised 8 → 10 as
an absolute backstop regardless of coverage.

Both fixes were verified against stub clients before any real call was spent.

## Decision rule for next time

**Run 2-3 more live sessions each of arms B and C.** Then:

- **If the checklist pattern replicates in most of them** — stopping exactly at the
  dimension floor, zero repeat questions, no follow-up on a vague or evasive answer —
  the next fix is **narrow**: *permit, do not require,* one additional question when
  an answer was vague, even after all seven dimensions are covered. That is a change
  to the stopping condition only. **Do not rebuild the turn contract, change the
  dimension list, or redesign the prompts** — the contract is doing its job; the
  floor is being read as a target.
- **If it does not replicate**, the current build is fine as-is. Stop iterating on
  Phase 0 and move toward Phase 1 (a real adaptive loop producing a structured
  `ResolvedIntent` and a plain-text `SearchSpace`) and then Phase 2.

Either way, resist changing more than one variable at a time — the arms are only
comparable while B and C differ solely by the vocabulary.

## Parked: a known Phase 2 blocker

**`syllabus_agent/utils/trust_scoring.py` will silently delete this pivot's target
sources.** `score_source_with_reason()` returns **0.3** for any domain that is not
`.edu` / `.ac.in` / `.ac.uk` or a known OCW platform, and
`pipeline/source_collection/collect.py` hard-drops anything below
`MIN_TRUST_SCORE = 0.5`. Every `.gov.in`, `.nic.in`, and `.org` source — UPSC, DRDO,
ISRO, RBI, SEBI, ICAI, PSU recruitment portals, i.e. precisely the organisations the
India vocabulary exists to surface — scores 0.3 and never reaches extraction.

A second instance of the same problem: `score_content_richness()` scores on
`Unit III` / `Module 2` / `L-T-P-C` / credit-hour patterns, which a recruitment
notification or examination notice does not have, so anything that *did* survive the
domain filter would still rank last.

Neither is a prompt problem, so "reuse stages 2-6 with new prompts" does not cover
them. Both fixes are small (the domain table and weights are named constants at the
top of the file) but they are **code changes to a module the working pipeline
depends on**, and they must be scoped into Phase 2 explicitly. Out of scope for
Phase 0. Not yet fixed. Full detail in [RECON_REPORT.md](RECON_REPORT.md) §4.

## Inventory — what is actually on disk

| Arm | Transcripts on disk | Which round |
|---|---|---|
| A — static | 1 | post-fix |
| B — generic adaptive | 1 | post-fix (7/7 dimensions, 7 questions, 0 repeats) |
| C — India adaptive | 1 | post-fix (7/7 dimensions, 7 questions, 0 repeats) |

All three live in `phase0/transcripts/`, timestamped `20260821T1643`-`T1650`Z.

**The three pre-fix (first round) transcripts are no longer on disk** — only these
three post-fix ones survive. The first round's findings are recorded in the
hypothesis section above and its call traces still exist in `logs/`, but the
transcripts themselves are gone. Worth knowing before trying to re-read them.

**Status: gitignored, not committed.** This repository is **public**
(`github.com/Achchutan-S/searchtosyllabi`, confirmed via unauthenticated API), and
the transcripts contain a real employer name and specific personal career answers
from the person playing the persona. `phase0/transcripts/` was added to
`.gitignore`; the transcripts remain local-only and were never committed in any
commit. `logs/` was already gitignored, which covers the `phase0_*.jsonl` call
traces — these contain full conversation content and must stay ignored.

## Start here

Read the hypothesis status and the decision rule above, then run
`python phase0/run.py --arm b` and `--arm c` two or three times each, live, playing
the persona for real — the next conclusion needs more than one transcript per arm.
