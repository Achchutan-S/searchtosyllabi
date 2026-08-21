---
noteId: "66bf2d2093d811f184f7db6706c5556d"
tags: []

---

# syllabus-agent — Archive Audit

**Date:** 2026-08-09
**Purpose:** pre-archive audit and cleanup. No new features; no pipeline
behaviour changed except the security fixes listed below.
**Test suite:** 80 passed, 0 failed, 0 skipped.

> **Updated 2026-08-09 (follow-up pass).** Two items in "Out-of-scope issues"
> have changed status: the merge coverage bug is **fixed**, and the JSONL trace
> corruption finding is **withdrawn as incorrect** — see items 2 and 3.

---

## Working

Everything below was verified by reading the code *and* by live runs during this
session — not assumed.

| Area | Status | Evidence |
|---|---|---|
| **Extraction** | Real | Live run: `39/41 sources produced text`, methods `{pdf_text: 18, html_parser: 21}`. BeautifulSoup strips nav/script and preserves table cells (`4 \| 1 \| 0 \| 5`); PyMuPDF emits `[page N]` blocks; OCR verified on a genuinely scanned file (`vjit.edu.in`, 6,878 chars, artifacts like `A.1.C.T.E.` confirming real Tesseract output). Tesseract 5.5.1 binary present with `eng`. |
| **Trust scoring** | Real | Two signals blended 0.5/0.5. Live: MIT OCW admin pages demoted from top-4 to ranks 10–21 (content 0.00–0.16) while content-rich PDFs rose. `source_ranking` on the output shows domain/content/blended per source. |
| **Relevance filter** | Real | Live: 30 assessed → `direct_match: 11, partial_match: 2, field_level: 14, unrelated: 3`. Correctly called the RMC degree catalog `partial_match` with accurate reasoning. |
| **Structuring** | Real | Organic extraction confirmed: four sources yielded **27, 1, 4 and 5 units** — no shape imposed. |
| **Semantic merge** | Real | Single LLM call, thematic grouping with per-topic provenance. `merge_notes` shows genuine reasoning ("…excluded as they fall under programming methodology rather than core data structures consensus"). |
| **doctor** | Real | Correctly distinguishes 404 / per-minute 429 / per-day 429; exit 0 healthy, 1 otherwise (verified all three). |
| **Logging** | Real | Per-run JSONL trace of every llm/search/extraction call; console INFO one-liners, `--verbose` for bodies. Keys redacted (verified 0 occurrences). |
| **Provider swappability** | Real | Swapped across 4 Gemini models this session with zero code changes. |
| **Zero-source handling** | Correct | Empty collection → empty extraction → relevance returns `[]` → pipeline returns a `PipelineResult` with `stage_reached=relevance` and an explanatory `error`, never an empty syllabus. `semantic_merge([])` returns an empty syllabus without calling the LLM. |

---

## Stubbed

**Nothing.** No stub, fake-data generator, or `TODO`/`FIXME`/`HACK` marker remains
anywhere in `syllabus_agent/`. Verified by grep. The former `_fake_blocks()`,
`_fake_hits_for()` and `_fake_completion_for()` generators are all gone.

Every `print()` in the package is intentional user-facing output: the `doctor`
report, the CLI's JSON result, the trace path (stderr), and the missing-key
warning (stderr). No stray debug prints.

---

## Dead Code Removed

| Symbol | File | Why |
|---|---|---|
| `tesseract_available()` | `clients/extraction_client.py` | Written during the extraction pass, never wired into `doctor` or anything else. 0 references. |
| `get_run_file()` | `logging_setup.py` | Accessor never called; the run path is returned directly by `configure_logging()`. 0 references. |
| `ExtractionResult.failed_sources` | `schemas/extraction.py` | Back-compat shim from when the field was replaced by `.failures`. 0 references. |
| `import time` | `clients/extraction_client.py` | Unused after fetching moved out. |
| `import pytest` ×2 | `tests/test_diagnostics.py`, `tests/test_extraction.py` | Unused (pytest re-added to the latter for the new SSRF tests). |

`pyflakes` on `syllabus_agent/` and `tests/` is now **clean**.

