---
noteId: "28029160927211f186e3991dcdee008a"
tags: []

---

# Syllabus Agent — Project Idea Doc

## 1. The Core Idea

An AI agent that takes a single subject as input (e.g. "data structures", "theory of computation") and:

1. Searches the web to find how **top universities structure that course** as an actual taught subject.
2. Reverse-engineers that into a **canonical syllabus**: a standard structure of units/chapters, each containing a set of topics.
3. Eventually, generates **professor-style lecture content** for each topic — scripted notes equivalent to what a student would get from a real one-hour lecture, complete with real-world examples.
4. The end goal: free, structured, high-quality education for any subject, stitched together from what the best institutions already teach, but delivered as an on-demand AI "professor."

The insight underpinning this: nearly every university publishes its syllabus online in some form, and modern LLMs already capture a large fraction (~80%) of the nuance of any given subject. The missing piece isn't raw knowledge — it's **structure**: taking scattered, inconsistently formatted syllabus data and turning it into a reliable curriculum shape, then generating content against that shape rather than freeform.

## 2. The Syllabus Structure Model (LTPC)

Borrowed from how Indian universities (and many others) structure a course:

- A **subject** is typically divided into **4 units** (chapters).
- Each unit contains roughly **15–20 topics**.
- One topic ≈ roughly one lecture/class session.
- Courses are tagged with **LTPC**: Lecture hours, Tutorial hours, Practical hours, Credits — a standard scheme describing how a course is delivered and weighted.

This LTPC/4-unit shape becomes the **canonical target structure** that the agent normalizes every subject into, regardless of how the source university actually formatted it.

## 3. High-Level System Design

The system is a **five-stage pipeline**, each stage independently testable and swappable:

1. **Ingestion & Discovery** — given a subject, search the web for syllabi.
2. **Syllabus Normalization** — cluster/merge multiple sources into the canonical 4-unit structure.
3. **Content Generation** — turn each topic into lecture-equivalent content (deferred to a later phase).
4. **Storage & Retrieval** — persist subjects/units/topics/content so nothing is regenerated twice (deferred).
5. **Delivery** — API + frontend serving this to a student (deferred).

**Phase 1 scope is deliberately narrow**: only stages 1–2 (renamed "Layer One" below). Content generation and storage are designed for, but not built yet.

## 4. Layer One — The Search & Structuring Pipeline (Phase 1 Focus)

Layer One itself is broken into five sequential stages:

### 4.0 Classifier / Router (the critical first gate)
Before any search happens, an LLM classifies the input subject into one of four buckets, because "give it a topic" turns out to span wildly different kinds of things:

