---
noteId: "883a4e209d6b11f1a1588b479177f642"
tags: []

---

# RECON_REPORT — syllabus-agent, pre-pivot reconnaissance

**Date:** 2026-08-21 · **Branch:** `main` (clean, synced with `origin/main`)
**Scope:** verification only. No code was written, edited, or scaffolded.

---

## 1. Confirmed vs. corrected

### CONFIRMED — six-stage pipeline, one package per stage, typed schemas throughout

The structure is exactly as described. Each stage is a package under
`syllabus_agent/pipeline/` exposing one async entry function:

| # | Package | Entry function | Signature |
|---|---|---|---|
| 1 | `classifier/classify.py` | `classify_subject` | `(subject: str, llm) -> ClassificationResult` |
| 2 | `query_generation/generate.py` | `generate_queries` | `(subject: str, llm) -> QueryGenerationResult` |
| 3 | `source_collection/collect.py` | `collect_sources` | `(subject, queries, search) -> SourceCollectionResult` |
| 4 | `extraction/extract.py` | `extract_sources` | `(subject, sources, *extractors) -> ExtractionResult` |
| 5 | `relevance/assess.py` | `assess_relevance` | `(subject, sources, llm) -> list[RelevanceResult]` |
| 6 | `structuring/structure.py` | `structure_per_source` + `semantic_merge` | `-> CanonicalSyllabus` |

`orchestrator.py:130-176` runs them in that order. Stages pass Pydantic models,
never loose dicts — 17 models across `schemas/`, verified by introspection.
Clients are all behind ABCs in `clients/` (`LLMClient`, `SearchClient`,
`CacheClient`, `BaseExtractor`). **Confirmed as stated.**

### CONFIRMED (emphatically) — no session or conversation concept exists anywhere

**This is the assumption the whole phased plan rests on, and it holds.**

Evidence:

1. **Grep across all of `syllabus_agent/`** for `session|conversat|multi.?turn|history|thread_id|follow.?up|dialogue|stateful|memory` returns **three hits, all irrelevant**: a code comment dated "the last session" in `diagnostics.py:34`, the word "history" in a cache docstring (`cache_client.py:8`), and the word "conversation"-adjacent prose in `classifier.txt:5`. **Zero code matches.**

2. **All 17 schema models enumerated** — no field resembling session, turn, or history state exists on any of them. `PipelineResult` carries exactly: `subject, route, classification, stage_reached, syllabus, error, generated_at, from_cache`.

3. **Route table dumped from the live app object** — exactly **one** user-defined route:
   ```
   {'POST'} /syllabus
   {'GET','HEAD'} /openapi.json  /docs  /docs/oauth2-redirect  /redoc   ← FastAPI built-ins
   ```
   `build_syllabus()` (`main.py:73`) constructs fresh client instances per request and returns one `PipelineResult`. Nothing is retained between requests. Strictly one-shot.

4. **The cache is not state.** `MongoCacheClient` is keyed on the normalised *subject string* and stores a whole `PipelineResult`. It is a memoisation layer, not a session store.

**One nuance worth knowing** (not a correction, but relevant): the classifier
already emits `clarifying_question` and `suggested_refinements` on the
`needs_clarification` route (`schemas/classification.py`, `prompts/classifier.txt`).
That is a *single-shot* clarification — the question is returned to the caller and
the run ends; nothing consumes an answer. It is the closest thing in the repo to
the new layer's idea, and it confirms the instinct was already there, but it is
**not** a loop and cannot be extended into one without adding state. Building the
adaptive layer as a separate stateful component remains the right call.

### CORRECTED — the configured model is not the README's default

- README (line 336, and the config table) discusses `gemini-3.6-flash` and its 20/day cap.
- **Actual `.env`: `GEMINI_MODEL=gemini-3.5-flash-lite`.**

`config.py:23` still *defaults* to `gemini-3.6-flash`, so the README's default is
correct as a code fact — but the machine is running flash-lite. Any quota
reasoning carried over from the README is about a different model than the one
that will actually run.