**Kept deliberately** (flagged, not removed): `score_source()` and
`content_richness_score()` are one-line public wrappers currently exercised only
by tests. They read as reasonable API surface rather than dead code.

---

## Security Findings + Fixes

### Verified clean (no action needed)

- **No hardcoded secrets.** Scanned all 63 would-be-tracked files for the literal
  key values and for key-shaped patterns (`AIza…`, `tvly-…`, `AQ.…`, `sk-…`).
  Only hits are obvious test fixtures in `test_diagnostics.py`
  (`AIzaSyTESTKEY0123456789abcd`).
- **`.gitignore` covers** `.env`, `logs/`, `__pycache__/`, `.venv/`,
  `.pytest_cache/`, `*.pyc`, `.DS_Store`. Added `.notebook/` (IDE artifact
  directory that was uncovered).
- **Redaction is applied at every leak point.** All key call sites audited:
  `cli.py`/`main.py` call `register_secret()` for both keys; `config.warn_on_missing_keys`
  prints only key *names*; `diagnostics.py` uses `mask_secret()` for display;
  `llm_client` puts a `Bearer <key>` placeholder in the trace record; and
  `search_client`'s body — which does contain the real key — is caught by
  `redact()` on the `api_key` field name. Verified empirically: **0 key
  occurrences across all JSONL traces and all 11 captured console logs.**

### Fixed in this pass

**1. SSRF — extraction fetched arbitrary URLs with no guard.**
`fetch_source()` had a 30s timeout but no scheme or host restriction, and used
`follow_redirects=True`. Since URLs come from an external search API, a poisoned
result could reach `file://`, `localhost`, or `169.254.169.254` (cloud metadata).

Added `assert_safe_url()`: http(s) only, DNS-resolved, rejecting private,
loopback, link-local, reserved, multicast and unspecified addresses. Redirects
are now followed **manually** (max 5 hops) with every hop re-checked — auto-follow
would have let a public URL bounce straight to an internal one. Three tests cover
it. Documented limitation: this does not close the DNS-rebinding (TOCTOU) window,
which is proportionate for a portfolio project.

**2. FastAPI 500s had no explicit handler.**
Added an `Exception` handler returning
`{"detail": "Internal server error. See server logs for details."}` with the full
traceback logged server-side via `logger.exception`. Verified with a forced
`RuntimeError("SECRET-ish detail xyz")`: the response body contains neither the
message nor a traceback.

*(Starlette's default was already generic rather than leaky, so this hardens and
guarantees the behaviour rather than fixing an active leak.)*

### Noted, not fixed

- **Redaction covers the JSONL trace, not the Python logging stream.**
  `redact()`/`_scrub_text()` run inside `record_call()` only. If a future log line
  interpolated a key it would print in the clear. No current log line does — the
  keys live in headers and request bodies, never in URLs or messages — so this is
  latent, not active.

---

## Performance Notes

**1. Extraction is sequential, not concurrent — contrary to expectation.**
`extract_sources()` is a plain `for source in sources:` loop with `await` inside.
`asyncio.gather` **is** used, but only in the relevance stage
(`asyncio.gather` + `Semaphore(4)`). Extraction fetches ~30–40 URLs one at a time,
which dominates wall-clock (minutes per run; a single OCR document costs ~26s).
Left unchanged — parallelising it changes request behaviour against third-party
university servers, which is beyond an audit pass.

**2. Large redundant work, by construction.** Extraction runs on *all* collected
sources and relevance assesses *all* extracted sources, but only the top 4 are
ever structured. A representative run: 41 extractions + 47 relevance calls to
produce a syllabus from 4 sources — roughly 85% discarded. This is inherent to
the ordering (ranking needs extracted text; relevance needs it too), not a bug,
but it is the main cost driver.

**3. No redundant re-fetching.** Each URL is fetched exactly once; there is no
N+1 pattern. The `pre_extracted_content` fast path avoids a fetch when the
provider returns ≥2,000 chars of real text (rare with Tavily, whose `content` is a
~918-char median snippet).

**4. Long PDFs are bounded**: 30 pages for text, 10 for OCR, 20k chars per block,
40k per source, all with warnings when truncation occurs.