- **(a) Genuine academic subject** — e.g. "data structures", "theory of computation". A real university syllabus exists; the standard pipeline runs as designed.
- **(b) General-knowledge topic** — e.g. "Mount Fuji", "rivers of India". No syllabus exists in the LTPC sense; needs a completely different output template (structured explainer: facts, significance, formation, etc.) rather than a forced 4-unit syllabus.
- **(c) Broad / jurisdiction-specific field** — e.g. "civil law", "Japanese history". A syllabus exists, but varies hugely by country/institution. The agent should ask a clarifying question (which country's legal system? which era?) before searching.
- **(d) Non-academic entity** — e.g. "the FBI". Not a subject at all; there's no syllabus to find. The agent should reject/redirect rather than force a fit.

This classifier is a **router**: its output determines which downstream pipeline variant actually executes. This was identified as essential after stress-testing the idea against a deliberately diverse set of example inputs (rivers of India, Mount Fuji, Japanese history, Paris, theory of computation, civil law, the FBI, Indian judiciary systems) — the realization being that "subject" is not one uniform category, and forcing everything through the same academic-syllabus shape breaks badly on non-academic or jurisdiction-dependent inputs. There's also a factual-accuracy risk case worth remembering: even valid academic subjects can be **time-sensitive** (e.g. legal codes that get amended), so sourced content needs freshness awareness, not just structural correctness.

### 4.1 Query Generation
An LLM expands the single input subject into multiple targeted search queries automatically — e.g. "top university syllabus data structures", "NPTEL data structures syllabus", "MIT OpenCourseWare data structures units" — rather than relying on one search phrase getting lucky.

### 4.2 Source Collection & Filtering
Run the generated queries through a real search API (not scraping Google directly). Collect a batch of candidate URLs (~20–30), then filter out obvious junk (forums, blogs, spam), keeping sources that look like actual syllabi: `.edu` domains, known open courseware platforms (MIT OCW, NPTEL), or PDFs with syllabus-like filenames/content.

### 4.3 Extraction
For each surviving source, detect whether it's HTML or PDF and extract raw text accordingly:
- HTML → parse tables/structure (e.g. BeautifulSoup).
- Text-based PDFs → direct text extraction (e.g. PyMuPDF).
- Scanned/image PDFs → OCR fallback (e.g. pytesseract).

**Key real-world finding (validated against SASTRA University's B.Tech CSE syllabus pages):** university syllabus data is messy in two distinct ways — (1) it's split across a mix of HTML "scheme of study" tables plus separate per-subject PDFs, and (2) even within a single clean PDF, formatting is inconsistent: LTPC appears as a raw number row (e.g. "4 1 0 5"), unit headers are inconsistently delimited (sometimes Roman numerals, sometimes not), and topic lists are dumped as comma/hyphen-separated prose rather than clean bullets. This means writing per-university regex parsers is a losing strategy. The right design is: the parser's only job is to **isolate raw per-unit text blocks** (don't try to deeply parse formatting), then hand each block to an LLM to do the actual segmentation into unit titles + topic lists. The LLM absorbs the formatting inconsistency; the extractor just needs to find the right chunk of text.

### 4.4 Structuring & Merge
Each raw per-unit text block goes through an LLM with a strict schema-output instruction: unit number, unit title, clean topic list, LTPC info if present. This produces 5–6 structured versions of the syllabus (one per source).

**Ranking/trust problem:** these versions won't agree perfectly. Rather than trusting one arbitrary source, each source gets scored on:
- **Source reputation** — a tier list of top schools *for that specific subject* (a top-ranked CS department's syllabus counts more for a CS subject).
- **Recency** — an old syllabus for a fast-moving subject (e.g. ML) is effectively stale.
- **Structural completeness** — does it actually have clear units and a topic count near the 15–20 target, vs. a vague one-liner.

Then, instead of picking a single "winner" source, the top 3–4 ranked syllabi are fed into an **LLM merge step**, which synthesizes one canonical unit structure: the common core topics everyone agrees on, plus valuable additions from the higher-ranked sources.

## 5. Future Phases (Designed For, Not Built in Phase 1)

These are the stages that come after Layer One is working. They're not being built yet, but the phase 1 architecture (typed schemas, swappable interfaces, an orchestrator) is deliberately designed so each of these slots in without rework.

### Phase 2 — Storage & Persistence
Once a canonical syllabus has been produced once for a subject, it shouldn't need to be regenerated from scratch every time someone asks for it again. This phase adds a real database layer.

- **Database**: MongoDB (matches prior project experience from the exam-helper, and the natural shape here — subject → units → topics → sources — is a nested document rather than something needing relational joins).
- **What gets stored**: the canonical syllabus itself (units, topics, LTPC info), the raw structured versions pulled from each source before merging (so the merge logic can be re-run or audited later without re-scraping), and metadata per source (university, trust score, recency, URL).
- **Caching/reuse logic**: before Layer One runs its full pipeline for a subject, check storage first — if a canonical syllabus already exists and is still "fresh enough" (see recency below), serve it directly instead of re-running search and structuring.
- **Versioning**: syllabi should be revisitable over time — a subject like "machine learning" taught in 2023 vs. 2026 will look different, so this phase needs to think about whether to overwrite old canonical syllabi or version them, especially for fast-moving subjects.
- **Freshness/staleness handling**: for subjects that are inherently time-sensitive (e.g. legal codes, fast-moving tech subjects), storage needs a "regenerate after N days/months" policy rather than treating a stored syllabus as permanently valid.

### Phase 3 — Content Generation
This is the actual "AI professor" layer — the part that turns a structured topic list into something a student actually learns from.

- **Per-topic lecture generation**: for each topic inside a unit, generate content equivalent to what a student would absorb from a real one-hour lecture on that topic — not a dry definition, but an explanation with structure, examples, and the kind of real-world framing an actual professor would use.
- **Persona/voice**: the LLM is prompted to act as a college professor teaching that specific subject — meaning tone, pacing, and pedagogical structure (intro → concept → example → recap) matter, not just factual coverage.
- **Granularity options to figure out**: generate one topic at a time (most controllable, easiest to cache/reuse), one unit at a time (more coherent narrative across topics but harder to cache individual pieces), or lazily on-demand only when a student actually requests that topic (saves generation cost, but means first-time latency for unpopular topics).
- **Grounding in the sourced syllabus data**: ideally the lecture content should stay anchored to what the top-university sources actually said about that topic (not just the LLM's own generic knowledge), which likely means passing the original extracted source text for that topic back into the generation prompt as grounding context, not just the topic title.
- **Real-world scenario injection**: explicitly called out as a goal — each lecture should include realistic, practical scenarios illustrating the topic, not just abstract theory.
- **Output format**: still open — could be a lecture script (spoken-style prose meant to be read start to finish), structured notes (headers, bullet points, key terms), or both, with the student choosing which format they want.

### Phase 4 — Delivery (API + Frontend)
Once syllabi and lecture content exist, this phase makes it actually usable by a student.

- **API**: expand beyond the phase-1 single "generate syllabus" endpoint into a full set — fetch a stored syllabus, fetch/generate a specific topic's lecture, list available subjects, etc.
- **Frontend**: React (matches prior project experience). Core student experience: pick or search a subject, see the 4-unit canonical structure, drill into a unit, then into a topic to read/consume that topic's lecture content — essentially a self-serve course browser.
- **Progressive consumption**: a student should be able to go unit by unit, or the whole subject at once, matching how the original idea described wanting to "cover the entire unit" or "the entire subject."
- **Free access as a design constraint, not just a mission statement**: since the whole point is free education, this phase needs to keep infra costs low enough to stay free to end users — which is part of why the free-tier LLM and swappable-interface decisions in Phase 1 matter long-term, not just for prototyping.

### Phase 5 — Possible Future Additions (Explicitly Out of Scope For Now)
These came up as natural extensions during design discussion but were deliberately excluded from the current plan, to keep phase 1 tightly scoped:

- **Assessments**: quizzes or tests per topic/unit to check understanding, not just deliver content one-directionally.
- **Student progress tracking**: marking which topics/units a student has completed, resuming where they left off.
- **Q&A / interactive follow-up**: letting a student ask the "professor" persona clarifying questions about a topic after the lecture, rather than only consuming static generated content.
- **Multi-source content diversity**: today the plan merges syllabi into one canonical structure; a future version could preserve multiple "tracks" (e.g. a more theoretical vs. more applied treatment of the same subject) rather than collapsing everything into a single merged version.

## 6. Tech Stack (Phase 1)

Chosen deliberately around tools already familiar from a prior project (the GATE exam-helper: FastAPI + React + MongoDB + Gemini), to avoid fighting unfamiliar tooling *and* a hard new problem simultaneously.

- **Backend**: Python + FastAPI, async throughout.
- **LLM interface**: built against the **OpenAI-compatible chat-completions shape**, not a vendor-specific SDK. This is the single most important architectural decision in the stack — it means the classifier, query generation, structuring, and (later) content generation stages don't care which model answers them. Swapping providers is a config change (base URL + model name + API key), not a code change.
- **Runtime LLM (default)**: **Gemini free tier**, called via its OpenAI-compatible endpoint. Chosen after evaluating alternatives (see §7) and after confirming a working API key with a successful test call.
- **Search**: Tavily assumed as the initial concrete implementation (agent-oriented search API), behind the same kind of swappable interface. Alternatives noted: Brave Search API, SerpAPI.
- **Extraction**: `httpx` (async fetch) + `BeautifulSoup` (HTML) + `PyMuPDF` (text PDFs) + `pytesseract` (OCR fallback for scanned PDFs).
- **Database (future)**: MongoDB.
- **Frontend (future)**: React.

## 7. LLM Provider Decision — Reasoning Trail

This was its own significant sub-discussion, worth preserving:

- **Claude Pro subscription ≠ Claude API access.** A paid Claude.ai subscription (chat) and the Claude Console/API are separate products with separate billing — the subscription does not include programmatic API access. Claude Code itself, however, *is* included in the Pro plan and draws from the subscription's usage pool (not API billing) — **unless** an `ANTHROPIC_API_KEY` environment variable happens to be set, in which case Claude Code silently switches to pay-per-token API billing instead. Worth checking `~/.zshrc` for this.
- **Considered running an open-source LLM**, since the project's core tasks (classification, structured JSON extraction, schema-conformant output) are exactly where open-weight models are strongest relative to proprietary ones — the capability gap is narrowest on structured/reasoning tasks and widest on creative writing.
- **Hardware reality check**: dev machine is an **M1 MacBook Air with 8GB RAM**. Running an LLM locally as the pipeline's always-on engine was ruled out — even a small quantized model would compete for memory with FastAPI, a browser, and Claude Code running simultaneously during development, making local inference impractical as the backbone (though technically possible later as an optional/offline experiment via Ollama, which the M1 chip can handle reasonably for small models).
- **Landed on a hosted free-tier API instead.** Candidates considered: **Groq** (custom fast inference hardware, free tier, OpenAI-compatible), **OpenRouter** (router in front of many models including free open-weight ones — good for trying several without separate signups), **Gemini** (free tier, already familiar from the exam-helper project), **Cerebras** (fast-inference free tier, similar pitch to Groq), **DeepSeek** (very cheap, strong on structured/reasoning tasks), **Together AI** (broad model hosting, free signup credits).
- **Final choice: Gemini free tier.** API key generated and a live test call confirmed working (model `gemini-3.6-flash`).
- Because the `llm_client` is built against the OpenAI-compatible interface rather than any specific SDK, this choice is not a lock-in — Groq, OpenRouter, local Ollama, or Claude (once API budget exists) are all a config change away.

## 8. Phase 1 Deliverable (In Progress)

A Claude Code prompt was written to scaffold the repo, requesting:
- A proposed **folder structure** and **Pydantic schemas** first, for review before implementation.
- One package per pipeline stage (`classifier/router`, `query_generation`, `source_collection`, `extraction`, `structuring`), each with a single clear entry function, passing typed Pydantic models between stages rather than loose dicts.
- All external dependencies (LLM, search, extraction) behind thin, swappable interfaces.
- A single orchestrator that runs stages in order and short-circuits based on the classifier's routing decision.
- A FastAPI endpoint (POST subject → canonical syllabus or routing decision) plus a CLI entry point.
- Config via `.env` + pydantic-settings, with placeholders for `GEMINI_API_KEY`, `GEMINI_BASE_URL`, `GEMINI_MODEL`, `TAVILY_API_KEY`.
- Stub implementations with realistic fake data everywhere, so the full skeleton runs end-to-end before real API calls are wired in.

Current status: prompt pasted into Claude Code in plan mode, in an empty repo, awaiting the proposed structure for review.

## 9. Open Questions / Not Yet Decided

- Exact schema for how syllabus data will be modeled in MongoDB once storage is built.
- Whether/how the (b) general-knowledge and (c) clarifying-question routing paths get their own dedicated content-generation templates downstream.
- Final choice of search API (Tavily assumed, not yet confirmed as final).
- Content generation granularity (per-topic vs. per-unit vs. lazy on-demand) and output format (script vs. structured notes vs. both) — see §5, Phase 3.
- Exact freshness/versioning policy for stored syllabi on time-sensitive subjects — see §5, Phase 2.
