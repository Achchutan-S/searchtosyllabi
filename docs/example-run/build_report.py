
"""Regenerate index.html from the captured run artifacts in this directory.

Reads `full_response.json` and `trace.jsonl` (both committed alongside this
script) and writes `index.html`. Pure standard library, no build step.

    python docs/example-run/build_report.py

Paths resolve against this file's own location, so it can be run from any
working directory. To capture a *new* run, replace the two input files:

    python -m syllabus_agent.cli "<subject>" --verbose > docs/example-run/full_response.json
    cp logs/run_<timestamp>.jsonl docs/example-run/trace.jsonl
    python -m syllabus_agent.cli doctor > docs/example-run/doctor_output.txt
    python docs/example-run/build_report.py

Note that some prose in the generated page describes what *this particular run*
showed (see the trust-ranking commentary); re-read it after a new capture rather
than assuming it still matches the data.
"""

import collections
import html
import json
import pathlib

BASE = pathlib.Path(__file__).resolve().parent
resp = json.loads((BASE / "full_response.json").read_text())
recs = [json.loads(l) for l in (
    BASE / "trace.jsonl").read_text().split("\n") if l.strip()]

syl = resp["syllabus"]
cls = resp["classification"]

# --- derive stats from the trace -------------------------------------------
stage_counts = collections.Counter(r["stage"] for r in recs)
ext = [r for r in recs if r["call_type"] == "extraction"]
ext_ok = [r for r in ext if r["status"] == "success"]
methods = collections.Counter(r["response"].get("method") for r in ext_ok)

verdicts = collections.Counter()
for r in recs:
    if r["stage"] == "relevance" and r["status"] == "success":
        try:
            verdicts[json.loads(r["response"]["cleaned"])
                     ["verdict"].lower()] += 1
        except Exception:
            pass

searches = stage_counts.get("source_collection", 0)
llm_calls = sum(v for k, v in stage_counts.items() if k !=
                "extraction" and k != "source_collection")

e = html.escape
def short(u, n=78): return e(u if len(u) <= n else u[: n - 1] + "…")


METHOD_LABEL = {"html_parser": "HTML (BeautifulSoup)",
                "pdf_text": "PDF text (PyMuPDF)", "pdf_ocr": "OCR (pytesseract)"}
VERDICT_LABEL = {
    "direct_match": ("Direct match", "a syllabus for exactly this course"),
    "partial_match": ("Partial match", "contains this course among others"),
    "field_level": ("Field level", "about the broader field, not this course"),
    "unrelated": ("Unrelated", "not about this subject at all"),
}

# --- ranking rows, best first ----------------------------------------------
ranking = sorted(syl["source_ranking"],
                 key=lambda r: r["blended_score"], reverse=True)
rank_rows = []
for i, r in enumerate(ranking, 1):
    used = "yes" if r["structured"] else "no"
    cls_row = "used" if r["structured"] else "unused"
    pen = r["relevance_penalty"]
    pen_cell = f"×{pen:g}" if pen < 1.0 else "—"
    rank_rows.append(f"""      <tr class="{cls_row}">
        <td class="num">{i}</td>
        <td class="url"><a href="{e(r['source_url'])}">{short(r['source_url'])}</a></td>
        <td class="num">{r['domain_score']:.2f}</td>
        <td class="num">{r['content_score']:.3f}</td>
        <td class="num pen">{pen_cell}</td>
        <td class="num strong">{r['blended_score']:.3f}</td>
        <td class="num">{r['extracted_chars']:,}</td>
        <td class="used-{used}">{used}</td>
      </tr>""")

# --- units ------------------------------------------------------------------
unit_blocks = []
for u in syl["units"]:
    topics = "\n".join(
        f'          <li>{e(t["name"])}<span class="prov">{len(t["source_urls"])} source'
        f'{"s" if len(t["source_urls"]) != 1 else ""}</span></li>'
        for t in u["topics"]
    )
    unit_blocks.append(f"""      <section class="unit">
        <h3>{e(u['unit_title'])} <span class="count">{len(u['topics'])} topics</span></h3>
        <ol>
{topics}
        </ol>
      </section>""")

