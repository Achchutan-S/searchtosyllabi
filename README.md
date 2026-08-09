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
| **`doctor` diagnostics** | Distinguishes 404 / per-minute 429 / per-day 429; scriptable exit codes |
| **Observability** | Per-run JSONL trace of every external call, with API keys redacted (verified: zero occurrences) |
| **Provider swappability** | Swapped across four Gemini models with zero code changes — `.env` only |

### What doesn't work well

- **Output quality is inconsistent.** A recent "data structures" run produced 6
  thematically-correct units and 22 topics — reasonable. Earlier runs produced 21
  units contaminated with other courses. The relevance filter and semantic merge
  fixed the contamination, but topic coverage still varies run to run with which
  sources the search happens to surface.
- **Free-tier economics don't work.** ~50 LLM calls per run against a ~20/day
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

Against the original five-phase plan (`syllabus-agent-idea-doc.md`):

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
stage logs the raw response and r
aises — a wrong default here would send a
subject down the full pipeline and hide the real cause.

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
`CanonicalSyllabus.collected_not_structured`, separate from
`contributing_sources` (the ones actually structured and merged), so you can
audit what the pipeline saw versus what it used.

A secondary benefit is cost/quota: a real run collects ~38 sources, so
structuring all of them meant ~38 LLM calls and a guaranteed HTTP 429. Capped, a
run costs `1 (classify) + 1 (query generation) + N (structuring)` = **6 calls at
N=4** in the happy path, plus one extra per retry.

### Free-tier quota notes

Gemini enforces two separate free-tier caps, both surfaced as HTTP 429, and the
model you pick matters enormously:

- **Per-minute** (`GenerateRequestsPerMinutePerProjectPerModel-FreeTier`) — ~5/min. A
  6-call run trips this once; the client reads the provider's `Retry-After` and waits.
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
- `stage` — `classifier` | `query_generation` | `source_collection` | `structuring`
  (the merge step is deterministic Python, not an LLM call, so it produces no record —
  it logs its input/output at INFO/DEBUG instead)
- `status` — `success` | `http_error` | `parse_error` | `rate_limited`

Retries appear as separate lines with the same stage and an incremented `attempt`,
so a parse failure followed by a successful retry is two records.

**API keys are always redacted** — both by key name (`api_key`, `Authorization`,
…) and by scrubbing the literal key values anywhere they appear, including inside
error strings. Tavily sends its key in the JSON body rather than a header; that is
redacted too. `logs/` is gitignored.

## Layout

```
syllabus_agent/
  config.py            pydantic-settings Settings, reads .env
  diagnostics.py        `doctor` checks — env, model listing, probes, quota triage
  logging_setup.py      console/file logging, per-call JSONL tracing, key redaction
  main.py               FastAPI app — POST /syllabus
  cli.py                 argparse CLI — python -m syllabus_agent.cli "<subject>"
  schemas/               Pydantic models shared between stages
  clients/                LLM / search / extraction / cache interfaces + impls
  prompts/                 base.txt (shared output rules) + one .txt per stage, composed by load_prompt()
  pipeline/
    classifier/           stage 1
    query_generation/       stage 2
    source_collection/       stage 3
    extraction/                stage 4
    structuring/                 stage 5
    orchestrator.py               runs stages 1-5, short-circuits on routing,
                                   wraps the whole run in the result cache
  utils/
    trust_scoring.py       pure heuristic scoring, not behind a client interface
tests/                    pytest, one file per stage, using fakes from conftest.py
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

Re-running the pipeline will produce different results — the sources depend on what
the search API surfaces that day.

## Running it

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then add real GEMINI_API_KEY and TAVILY_API_KEY
                       # (all clients are live; run `cli doctor` to verify)

pytest                                          # unit tests, mocked clients
uvicorn syllabus_agent.main:app --reload        # POST /syllabus {"subject": "..."}
python -m syllabus_agent.cli "data structures"  # same pipeline via CLI
```

MongoDB is optional — without it the pipeline runs exactly as before, just with no
[caching](#caching). To get the cache, run a local server (`brew services start
mongodb-community`, or `docker run -d -p 27017:27017 mongo`) or point `MONGODB_URI`
at a free Atlas cluster. `cli doctor` tells you which of the two you have.

## Not built (by design)

- Syllabus **versioning** and partial invalidation (Phase 2). The result
  [cache](#caching) is built; a store of record with history is not.
- Lecture-note / content generation (Phase 3).
- Frontend (Phase 4).

See [Phase status](#phase-status) for the full picture.