### CORRECTED — the test-count claim is right, but the "skips itself" framing is misleading today

README: "101 passing; live-API tests skip themselves without keys."

Actual `pytest`: **101 passed, 0 skipped, 0 failed** (6.58s). The count matches
exactly. But because keys *are* present, the live tests **ran for real** — verified
by running them in isolation: `tests/test_llm_client_live.py
tests/test_search_client_live.py` → **3 passed in 1.74s**, including one real
Gemini `chat_completion` and one real Tavily search.

**Practical consequence: every `pytest` run silently spends 1 LLM call and 1
Tavily call.** That matters on a day you are budgeting quota for experiments.
Deselect with `pytest --ignore=tests/test_llm_client_live.py --ignore=tests/test_search_client_live.py`.

### CONFIRMED — repo freshness

```
b77ca2f  2026-08-21 17:22  Readme updated      ← today, docs only
d30a1d3  2026-08-09 18:16  Adding cache
1e0468d  2026-08-09 16:35  Initial commit - logical conclusion
```

`git status`: **clean**, `main` in sync with `origin/main`, no other branches.
Only three commits total. **Code is 12 days untouched** (last code change
2026-08-09); today's commit is README prose only. Nothing uncommitted, nothing
in flight. `.env` is correctly gitignored and untracked.

---

## 2. What's reusable as-is for Phase 0

### `OpenAICompatibleLLMClient` — YES, usable directly, nothing to strip

I traced the import graph. `clients/llm_client.py` imports **only** stdlib +
`httpx` + `pydantic`, plus exactly one project module:

```python
from syllabus_agent.logging_setup import current_stage, record_call
```

And `logging_setup.py` itself imports **stdlib only** — no FastAPI, no config, no
orchestrator, no pipeline. So the whole dependency closure for the LLM client is
two files.

Both borrowed functions are **safe no-ops when unconfigured**:

- `record_call()` opens with `if _run_file is None: return` (`logging_setup.py:189`). Without calling `configure_logging()`, tracing is simply off. No file is created, nothing raises.
- `current_stage()` reads a `ContextVar` with `default="unknown"` (`logging_setup.py:52`). Never raises without a `stage_context()` block.

**The constructor takes plain values, not a `Settings` object:**

```python
OpenAICompatibleLLMClient(base_url=..., api_key=..., model=..., timeout=30.0)
```

So a throwaway script needs `Settings` only if it *wants* `.env` loading — it can
equally pass literals or `os.environ`. Nothing about the class is coupled to this
project.