**5. Non-English content will degrade.** Tesseract has only `eng` installed, so a
scanned non-English PDF will OCR to garbage; all prompts are English-only. Not a
crash risk — the content-richness heuristic keys on English patterns (`unit`,
`credits`), so such sources will simply rank low and be filtered out.

---

## Test Suite Result

```
80 passed, 0 failed, 0 skipped   (~6s)
```

- **3 live tests ran and passed** (they auto-skip only when no API key is set;
  a key is present, so they executed against real Gemini/Tavily).
- **No tests were broken by the recent passes** — the suite was green at the start
  of this audit and stayed green.
- **3 tests added** in the audit pass, all for the SSRF guard; **2 more** in the
  follow-up pass (trust/penalty separation, U+2028 trace escaping), and one test
  asserting the old penalised-trust behaviour was removed as it encoded the bug.
- One pre-existing fragility improved earlier and worth noting: `conftest`'s fake
  LLM now **raises** on an unrecognised system prompt instead of silently
  returning the wrong shape, which is what caught the orchestrator breakage during
  the semantic-merge pass.

---

## Out-of-scope issues — reported, not fixed

Per your instruction, these are flagged rather than silently changed.

1. **The all-MIT ranking issue is genuinely fixed**, and I re-verified it: MIT OCW
   admin pages now land at ranks 10–21 with content scores of 0.00–0.16, and the
   relevance filter independently catches degree catalogs. Two separate mechanisms
   now guard it.

2. ~~**Merge coverage rule is mis-tuned.**~~ **FIXED** (follow-up pass, 2026-08-09).
   The relevance stage's 0.7× partial-match penalty was multiplied into
   `trust_score`, which is also what `prompts/merge.txt` rule 6 reads when
   deciding whether to keep a single-source topic at `trust >= 0.7`. The penalty
   now lives on a separate `RawTextBlock.relevance_penalty` field: ranking uses
   `blend_scores(domain × penalty, content)`, while the merge is told the
   unpenalised trust. Verified in the ranking table — partial-match sources show
   `domain=0.80 (unpenalised) x0.7 -> blended=0.556`. Topic count moved 19 → 22,
   inside the 20–80 target.

   **Caveat on that number:** all four sources selected in the verifying run were
   `direct_match` (penalty 1.0), so no partial-match source actually reached the
   merge. The fix is confirmed by the ranking table and a regression test; its
   effect on topic count is *not* isolated by that run, and 19 → 22 is confounded
   with a different source set being selected.

   **Correction to the original diagnosis above:** it claimed no source cleared
   0.7 *because of* the penalty. That was only partly right — the verifying run
   shows 3 of 4 selected sources at trust 0.80, and the one below the bar sits at
   0.55, which is its genuine domain score for "PDF on an unrecognised domain",
   unrelated to any relevance penalty.

3. ~~**JSONL trace corruption persists.**~~ **WITHDRAWN — the finding was wrong.**
   The trace files were never corrupt; the *verification method in this audit*
   was. `json.dumps(..., ensure_ascii=False)` leaves U+2028 (LINE SEPARATOR)
   unescaped, and Python's `str.splitlines()` treats U+2028 as a line break while
   JSONL does not. The same file read two ways:

   ```
   splitlines()  -> 88 lines, 2 "malformed"
   split("\n")   -> 87 lines, 0 malformed
   ```

   The concurrency hypothesis was also wrong: `record_call()` is synchronous with
   no `await`, so `asyncio.gather` cannot interleave it — a lock would have fixed
   nothing. The real (minor) defect was emitting a raw U+2028 inside a line, which
   breaks `splitlines()`-based readers. Fixed at the writer by escaping
   U+2028/U+2029, with a regression test. A live run now reads clean under both
   readers: **84 valid, 0 malformed**.

4. **Free-tier economics remain the hard limit** — ~50 LLM calls per run against a
   ~20/day per-model cap. Fine for a portfolio piece; fatal for the original
   "free education for everyone" goal without caching or a different provider.

5. **`.env` is currently untracked but the repo is not yet a git repo at all.**
   Run `git init` and confirm `git status` does not list `.env` or `logs/` before
   the first commit.
