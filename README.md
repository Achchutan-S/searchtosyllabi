---
noteId: "349425c0927111f186e3991dcdee008a"
tags: []

---

# syllabus-agent — Layer One

Search-and-structure pipeline: given a subject (e.g. "data structures"), classify
it, search the web for how top universities teach it, extract raw syllabus text
from HTML and PDFs, filter out documents that aren't about that course, and merge
what survives into one canonical syllabus.

> **Status: archived portfolio piece.** The core idea is validated and the
> pipeline runs end-to-end against live APIs, but this is not maintained as a
> production system. See **[Current State & Known Issues](#current-state--known-issues)**
> below for an honest account of what works and what doesn't.

This is **layer one only** — the search-and-structure half. Content/lecture-note
generation and persistence were designed for but never built; see
[Phase status](#phase-status).

## Snapshot

| | |
|---|---|
| **What it is** | A 6-stage async pipeline (classify → query → collect → extract → relevance → structure+merge) behind a CLI and a one-endpoint FastAPI app |
| **Language / runtime** | Python 3.11+ (developed on 3.12), `asyncio` throughout |
| **Stack** | FastAPI + uvicorn, Pydantic v2 + pydantic-settings, httpx, BeautifulSoup, PyMuPDF, pytesseract + Pillow, motor (MongoDB), pytest + pytest-asyncio |
| **External services** | An OpenAI-compatible LLM endpoint (Gemini by default), Tavily search, and optionally MongoDB for the result cache. Tesseract is a system binary |
| **Entry points** | `python -m syllabus_agent.cli "<subject>"`, `python -m syllabus_agent.cli doctor`, `uvicorn syllabus_agent.main:app` |
| **Tests** | 101 passing; live-API tests skip themselves without keys |
| **Config** | `.env` only — 7 settings, all with defaults except the two API keys ([`.env.example`](.env.example)) |
| **Output** | One `PipelineResult` envelope for every route, with full provenance: per-source structures, the blended ranking table, and per-topic source URLs |
| **State of play** | Feature-complete for layer one, archived rather than maintained. Last verified end-to-end 2026-08-09 (see [AUDIT.md](AUDIT.md)) |

## Current State & Known Issues

**This section is the single source of truth for what works today.** Full
detail — including the security and dead-code audit — is in **[AUDIT.md](AUDIT.md)**.

**→ [See a real run](docs/example-run/index.html)** — a complete captured execution
rendered as a static report: classification, trust ranking, extraction and relevance
breakdowns, and the final syllabus. Nothing live, nothing mocked.

### What genuinely works

Every stage runs against live APIs. **No stubs or `TODO` placeholders remain
anywhere in the codebase.** Verified by live runs, not assumed:

| Stage | Evidence it's real |
|---|---|
| **Classifier / router** | Live 4-way routing with reasoning; correctly sent "business management" down the `needs_clarification` path |
| **Query generation** | Expands one subject into 5–8 queries, using `site:.edu` and `filetype:pdf` operators unprompted |
| **Source collection** | Tavily search → ~30–40 candidates, deduped and domain-filtered |
| **Extraction** | BeautifulSoup + PyMuPDF + pytesseract OCR. A representative run: `39/41 sources produced text`. OCR verified on a genuinely scanned Indian university course file |
| **Relevance filter** | Catches documents about the *field* rather than the *course* — correctly classified a full CS degree catalog as `partial_match` |
| **Trust ranking** | Blends domain reputation with a local content-richness heuristic; demoted MIT OCW admin pages from the top 4 to ranks 10–21 |
| **Structuring** | Extracts each source's own structure — one run yielded 27, 1, 4 and 5 units from four sources, no shape imposed |
| **Semantic merge** | Single LLM call grouping topics by meaning with per-topic provenance; visibly excludes off-topic material in its `merge_notes` |
| **Result cache** | Real MongoDB read/write in front of the pipeline; a repeat subject is served in milliseconds with zero LLM calls, and an unreachable Mongo degrades to a miss rather than an error |
| **`doctor` diagnostics** | Distinguishes 404 / per-minute 429 / per-day 429; scriptable exit codes |
| **Observability** | Per-run JSONL trace of every external call, with API keys redacted (verified: zero occurrences) |
| **Provider swappability** | Swapped across four Gemini models with zero code changes — `.env` only |

### What doesn't work well

- **Output quality is inconsistent.** A recent "data structures" run produced 6
  thematically-correct units and 22 topics — reasonable. Earlier runs produced 21
  units contaminated with other courses. The relevance filter and semantic merge
  fixed the contamination, but topic coverage still varies run to run with which
  sources the search happens to surface.
- **Free-tier economics don't work.** ~40–50 LLM calls per run (42 in the
  [captured run](#what-a-run-actually-costs), most of them relevance) against a ~20/day
  per-model cap is 2–3 runs per day. Three models were exhausted in one working
  session. The original "free education for everyone" goal needs caching or a
  more generous provider.
- **~85% of extraction work is discarded.** Ranking and relevance both need
  extracted text, so all ~30–40 sources are fetched and parsed, but only the top
  4 are ever structured.
- **Extraction is sequential, not concurrent** — it fetches URLs one at a time,
  which dominates wall-clock. (`asyncio.gather` is used, but only in the
  relevance stage.)
- **Classifier verdicts vary by model.** "business management" routes differently
  on `gemini-3.5-flash` vs `gemini-3.5-flash-lite`. Neither is wrong; routing is
  model-sensitive and unpinned.
- **Non-English content degrades.** Tesseract has only `eng` installed and all
  prompts are English; such sources rank low and get filtered rather than crash.

### Phase status

Against the original five-phase plan ([`syllabus-agent-idea-doc.md`](syllabus-agent-idea-doc.md)):

| Phase | Scope | Status |
|---|---|---|
| **1 — Layer One** | Search & structuring pipeline | **Built and working** |
| **2 — Storage** | MongoDB, caching, versioning, freshness | **Caching built** (TTL-based, see [Caching](#caching)); no versioning |
| **3 — Content generation** | Per-topic "AI professor" lecture notes | Not started |
| **4 — Delivery** | Full API + React frontend | One endpoint; no frontend |
| **5 — Extensions** | Quizzes, progress tracking, Q&A | Not started |

### Still-open deviations from the original design

| Design doc | Intended | Actual |
|---|---|---|
| §4.4 | Trust = reputation + **recency** + structural completeness | No recency signal at all |
| §4.0 | `general_knowledge` route gets its own explainer template | Short-circuits with no downstream template |
| §4.0 | Time-sensitive subjects flagged (e.g. amended legal codes) | No freshness field in any schema |
| §2 | Canonical target of 4 units × 15–20 topics | Unit count is organic; no target enforced |

*(Two earlier deviations — the positional merge and discarded per-source
structures — have since been fixed.)*

## Architecture

Six sequential, independently-testable stages, each a package under
`syllabus_agent/pipeline/` exposing one async entry function. Stages pass typed
Pydantic models (`syllabus_agent/schemas/`), never loose dicts.

```
subject
  │
  ▼
1. classifier          classify_subject(subject, llm) -> ClassificationResult
  │  route == genuine_academic_subject?
  │     no  → short-circuit, return ClassificationResult (+ clarifying_question if applicable)
  │     yes ↓
2. query_generation     generate_queries(subject, llm) -> QueryGenerationResult
  ▼
3. source_collection     collect_sources(subject, queries, search) -> SourceCollectionResult
  ▼
4. extraction            extract_sources(subject, sources, ...extractors) -> ExtractionResult
  ▼
5. relevance             assess_relevance(subject, sources, llm) -> list[RelevanceResult]
  │  keeps direct_match + partial_match; errors out if nothing survives
  ▼
6. structuring           structure_per_source(subject, extraction, llm) -> [PerSourceStructure]
  ▼
   merge                 semantic_merge(subject, structures, llm) -> CanonicalSyllabus
```

`syllabus_agent/pipeline/orchestrator.py` runs these in order and short-circuits
right after classification for any route other than `genuine_academic_subject`.
It owns the two pieces of glue that don't belong to any stage: the result
[cache](#caching) wraps the whole run (so a hit returns before stage 1), and
`apply_relevance_filter()` translates stage 5's verdicts into a filtered
`ExtractionResult` — dropping `field_level`/`unrelated` blocks and stamping
`relevance_penalty` on what remains — so stage 6 stays unaware that relevance
exists.

### Why classification routes to four outcomes

- `genuine_academic_subject` — proceeds through the full pipeline.
- `general_knowledge` — a real topic, but no formal syllabus exists for it.
- `needs_clarification` — too broad/jurisdiction-specific (e.g. "law", "engineering").
- `rejected_non_academic` — not an academic subject at all.

Only the first route runs the remaining stages; the other three return the
`ClassificationResult` directly (`PipelineResult.stage_reached ==
"classification"`, `syllabus == null`).

## External dependencies are behind thin interfaces

Everything that talks to the outside world lives in `syllabus_agent/clients/`
behind an abstract base class, so it's swappable and mockable:

- `llm_client.py` — `LLMClient` ABC, built against the **OpenAI-compatible
  chat-completions shape**, not a vendor SDK. `GeminiOpenAICompatClient` is the
  concrete implementation; `Settings` holds `base_url` + `model` + `api_key`, so
  swapping to Groq, OpenRouter, local Ollama, or Claude later is a `.env` change,
  not a code change.
- `search_client.py` — `SearchClient` ABC; `TavilySearchClient` is the concrete
  implementation for now.
- `cache_client.py` — `CacheClient` ABC (`get`/`set` a whole `PipelineResult` by
  normalised subject); `MongoCacheClient` is the concrete implementation. See
  [Caching](#caching).
- `extraction_client.py` — `HtmlExtractor` (BeautifulSoup), `PdfTextExtractor`
  (PyMuPDF), `PdfOcrExtractor` (PyMuPDF render + pytesseract OCR fallback for
  scanned PDFs), plus a `detect_format()` helper.

### Current wiring state

| Client | State |
|---|---|
| `llm_client.py` (`OpenAICompatibleLLMClient`) | **Live.** Real async httpx POST to `{base_url}/chat/completions`, 30s timeout, 3 attempts, honours `Retry-After` (and Gemini's "retry in Ns" 429 body), strips ```` ```json ```` fences, logs status + body on failure. |
| `search_client.py` (`TavilySearchClient`) | **Live.** Real POST to `api.tavily.com/search` with `search_depth="basic"` to conserve free-tier credits. Same retry/timeout/logging pattern. |
| `cache_client.py` (`MongoCacheClient`) | **Live.** Real motor/MongoDB, one document per normalised subject, `serverSelectionTimeoutMS=3000` so a missing server is cheap to discover. Every failure degrades to a cache miss rather than raising. |
| `extraction_client.py` (HTML / PDF / OCR) | **Live.** Real `httpx` fetch with the same retry/timeout shape; BeautifulSoup for HTML (chrome stripped, table cells preserved with `\|` separators so LTPC rows survive); PyMuPDF per page with `[page N]` markers; automatic pytesseract OCR fallback when the text layer is too sparse. CPU-bound parsing runs via `asyncio.to_thread`. |

There are no stubs left anywhere in the pipeline.

### Extraction specifics

- **Caps**: 30 PDF pages, 10 OCR pages (OCR is ~25s/document), 20k chars per block, 40k per source — a course catalog can run to 654 pages, and uncapped text would blow up the structuring prompt and its token cost.
- **OCR trigger**: mean chars-per-page below `MIN_CHARS_PER_PAGE` (100). Verified live against a scanned Indian university course file, which OCR recovered at 6,878 chars.
- **Per-source failures** are recorded on `ExtractionResult.failures` with the error and the method attempted; one bad source never aborts a run. Because structuring ranks by trust *after* extraction, a failed top-ranked source simply drops out of the pool and the next-best takes its slot.
- **`pre_extracted_content` fast path**: used only when the search provider's text is at least `MIN_PRE_EXTRACTED_CHARS` (2,000). Measured live, Tavily's `content` is a search *snippet* — median 918 chars, ceiling ~1,500, versus ~37k for a fetched PDF. Accepting it would trade the syllabus body for a blurb, so below the threshold the page is fetched properly. In practice this means the fast path rarely fires with Tavily; it exists for providers that return real page text.

There are no silent fallbacks left. If the LLM returns unparseable JSON, the
stage logs the raw response and raises — a wrong default here would send a
subject down the full pipeline and hide the real cause.

### The relevance filter answers a question scoring can't

`pipeline/relevance/assess.py` runs between extraction and structuring and asks
one thing per source: *is this document about **this course**, or about the whole
field?* Neither domain reputation nor content richness can tell the difference — a
university's full CS degree catalog is `.edu`, long, densely sectioned and
credit-bearing, so it outranked real syllabi and produced a "data structures"
syllabus containing units named "Introduction to Robotics" and "Web Development".

| Aspect | Behaviour |
|---|---|
| Cost | One LLM call per extracted source (the dominant term — see [What a run actually costs](#what-a-run-actually-costs)) |
| Input size | First `MAX_TEXT_CHARS` (3,000) of the extracted text — enough for title, headers and first unit without paying for a 40k-char PDF body |
| Concurrency | `asyncio.gather` behind a `Semaphore(MAX_CONCURRENT_ASSESSMENTS)` = 4 |
| Optional cap | `MAX_SOURCES_TO_ASSESS` (currently `None` — assess everything) |

Four verdicts (`RelevanceVerdict`), of which two survive:

| Verdict | Meaning | Effect |
|---|---|---|
| `direct_match` | A syllabus/outline for exactly this course | Kept, no penalty |
| `partial_match` | Contains a section about this course among other things | Kept, demoted ×`PARTIAL_MATCH_TRUST_MULTIPLIER` (0.7) |
| `field_level` | The broader field, or a related but different course | Dropped |
| `unrelated` | Not about this subject at all | Dropped |

**The demotion is kept separate from trust, deliberately.** A partial match is
recorded on `RawTextBlock.relevance_penalty` (via `relevance_multiplier()`) and
multiplied into the blended score **for ranking and selection only** — the trust
score handed to the merge prompt stays unpenalised. Folding the two together meant
no partial-match source could ever clear the merge prompt's `trust >= 0.7`
single-source threshold, so its topics were silently dropped. `SourceRanking`
carries both numbers so the blend can be audited.

**If nothing survives, the run errors rather than inventing a syllabus.** Zero
surviving sources returns a `PipelineResult` with `stage_reached=relevance` and an
explanatory `error`, never an empty `CanonicalSyllabus`.

### Trust ranking uses two signals, not one

Sources are ranked by a blend of **domain reputation** and **content richness**:

```
blended = DOMAIN_WEIGHT * domain_reputation + CONTENT_WEIGHT * content_richness   # 0.5 / 0.5
```

**Why the second signal exists.** Domain reputation alone is actively
misleading. In a live run for "data structures", `ocw.mit.edu` course-*admin*
pages scored 0.95 and took all four structuring slots — but their text is
"Course Meeting Times… Recitations… Instructors", with no curriculum in it. The
structuring LLM correctly returned `{"units": []}` for every one, and the run
produced an empty syllabus, while a 37,000-character PDF holding a real 12-unit
breakdown sat unused at 0.8.

`content_richness_score()` in `utils/trust_scoring.py` is a local, no-API-cost
heuristic over already-extracted text:

| Signal | Effect |
|---|---|
| Length | Near 0 below 500 chars (a snippet cannot hold a unit breakdown), rising to a cap at 8k — length is evidence, not a prize |
| Sectioning | `Unit III` / `Module 2` / `Week 7` / `Chapter 4`, rewarding several *distinct* vocabularies plus repeated hits |
| Credits | `L-T-P-C`, `credits`, `lecture hours` |
| Topic density | Comma/semicolon density — syllabus topic lists are separator-heavy prose |
| Admin penalty | `meeting times`, `office hours`, `instructor:`, `recitation` near the top of *short* text; scaled by shortness so a long real syllabus mentioning office hours is not punished |

Every sub-signal is recorded on the result and logged at DEBUG, so the heuristic
is tunable from evidence rather than guesswork. Weights are named constants at
the top of the file.

After the change, the same "data structures" run demoted the MIT admin pages to
ranks 10–21 (blended 0.475–0.554, content 0.00–0.16) and promoted content-rich
PDFs into the top 4. `CanonicalSyllabus.source_ranking` carries the full table —
domain, content, blended, extracted chars, and whether each source was used.

**Sequencing note.** This needs extracted text, so ranking must run after
extraction. It already did: `extract_sources()` receives *all* collected sources
and the top-N cutoff lives inside `structure_per_source()`, so no pipeline
restructuring was required.

### Structuring is capped at the top-N trust-ranked sources

Per the design intent (idea doc §4.4), a canonical syllabus is synthesised from
the **top 3-4 ranked syllabi**, not from every source that survives filtering.
`structure.py` sorts collected sources by `trust_score` descending and slices to
`TOP_N_SOURCES_TO_STRUCTURE` (currently 4) *before* making any LLM call.

Sources below the cutoff aren't silently dropped — they're reported on
`CanonicalSyllabus.collected_not_structured`, separate from `source_urls` /
`per_source_structures` (the ones actually structured and merged), so you can
audit what the pipeline saw versus what it used. `source_ranking` carries the
whole table either way, with a `structured` flag per row.

A secondary benefit is cost/quota: a real run collects ~38 sources, so
structuring all of them meant ~38 LLM calls and a guaranteed HTTP 429.

### What a run actually costs

The cap above bounds *structuring*, not the run. Relevance assessment is one LLM
call per extracted source, and that term now dominates:

```
1 (classify) + 1 (query generation) + S (relevance, one per extracted source)
             + N (structuring, N=4) + 1 (merge)
```

The captured run in [`docs/example-run/`](docs/example-run/index.html) is the
reference figure — **80 trace records** for "data structures":

| Stage | Records | Call type |
|---|---|---|
| `classifier` | 1 | `llm` |
| `query_generation` | 1 | `llm` |
| `source_collection` | 6 | `search` (one per generated query) |
| `extraction` | 32 | `extraction` (HTTP fetches) |
| `relevance` | 35 | `llm` (30 sources + 5 retries) |
| `structuring` | 4 | `llm` |
| `merge` | 1 | `llm` |

That is **42 LLM calls**, which is where the "~50 calls per run" figure in
[What doesn't work well](#what-doesnt-work-well) comes from — and why a 20/day
per-model free tier yields 2–3 runs. `MAX_SOURCES_TO_ASSESS` in
`pipeline/relevance/assess.py` exists to cap the relevance term (currently
`None`, i.e. uncapped); the [cache](#caching) is the other half of the answer.

### Free-tier quota notes

Gemini enforces two separate free-tier caps, both surfaced as HTTP 429, and the
model you pick matters enormously:

- **Per-minute** (`GenerateRequestsPerMinutePerProjectPerModel-FreeTier`) — ~5/min. A
  ~42-call run trips this repeatedly, mostly during the relevance fan-out; the client
  reads the provider's `Retry-After` and waits. `MAX_CONCURRENT_ASSESSMENTS` is held at
  4 for the same reason — a wider fan-out converts into 429s and backoff, not speed.
- **Per-day** (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`) — this is the one
  that bites. `gemini-3.6-flash` allows only **20 requests/day**, i.e. ~3 pipeline
  runs. The client raises `DailyQuotaExhausted` immediately rather than retrying,
  because the provider still sends a short `retryDelay` (~17s) that will never clear a
  daily cap.

Quotas are per-model, so switching `GEMINI_MODEL` gets you a fresh budget. Note
that several older models (`gemini-2.5-flash`, `gemini-2.5-flash-lite`) return
404 "no longer available to new users" even though they appear in `GET /models`.
`gemini-3.5-flash` and `gemini-3.5-flash-lite` both work.

## Caching

A MongoDB result cache sits **in front of** the pipeline, in the orchestrator.
Re-running a subject that has already been generated returns the stored result
without making a single LLM call, which is the difference between ~3 demo runs
per day and an unlimited number of them.

```bash
python -m syllabus_agent.cli "compiler engineering"                  # miss — full pipeline
python -m syllabus_agent.cli "compiler engineering"                  # hit — instant, 0 LLM calls
python -m syllabus_agent.cli "compiler engineering" --force-refresh  # ignore the hit, run it live
```

```bash
curl -X POST localhost:8000/syllabus -d '{"subject": "compiler engineering"}'
curl -X POST 'localhost:8000/syllabus?force_refresh=true' -d '{"subject": "compiler engineering"}'
# force_refresh is accepted as a query param or a body field.
```

Configuration (`.env`):

| Setting | Default | Notes |
|---|---|---|
| `MONGODB_URI` | `mongodb://localhost:27017` | MongoDB Atlas's free M0 tier works fine — paste its `mongodb+srv://` string |
| `MONGODB_DB` | `syllabus_agent` | Collection is always `syllabus_cache` |
| `CACHE_TTL_DAYS` | `30` | A hit older than this is treated as a miss and regenerated. `0` disables caching |

**How it works.**

- **Key**: the subject, lowercased and whitespace-collapsed, so `"Data Structures"`,
  `"data structures"` and `"  data   structures "` are one entry, not three.
- **Value**: the whole `PipelineResult`, whichever route it took. Classification-only
  results (`needs_clarification`, `rejected_non_academic`, `general_knowledge`) are
  cached too — repeatedly probing a rejected subject is exactly what a demo audience
  does, and it shouldn't keep costing a classifier call.
- **Freshness**: `PipelineResult.generated_at` (new — a top-level field, because
  `CanonicalSyllabus.generated_at` only exists on the full-pipeline route) versus
  `CACHE_TTL_DAYS`. Stale entries are overwritten by the regenerated result.
- **`from_cache`**: a new `bool` on `PipelineResult`, true only for a result served
  without running any stage. It is never *stored* as true — the flag describes how a
  response was served, not what's on disk.
- **`--force-refresh` / `force_refresh`** skips the lookup but still writes the fresh
  result back, so you can demo the live pipeline and leave the cache warm for the
  next question from the audience.

**Where it sits.** `clients/cache_client.py` follows the same pattern as the other
clients: a `CacheClient` ABC (`get(subject)` / `set(subject, response)`) with
`MongoCacheClient` as the concrete implementation, so it is swappable for Redis or a
dict and mockable in tests. The orchestrator wraps the stages with it; no stage knows
the cache exists, and no stage logic changed.

**Failure is a miss, never an error.** Every Mongo failure — server down, bad URI,
a document written before a schema change — logs a warning and degrades to a cache
miss. The pipeline then runs at full cost, which is the pre-cache behaviour. This is
why `doctor` reports an unreachable Mongo as `⚠` rather than `✗` and leaves the exit
code alone: caching costs quota when it's broken, not correctness.

**Intentionally simple.** No versioning, no partial invalidation, no history —
latest write wins, and freshness is one TTL. The idea doc's §9 lists "exact
freshness/versioning policy for stored syllabi" as an open question for Phase 2;
this is the quota shield that makes repeated demo runs survivable, not the storage
layer that answers it. Notably absent: per-source invalidation (a syllabus is cached
whole), a "regenerate if sources changed" signal, and any notion of a previous
version to diff against.

## Troubleshooting: `doctor`

Before debugging a failing pipeline run, check the configuration:

```bash
python -m syllabus_agent.cli doctor
```

Exits `0` when environment, the configured-model probe, and the search check all
pass; `1` otherwise — so it works as a pre-flight check in a script. Model
listing is informational and never affects the exit code.

| Check | Cost | What it tells you |
|---|---|---|
| Environment | free | All four settings are present and not still the `.env.example` placeholders. Key values are masked (`AIza...9x2Q`). Warns if `GEMINI_BASE_URL` isn't an OpenAI-compatible `/openai` path — the native `/v1beta/interactions` endpoint 404s against `/chat/completions`. |
| Model listing | 1 call | How many models the key can see, and whether `GEMINI_MODEL` is among them. **Listed ≠ callable** — `gemini-2.5-flash` is listed but 404s with "no longer available to new users". |
| Configured-model probe | 1 call | Whether `GEMINI_MODEL` actually answers. Distinguishes 404 (not available to this key), per-minute 429 (recoverable, reports the reset delay), and per-day 429 (will not recover today). |
| Search provider | 1 call | Whether the Tavily key is accepted. |
| MongoDB cache | free | Whether the cache server answers a `ping`. Credentials in an Atlas URI are stripped from the output. Reports `⚠` and **never affects the exit code** — a broken cache costs quota, not correctness. |
| Candidate probes | 1 call each | Only with `--probe-models`. |

### `--probe-models`

Each probe is a billable call, so reach for it **only when the configured model
is actually failing** — the plain `doctor` run already tells you that.

```bash
python -m syllabus_agent.cli doctor --probe-models
python -m syllabus_agent.cli doctor --probe-models --candidates gemini-3.5-flash-lite,gemini-2.0-flash
```

Quotas are per-model, so a model that is daily-exhausted says nothing about the
others. Output ends with a concrete suggestion:

```
Candidate model probes (4 calls)
  ✗ gemini-3.5-flash        429 daily quota exhausted — will not recover today
  ✓ gemini-3.5-flash-lite   200 OK — callable
  ✓ gemini-3.1-flash-lite   200 OK — callable
  ✗ gemini-2.0-flash        429 daily quota exhausted — will not recover today

Suggestion: set GEMINI_MODEL in .env to one of: gemini-3.5-flash-lite, gemini-3.1-flash-lite
```

`doctor` never edits `.env` — it tells you what to change and leaves it to you.

## Logging and tracing

Two independent channels, so normal runs stay readable while full detail is
always captured:

| Channel | Default | With `--verbose` |
|---|---|---|
| Console (stderr) | INFO — one concise line per external call (stage, model, prompt chars, status, duration) | DEBUG — full outgoing messages, full raw response body, parsed JSON, retry waits |
| `logs/*.jsonl` | Full detail, always | Full detail, always |

```bash
python -m syllabus_agent.cli "Business management" --verbose
LOG_LEVEL=DEBUG python -m syllabus_agent.cli "Business management"   # equivalent
```

The trace path is printed to stderr at the start of every run:
`Full call trace: logs/run_20260807T172411Z.jsonl`

The CLI writes one file per invocation (`run_<UTC>.jsonl`). The FastAPI app
writes one file **per server run** (`server_<UTC>.jsonl`) rather than per
request — per-request files would multiply without bound under real traffic, and
each line already carries a timestamp and stage, so a single append-only file
stays greppable.

### JSONL schema

One line per external call:

```json
{
  "timestamp": "2026-08-07T17:24:15.301160+00:00",
  "call_type": "llm",
  "stage": "classifier",
  "request": {"url": "...", "headers": {"Authorization": "***REDACTED***"}, "body": {...}},
  "response": {"http_status": 200, "raw": "...", "cleaned": "...", "usage": {...}},
  "status": "success",
  "duration_ms": 3576.03,
  "attempt": 1
}
```

- `call_type` — `llm` | `search` | `extraction` | `cache`
- `stage` — `classifier` | `query_generation` | `source_collection` | `extraction` |
  `relevance` | `structuring` | `merge` (`unknown` if a call somehow escapes a
  `stage_context()` block). The merge is an LLM call and traces like any other; the
  `apply_relevance_filter` step between relevance and structuring is pure Python and
  produces no record, logging its keep/drop counts at INFO instead.
- `status` — `success` | `http_error` | `parse_error` | `rate_limited`

The stage label is carried on a `contextvars.ContextVar` set by each stage
(`stage_context()` in `logging_setup.py`), so the clients stay unaware of where in
the pipeline they are being called from — which is what lets the relevance stage
fan out concurrently and still have each of its calls labelled correctly.

Retries appear as separate lines with the same stage and an incremented `attempt`,
so a parse failure followed by a successful retry is two records.

**API keys are always redacted** — both by key name (`api_key`, `Authorization`,
…) and by scrubbing the literal key values anywhere they appear, including inside
error strings. Tavily sends its key in the JSON body rather than a header; that is
redacted too. `logs/` is gitignored.

## Security posture

Full detail and the audit trail are in [AUDIT.md](AUDIT.md); the short version:

- **SSRF guard on extraction.** Extraction fetches URLs handed to it by an external
  search API, which makes it an SSRF sink. `assert_safe_url()` allows http(s) only,
  resolves the host, and rejects private, loopback, link-local, reserved, multicast
  and unspecified addresses. Redirects are followed **manually**, max 5 hops, with
  every hop re-checked — auto-follow would let a public URL bounce straight to an
  internal one. Known limitation: this does not close the DNS-rebinding (TOCTOU)
  window between check and connect.
- **Secrets never reach disk or the console.** Keys are redacted by field name and
  by scrubbing their literal values (`register_secret()`), `doctor` masks them for
  display, and `.env` plus `logs/` are gitignored. Verified: zero key occurrences
  across all captured traces and console logs. Caveat: `redact()` runs inside
  `record_call()`, so it covers the JSONL trace, not arbitrary future log lines.
- **Generic 500s.** The FastAPI exception handler returns a fixed `detail` string
  and logs the traceback server-side, so provider responses and request context
  never leave the process.
- **No hardcoded secrets** anywhere in the tracked tree — only key-shaped fixtures
  in `test_diagnostics.py`.

## Layout

```
syllabus_agent/
  config.py             pydantic-settings Settings, reads .env (provider-agnostic field names)
  diagnostics.py        `doctor` checks — env, model listing, probes, quota triage, Mongo ping
  logging_setup.py      console/file logging, per-call JSONL tracing, stage context, key redaction
  main.py               FastAPI app — POST /syllabus
  cli.py                argparse CLI — python -m syllabus_agent.cli "<subject>" | doctor
  schemas/              Pydantic models shared between stages
    enums.py              RouteDecision, SourceFormat, ExtractionMethod,
                          RelevanceVerdict, PipelineStage
    classification.py     ClassificationResult
    query.py              SearchQuery, QueryGenerationResult
    source.py             CandidateSource, SourceCollectionResult
    extraction.py         RawTextBlock, ExtractionFailure, ExtractedSource, ExtractionResult
    relevance.py          RelevanceResult
    syllabus.py           SourceUnit, PerSourceStructure, SourceRanking,
                          MergedTopic, MergedUnit, CanonicalSyllabus
    pipeline.py           PipelineResult — the response envelope for every route
  clients/              everything that talks to the outside world, behind ABCs
    llm_client.py         LLMClient ABC + OpenAICompatibleLLMClient
    search_client.py      SearchClient ABC + TavilySearchClient
    cache_client.py       CacheClient ABC + MongoCacheClient
    extraction_client.py  HtmlExtractor / PdfTextExtractor / PdfOcrExtractor, detect_format()
  prompts/              base.txt (shared output rules) + classifier / query_generation /
                        relevance / structuring / merge .txt, composed by load_prompt()
  pipeline/
    classifier/           stage 1  classify_subject()
    query_generation/     stage 2  generate_queries()
    source_collection/    stage 3  collect_sources()
    extraction/           stage 4  extract_sources()
    relevance/            stage 5  assess_relevance()
    structuring/          stage 6  structure_per_source() + semantic_merge()
    orchestrator.py       runs stages 1-6 + merge, short-circuits on routing,
                          applies the relevance filter, wraps the run in the result cache
  utils/
    trust_scoring.py    pure heuristic scoring (domain, content richness, blend,
                        relevance multiplier) — not behind a client interface
tests/                  pytest, one file per stage plus cache/logging/diagnostics/trust,
                        using fakes from conftest.py; *_live.py hit real APIs and
                        skip themselves when the corresponding key is unset
docs/example-run/       one complete captured run, rendered as a static HTML report
AUDIT.md                pre-archive audit: security fixes, dead code removed, open issues
syllabus-agent-idea-doc.md   the original five-phase design doc this is measured against
setup.sh                venv + deps + .env + test run in one `source setup.sh`
```

## See a real run

Rather than a live demo — which would depend on free-tier quota and on whatever the
search API returns that day — one complete, successful run is captured in the repo:

**[`docs/example-run/index.html`](docs/example-run/index.html)** — open it directly
after cloning, or serve it via GitHub Pages. It renders one real execution for
"data structures": the classification and its reasoning, the full trust-ranking
table (domain vs content vs blended score, and which sources were used), extraction
and relevance-filter breakdowns, the final units and topics with per-topic
provenance, and the merge step's own account of what it excluded and why.

The raw evidence sits alongside it, unedited:

| File | What it is |
|---|---|
| [`full_response.json`](docs/example-run/full_response.json) | The complete pipeline response, including per-source structures captured before merging |
| [`trace.jsonl`](docs/example-run/trace.jsonl) | Every external call in that run — 80 records with request, response, status and duration. API keys redacted by the logging layer |
| [`doctor_output.txt`](docs/example-run/doctor_output.txt) | A healthy `cli doctor` pre-flight check from the same session |
| [`build_report.py`](docs/example-run/build_report.py) | The generator that renders `index.html` from the two files above — the report is derived, not hand-written |

Re-running the pipeline will produce different results — the sources depend on what
the search API surfaces that day.

## Running it

Python **3.11+** (developed and tested on 3.12; the code uses `X | None`
annotations throughout). `source setup.sh` does the whole block below in one go —
it creates the venv, installs, copies `.env.example` to `.env` if absent, and runs
the tests. Manually:

```bash
python3 -m venv .venv && source .venv/bin/activate     # 3.11+
pip install -r requirements.txt
cp .env.example .env   # then add real GEMINI_API_KEY and TAVILY_API_KEY
                       # (all clients are live; run `cli doctor` to verify)

python -m syllabus_agent.cli doctor             # pre-flight: keys, model, search, cache
pytest                                          # 101 tests
uvicorn syllabus_agent.main:app --reload        # POST /syllabus {"subject": "..."}
python -m syllabus_agent.cli "data structures"  # same pipeline via CLI
```

**Tesseract** is a system binary, not a pip package — without it the OCR fallback
for scanned PDFs fails per-source (recorded on `ExtractionResult.failures`) rather
than aborting the run. `brew install tesseract` on macOS.

MongoDB is optional — without it the pipeline runs exactly as before, just with no
[caching](#caching). To get the cache, run a local server (`brew services start
mongodb-community`, or `docker run -d -p 27017:27017 mongo`) or point `MONGODB_URI`
at a free Atlas cluster. `cli doctor` tells you which of the two you have.

### Configuration

Everything is read from `.env` by `config.py` (pydantic-settings, `extra="ignore"`).
The full set:

| Setting | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | — | **Required.** Key for the LLM endpoint |
| `GEMINI_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai` | Any OpenAI-compatible base; `doctor` warns if the path isn't `/openai`-shaped |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Quotas are per-model, so this is also the quota dial |
| `TAVILY_API_KEY` | — | **Required.** Key for search |
| `MONGODB_URI` | `mongodb://localhost:27017` | Result cache; a local `mongod` or a free Atlas `mongodb+srv://` string |
| `MONGODB_DB` | `syllabus_agent` | Collection is always `syllabus_cache` |
| `CACHE_TTL_DAYS` | `30` | `0` disables caching |
| `LOG_LEVEL` | `INFO` | Env var, not a `Settings` field. `DEBUG` is equivalent to `--verbose` |

The field names are deliberately provider-agnostic *inside* the code — `Settings`
exposes `llm_api_key` / `llm_base_url` / `llm_model` properties, so nothing
downstream mentions Gemini and swapping providers stays a `.env` change. (The env
var names still carry the `GEMINI_` prefix; renaming them was left alone rather
than breaking existing `.env` files.)

Missing or placeholder keys produce a loud stderr warning at startup
(`warn_on_missing_keys`) instead of an opaque 401 mid-run.

### Tests

`pytest` runs **101 tests** — one file per stage plus `test_cache.py`,
`test_logging_setup.py`, `test_diagnostics.py` and `test_trust_scoring.py`, all
against the fakes in `conftest.py`. `asyncio_mode = auto` is set in `pytest.ini`,
so async tests need no decorator.

`test_llm_client_live.py` and `test_search_client_live.py` hit the real providers
and **skip themselves** when `GEMINI_API_KEY` / `TAVILY_API_KEY` are unset, so a
clone with no keys still runs green. With keys present they cost a handful of
calls against the daily quota — worth knowing before running `pytest` on a day you
need the quota for a demo.

### The API

One endpoint, plus FastAPI's own `/docs` (Swagger UI) and `/openapi.json`:

```bash
curl -X POST localhost:8000/syllabus \
  -H 'Content-Type: application/json' \
  -d '{"subject": "data structures"}'
```

Request: `{"subject": str, "force_refresh": bool = false}` — `force_refresh` is
also accepted as a `?force_refresh=true` query param. Response: a `PipelineResult`
on every route, so a caller parses one shape regardless of what happened:

| Field | Meaning |
|---|---|
| `subject` | Echoed back as sent |
| `route` | The classifier's verdict — one of the four `RouteDecision` values |
| `classification` | Full `ClassificationResult`, including the model's reasoning and any `clarifying_question` |
| `stage_reached` | `classification` \| `relevance` \| `structuring` — how far the run got |
| `syllabus` | The `CanonicalSyllabus`, or `null` on any non-`genuine_academic_subject` route or error |
| `error` | Set when a stage ended the run deliberately (e.g. nothing survived relevance filtering) |
| `generated_at` | UTC timestamp, on every route — this is what the cache TTL is measured against |
| `from_cache` | `true` only when served from cache without running a stage; never stored as `true` |

Unhandled exceptions return a generic `500 {"detail": ...}` with the traceback kept
server-side — pipeline errors can carry provider responses and request context, so
they are logged, not serialised into the response.

## Not built (by design)

- Syllabus **versioning** and partial invalidation (Phase 2). The result
  [cache](#caching) is built; a store of record with history is not.
- Lecture-note / content generation (Phase 3).
- Frontend (Phase 4).

See [Phase status](#phase-status) for the full picture.