**Verdict: zero stripping required.** A bare Phase 0 script can import the client,
instantiate it, and loop `await client.chat_completion([...])`. You get the
retry/backoff, the `Retry-After` handling, the `DailyQuotaExhausted` distinction,
and the ```` ```json ```` fence-stripping for free — all of which you would
otherwise rewrite badly.

**Two things you inherit that you should know about:**

- `chat_completion(messages, response_format=...)` — when `response_format` is passed, the client **validates `json.loads()` itself** and *retries* on a parse failure (`llm_client.py:186`). Good for a `ResolvedIntent` extraction call. For free-form conversational turns, pass `response_format=None` and you get the raw string back.
- Default `temperature=0.2`. Fine for classification; you will likely want it higher for question generation, and it is a per-call keyword argument, so that's free.

**Optional, and worth it:** calling `configure_logging(run_label="phase0")` plus
`register_secret(key)` at the top of the script gives you a full JSONL transcript
of every turn — request, response, timing, keys redacted — for free. For an
experiment whose entire output is "which questioning strategy was better," having
the complete call trace on disk to re-read later is close to essential. Two lines.

### Prompt composition — trivially extensible, use it

`prompts/__init__.py` is 30 lines. `load_prompt(name)` reads
`prompts/<name>.txt`, strips it, and prefixes `base.txt` separated by a blank
line, `lru_cache`d. `load_prompt(name, with_base=False)` skips the prefix.

`base.txt` is **pure JSON-output discipline** — "return one raw JSON object and
nothing else, no fences, no markdown inside values, no extra keys, use null
rather than inventing placeholders."

Each stage prompt then follows the same three-part shape (verified against
`classifier.txt` and `relevance.txt`):

```
Task: <role + what to decide>
<enumerated categories or criteria, each with a one-line definition and an example>
<field-by-field notes: what confidence means, what reasoning means>
Schema:
{"field": type, ...}
```

**Important for Phase 0:** `base.txt` is the wrong base for a *conversational*
turn — it forbids prose and demands a bare JSON object, which is the opposite of
what an interviewer that asks one question per turn should emit. Use
`with_base=False` for the conversational prompts, and keep `with_base=True` for
the structured extraction step that produces `ResolvedIntent` at the end. Adding
new prompts is just dropping `.txt` files in the directory — no registration.

### Model/provider split — already trivial, no new plumbing

`Settings` (`config.py`) has three flat fields (`gemini_api_key`,
`gemini_base_url`, `gemini_model`) exposed through provider-agnostic properties
(`llm_api_key`, `llm_base_url`, `llm_model`). More importantly, **the client
constructor takes the three values directly** — it never reads `Settings` itself.

So a cheap conversational model + a stronger judge model is:

```python
cheap  = OpenAICompatibleLLMClient(base_url=s.llm_base_url, api_key=s.llm_api_key, model="gemini-3.5-flash-lite")
strong = OpenAICompatibleLLMClient(base_url=s.llm_base_url, api_key=s.llm_api_key, model="gemini-3.5-flash")
```

Two instances, one `Settings`, zero new plumbing. Different *providers* is equally
easy — pass a different `base_url` and `api_key`; the client is written against
the OpenAI-compatible shape, not a vendor SDK. Note `get_settings()` is
`lru_cache`d, so two `Settings` instances pointed at different `.env` files is the
*awkward* path; two client instances is the clean one. **This is a genuine
strength — quotas are per-model, so splitting models also splits the quota budget.**

---

## 3. Today's real cost/quota picture

`python -m syllabus_agent.cli doctor` → **PASS, exit 0**, run just now:

```
Environment
  ✓ GEMINI_API_KEY         AQ.A...PdAQ
  ✓ TAVILY_API_KEY         tvly...sytY
  ✓ GEMINI_MODEL           gemini-3.5-flash-lite
  ✓ GEMINI_BASE_URL        https://generativelanguage.googleapis.com/v1beta/openai
Model listing
  ✓ 51 models listed; configured 'gemini-3.5-flash-lite' is present
Configured model probe (gemini-3.5-flash-lite)
  ✓ gemini-3.5-flash-lite   200 OK — callable
Search provider
  ✓ Tavily search           200 OK — 1 result(s)
Cache
  ✓ MongoDB cache           ping OK — mongodb://localhost:27017, db=syllabus_agent, TTL 30d