method_rows = "\n".join(
    f"      <tr><td>{METHOD_LABEL.get(m, m)}</td><td class='num'>{c}</td></tr>"
    for m, c in methods.most_common()
)

verdict_rows = "\n".join(
    f"""      <tr class="v-{v}">
        <td><span class="dot"></span>{VERDICT_LABEL[v][0]}</td>
        <td class="num">{verdicts.get(v, 0)}</td>
        <td class="muted">{VERDICT_LABEL[v][1]}</td>
        <td>{'kept' if v in ('direct_match', 'partial_match') else 'filtered out'}</td>
      </tr>"""
    for v in ("direct_match", "partial_match", "field_level", "unrelated")
)

contributing = "\n".join(
    f'        <li><a href="{e(u)}">{short(u, 92)}</a></li>' for u in syl["source_urls"]
)
not_structured = "\n".join(
    f'        <li><a href="{e(u)}">{short(u, 92)}</a></li>' for u in syl["collected_not_structured"]
)

page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>syllabus-agent — captured run: {e(resp['subject'])}</title>
<style>
  :root {{
    --bg: #fbfbfa; --panel: #fff; --ink: #1a1a18; --muted: #6b6b66;
    --line: #e3e2dd; --accent: #2f5d50; --accent-soft: #eef3f1;
    --warn: #8a5a00; --warn-soft: #fdf5e6; --dim: #f6f6f4;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #15161a; --panel: #1c1e23; --ink: #e8e8e6; --muted: #9a9a94;
      --line: #2e3138; --accent: #7fc0ac; --accent-soft: #1e2a27;
      --warn: #e0b160; --warn-soft: #2a2318; --dim: #212429;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--ink);
    font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }}
  .wrap {{ max-width: 1000px; margin: 0 auto; padding: 40px 24px 80px; }}
  h1 {{ font-size: 1.9rem; margin: 0 0 6px; letter-spacing: -0.02em; }}
  h2 {{ font-size: 1.15rem; margin: 44px 0 14px; padding-bottom: 8px; border-bottom: 2px solid var(--line); letter-spacing: -0.01em; }}
  h3 {{ font-size: 1rem; margin: 0 0 8px; }}
  p {{ margin: 0 0 12px; }}
  a {{ color: var(--accent); }}
  .sub {{ color: var(--muted); margin-bottom: 22px; }}
  .note {{
    background: var(--warn-soft); border-left: 3px solid var(--warn);
    padding: 14px 16px; border-radius: 0 6px 6px 0; margin: 0 0 28px; font-size: 0.93rem;
  }}
  .note strong {{ color: var(--warn); }}
  .cards {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 0 0 18px; }}
  .card {{
    background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
    padding: 12px 16px; min-width: 116px; flex: 1 1 116px;
  }}
  .card .n {{ font-size: 1.5rem; font-weight: 600; letter-spacing: -0.02em; }}
  .card .l {{ color: var(--muted); font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.05em; }}
  .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px 20px; }}
  .scroll {{ overflow-x: auto; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem; }}
  th, td {{ text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--line); }}
  th {{ font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); font-weight: 600; white-space: nowrap; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  td.strong {{ font-weight: 650; }}
  td.pen {{ color: var(--warn); }}
  td.url {{ max-width: 430px; word-break: break-all; }}
  tr.used {{ background: var(--accent-soft); }}
  .used-yes {{ color: var(--accent); font-weight: 650; }}
  .used-no {{ color: var(--muted); }}
  .dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 8px; background: var(--muted); }}
  .v-direct_match .dot {{ background: #3f8f6f; }}
  .v-partial_match .dot {{ background: #b8912f; }}
  .v-field_level .dot {{ background: #9a6b4f; }}
  .v-unrelated .dot {{ background: #a04b4b; }}
  .muted {{ color: var(--muted); }}
  .unit {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px 20px; margin-bottom: 12px; }}
  .unit .count {{ float: right; color: var(--muted); font-size: 0.78rem; font-weight: 400; }}
  .unit ol {{ margin: 0; padding-left: 22px; }}
  .unit li {{ margin: 3px 0; }}
  .prov {{ color: var(--muted); font-size: 0.76rem; margin-left: 8px; }}
  blockquote {{
    margin: 0; padding: 16px 20px; background: var(--panel);
    border-left: 3px solid var(--accent); border-radius: 0 6px 6px 0; font-size: 0.94rem;
  }}
  .two {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  @media (max-width: 720px) {{ .two {{ grid-template-columns: 1fr; }} }}
  .two ul {{ margin: 0; padding-left: 20px; font-size: 0.86rem; word-break: break-all; }}
  .two li {{ margin: 5px 0; }}
  .files {{ font-size: 0.9rem; }}
  .files code {{ background: var(--dim); padding: 2px 6px; border-radius: 4px; }}
  footer {{ margin-top: 50px; padding-top: 18px; border-top: 1px solid var(--line); color: var(--muted); font-size: 0.85rem; }}
</style>
</head>
<body>
<div class="wrap">

  <h1>syllabus-agent — captured run</h1>
  <p class="sub">Subject <strong>“{e(resp['subject'])}”</strong> · generated {e(syl['generated_at'])} · model <code>gemini-3.5-flash-lite</code></p>

  <div class="note">
    <strong>This is a captured real run, not a live demo.</strong> Every number and
    URL below came from one actual execution against live Gemini and Tavily APIs —
    nothing here is mocked or hand-written. It is a static report precisely because
    the pipeline is not reliable enough to demo live: see
    <a href="../../README.md#current-state--known-issues">Current State &amp; Known Issues</a>
    in the README for what is fragile and why this isn’t a clickable interface.
  </div>

  <h2>Classification</h2>
  <div class="panel">
    <p style="margin:0 0 6px"><strong>Route:</strong> <code>{e(cls['route'])}</code>
       &nbsp;·&nbsp; <strong>Confidence:</strong> {cls['confidence']}</p>
    <p style="margin:0" class="muted">“{e(cls['reasoning'])}”</p>
  </div>

  <h2>Run at a glance</h2>
  <div class="cards">
    <div class="card"><div class="n">{len(ext)}</div><div class="l">sources collected</div></div>
    <div class="card"><div class="n">{len(ext_ok)}</div><div class="l">extracted ok</div></div>
    <div class="card"><div class="n">{sum(verdicts[v] for v in ('direct_match','partial_match'))}</div><div class="l">passed relevance</div></div>
    <div class="card"><div class="n">{len(syl['source_urls'])}</div><div class="l">structured</div></div>
    <div class="card"><div class="n">{len(syl['units'])}</div><div class="l">final units</div></div>
    <div class="card"><div class="n">{syl['total_topics']}</div><div class="l">final topics</div></div>
    <div class="card"><div class="n">{llm_calls}</div><div class="l">LLM calls</div></div>
    <div class="card"><div class="n">{searches}</div><div class="l">searches</div></div>
  </div>

  <h2>Trust ranking</h2>
  <p class="muted" style="font-size:0.92rem">
    Sources are ranked by a blend of <strong>domain reputation</strong> (is it a
    university or known OCW platform?) and <strong>content richness</strong> (a local,
    no-API heuristic asking whether the extracted text actually looks like a syllabus).
    Reputation alone had proved misleading — <code>ocw.mit.edu</code> course-<em>admin</em>
    pages score 0.95 on domain while carrying no curriculum at all. Highlighted rows
    were structured.
  </p>
  <p class="muted" style="font-size:0.92rem">
    <strong>What this particular run shows, honestly:</strong> the blend is doing real
    work — rank 1 wins on content (0.477) despite an ordinary 0.80 domain — but it is
    not a clean win. Rank 4 is an <code>ocw.mit.edu</code> lecture-<em>video</em> listing
    with a content score of just 0.083 and 2,507 characters of text; its 0.95 domain
    score still carried it into a structuring slot. Meanwhile rank 7 (Rutgers) has the
    highest content score of any unused source (0.455) and 14,979 characters, and was
    pushed below thinner sources by the ×0.7 partial-match demotion. Reputation still
    outweighs content more than it should at the margin.
  </p>
  <div class="panel scroll">
    <table>
      <thead><tr>
        <th>#</th><th>Source</th><th>Domain</th><th>Content</th>
        <th>Relevance</th><th>Blended</th><th>Chars</th><th>Used</th>
      </tr></thead>
      <tbody>
{chr(10).join(rank_rows)}
      </tbody>
    </table>
  </div>
  <p class="muted" style="font-size:0.86rem; margin-top:10px">
    The <em>Relevance</em> column shows the partial-match demotion (×0.7). It applies
    to ranking only — the merge step is told the source’s unpenalised trust, so a
    partial match can still contribute topics.
  </p>

  <h2>Extraction</h2>
  <div class="two">
    <div class="panel">
      <h3>Outcome</h3>
      <table>
        <tr><td>Attempted</td><td class="num">{len(ext)}</td></tr>
        <tr><td>Produced text</td><td class="num">{len(ext_ok)}</td></tr>
        <tr><td>Failed</td><td class="num">{len(ext) - len(ext_ok)}</td></tr>
      </table>
    </div>
    <div class="panel">
      <h3>Method used</h3>
      <table>
{method_rows}
      </table>
    </div>
  </div>

  <h2>Relevance filter</h2>
  <p class="muted" style="font-size:0.92rem">
    One LLM call per extracted source asks whether the document is about
    <em>this course</em> or merely about the field. This is what stops a university’s
    full CS degree catalog — long, credit-bearing and superficially syllabus-shaped —
    from contributing units named “Web Development” and “Operating Systems”.
  </p>
  <div class="panel scroll">
    <table>
      <thead><tr><th>Verdict</th><th>Count</th><th>Meaning</th><th>Outcome</th></tr></thead>
      <tbody>
{verdict_rows}
      </tbody>
    </table>
  </div>

  <h2>Canonical syllabus</h2>
  <p class="muted" style="font-size:0.92rem">
    {len(syl['units'])} units, {syl['total_topics']} topics, merged from
    {syl['source_count']} sources. Unit count is organic — no target shape is imposed;
    the merge groups topics by meaning rather than by source position.
  </p>
{chr(10).join(unit_blocks)}

  <h2>How the merge reasoned</h2>
  <blockquote>{e(syl['merge_notes'])}</blockquote>

  <h2>Sources</h2>
  <div class="two">
    <div class="panel">
      <h3>Contributed to the syllabus <span class="muted">({len(syl['source_urls'])})</span></h3>
      <ul>
{contributing}
      </ul>
    </div>
    <div class="panel">
      <h3>Collected, not structured <span class="muted">({len(syl['collected_not_structured'])})</span></h3>
      <ul>
{not_structured}
      </ul>
    </div>
  </div>

  <h2>Raw evidence</h2>
  <div class="panel files">
    <p><a href="full_response.json"><code>full_response.json</code></a> — the complete
       pipeline response, including the per-source structures captured before merging.</p>
    <p><a href="trace.jsonl"><code>trace.jsonl</code></a> — every external call made
       during this run ({len(recs)} records: request, response, status, duration).
       API keys are redacted by the logging layer.</p>
    <p style="margin:0"><a href="doctor_output.txt"><code>doctor_output.txt</code></a> —
       a <code>cli doctor</code> pre-flight check from the same session.</p>
  </div>

  <footer>
    Generated from a real pipeline run on {e(syl['generated_at'][:10])}.
    Reproduce with <code>python -m syllabus_agent.cli "data structures" --verbose</code>
    — results will differ, since they depend on what the search API returns that day.
  </footer>

</div>
</body>
</html>
"""

out = BASE / "index.html"
out.write_text(page)
print(f"wrote {out} ({len(page):,} bytes)")