```

**Everything is green right now.** Local MongoDB is actually running. Both keys
are live. The API key is an `AQ.Ab8...` OAuth-style credential, not a classic
`AIza...` key — worth noting only because README examples show the `AIza` form.

**A caveat you should hold onto:** `doctor` proves the *next* call succeeds. It
cannot report *remaining* daily budget — Gemini exposes no such counter, and the
probe just makes one real call. So "callable now" is not "N calls left today."
Treat the green as a floor, not a balance.

### Is three ~10-15-turn conversations feasible tomorrow?

Arithmetic, since the pipeline's own trace gives us a calibrated reference. The
captured run in `docs/example-run/trace.jsonl` — which I re-counted rather than
trusting the README — is **80 records, 42 of them LLM calls**:

| stage | records | type |
|---|---|---|
| classifier | 1 | llm |
| query_generation | 1 | llm |
| source_collection | 6 | search |
| extraction | 32 | extraction |
| relevance | 35 | llm |
| structuring | 4 | llm |
| merge | 1 | llm |

README's "42 LLM calls" is **accurate**. Phase 0 is much cheaper than that:

| Phase 0 arm | Turns | LLM calls |
|---|---|---|
| A — static ~10-question list | 10 | **0** (the list is fixed; only the persona's answers cost anything) |
| B — generic adaptive | ~12 | ~12 (one per question chosen) |
| C — India-vocabulary adaptive | ~12 | ~12 |
| Blind judging | — | ~3-6 |

**If you answer as the persona yourself: ~30 calls total. Comfortably feasible in
one day, on one model.**

**If an LLM plays the persona too, it roughly doubles to ~60-70 calls** — that is
in the same range as two full pipeline runs, and it is where the README's "20/day
per model" ceiling would bite *if* flash-lite shared flash's cap.

**Three mitigations, in order of preference, all already available:**

1. **Play the persona yourself.** Halves the cost *and* is methodologically better — the whole experiment is about whether the questions surface things *you* didn't know, and an LLM persona cannot exhibit genuine surprise. This is the recommendation.
2. **Split models across arms.** Quotas are per-model. Run arm B on `gemini-3.5-flash-lite` and arm C on `gemini-3.5-flash` (both confirmed callable by this key per `diagnostics.py:38`), and each gets an independent budget. Judge on the third.
3. **`python -m syllabus_agent.cli doctor --probe-models`** when something 429s — it distinguishes per-minute (recoverable, reports the delay) from per-day (won't recover today) and prints a concrete "set GEMINI_MODEL to X" suggestion.

**Also: don't run bare `pytest` on experiment day** — it spends a real LLM call and
a real Tavily call, per §1.

**Bottom line: yes, feasible tomorrow, on the free tier, with nothing changed** —
provided you play the persona rather than simulating it. If you want a simulated
persona, split the arms across two models first.

---

## 4. Repo realities that should change the phased plan

### ⚠ The biggest one: Phase 2's "reuse stages 2-6 essentially as-is" is optimistic — **stage 3 will silently delete your entire corpus**

This is the finding most likely to cost you a week if it isn't caught now.

`utils/trust_scoring.py:118-130`, `score_source_with_reason()`:

```python
if domain in TRUSTED_OCW_DOMAINS:              # ocw.mit.edu .95, nptel.ac.in .9, edx .85, coursera .75
    return TRUSTED_OCW_DOMAINS[domain], ...
if domain.endswith(KNOWN_UNIVERSITY_SUFFIXES): # (".edu", ".ac.in", ".ac.uk")
    return 0.8, ...
if url.lower().endswith(".pdf"):
    return 0.55, "PDF from an unrecognised domain"
return 0.3, "not a .edu/.ac.in/.ac.uk or known OCW domain"
```

And `source_collection/collect.py:23`: `MIN_TRUST_SCORE = 0.5`, applied as a hard
drop at line 47.

**Consequence for a career/question-discovery pivot:** the authoritative sources
for exactly the India-specific vocabulary Phase 0 is designed to test —
`upsc.gov.in`, `drdo.gov.in`, `isro.gov.in`, `sebi.gov.in`, `rbi.org.in`,
`icai.org`, PSU recruitment portals, regulator sites — are `.gov.in` / `.org` /
`.nic.in`. **Every one of them scores 0.3 and is dropped before extraction.**
Non-PDF pages on those domains never reach the pipeline at all.

This is not a prompt problem, so the plan's "except for new domain-specific
relevance/structuring prompts" does not cover it. It is a hardcoded domain table
plus a threshold. The fix is small — extend `TRUSTED_OCW_DOMAINS` /
`KNOWN_UNIVERSITY_SUFFIXES` or make them injectable — but **it must be scoped into
Phase 2 explicitly**, and it is a code change to a module currently used by a
working pipeline.

### ⚠ Related: `content_richness_score()` is syllabus-shaped and will misrank career sources

Same file, lines 137-200. The signal set is: `Unit III` / `Module 2` / `Week 7` /
`Chapter 4` regexes (40% weight), `L-T-P-C` and "credit hours" (15%), and
comma/semicolon density (20%). A DRDO recruitment page or a UPSC exam notification
has **none** of these and will score near zero, so even if it survives the domain
filter it will lose every ranking contest to a university PDF. Add to Phase 2 scope
alongside the domain table — the sub-signals are named constants at the top of the
file and the structure is clean, but the *content* of the heuristic is
subject-specific in a way the plan doesn't currently account for.

### ✓ Good news: the Phase 2 seam is cleaner than expected

`generate_queries(subject: str, llm)` takes a **plain string**. So connecting
`ResolvedIntent` is "render the intent to a query-seed string" — no signature
change to stage 2, no schema surgery. And `orchestrator.py` is already the single
composition point: `run_pipeline()` takes every client by keyword argument
(`llm=`, `search=`, `html_extractor=`, ..., `cache=`), so a variant orchestrator
that skips stage 1 and enters at stage 2 is a new function next to the existing
one, not a modification of it. **Phase 2's wiring is genuinely easy; only the
scoring heuristics are hard.**

### ✓ Good news: the relevance stage is the transferable idea, and it's cheap to re-aim

`relevance/assess.py` is the most valuable piece for the pivot and the most
portable: it is a `Semaphore(4)`-bounded `asyncio.gather` over one small LLM call
per source, capped at `MAX_TEXT_CHARS = 3_000` of input. Everything
subject-specific lives in `prompts/relevance.txt`, not the code. Re-aiming it at
"is this document about *this career path*?" really is a prompt swap, exactly as
the plan assumes. It also has a genuinely well-chosen failure mode worth
preserving: an unparseable response defaults to `partial_match` rather than
dropping the source (line 122), so a model hiccup can't silently shrink the corpus.

### ✓ Good news: the cache generalises to Phase 0/1 for free

`CacheClient` is a two-method ABC (`get(subject)` / `set(subject, result)`) over
any Pydantic model, and Mongo is confirmed running locally. Every failure degrades
to a miss rather than raising. If Phase 1 wants to persist `ResolvedIntent`
objects, the pattern is already here and already tested (`tests/test_cache.py`).

### ⚠ Minor: extraction is sequential, and that will hurt more later, not less

README admits this and it checks out — `asyncio.gather` appears only in the
relevance stage. Stage 4 fetches ~30-40 URLs one at a time and dominates
wall-clock. Not a Phase 0 or Phase 1 concern at all, but if Phase 2 widens the
source pool to cover a broader career-information space, this becomes the
bottleneck before anything else does.

### ⚠ Cosmetic: `main.py:39` uses `@app.on_event("startup")`, deprecated in current FastAPI

Works today, emits a `DeprecationWarning`, will need the `lifespan` handler
eventually. Irrelevant to Phase 0/1 (no FastAPI involved); a five-line fix
whenever Phase 4 revisits the API.

---

## 5. Recommended minimal layout for the Phase 0 experiment

Keep Phase 0 **entirely outside the package** — a single top-level `phase0/`
directory, gitignored or committed as you prefer, importing only
`syllabus_agent.clients.llm_client` and `syllabus_agent.config`. Nothing under
`syllabus_agent/` should change, which keeps the 101 tests green and the pivot
reversible. Concretely: `phase0/prompts/interviewer_generic.txt` and
`phase0/prompts/interviewer_india.txt` as plain files read directly (**not** via
`load_prompt()` — you want conversational prose, and `base.txt` forbids exactly
that), `phase0/questions_static.txt` holding arm A's fixed ~10 questions,
`phase0/run.py` as one script taking `--arm {a,b,c}` that builds a single
`OpenAICompatibleLLMClient` from `get_settings()`, calls `configure_logging(run_label="phase0")`
and `register_secret()` so every turn lands in a redacted JSONL trace, then loops:
print the model's question, read your answer from `input()`, append both to a
plain `list[ChatMessage]`, and pass the **whole accumulated list** back on each
turn (that list *is* the "state" the pipeline lacks — for three throwaway
conversations it needs to be nothing more than a Python list). Append each
finished transcript to `phase0/transcripts/<arm>_<timestamp>.md`. That is four
prompt/data files plus one script, roughly 80 lines, no new dependencies, and it
produces exactly the three blind-comparable transcripts the experiment needs —
with the JSONL trace as the audit trail if a result surprises you.
