"""Trace-writer tests."""

import json

from syllabus_agent.logging_setup import configure_logging, record_call


def test_line_separator_in_content_does_not_split_a_trace_record(tmp_path):
    """json.dumps leaves U+2028 unescaped under ensure_ascii=False, and Python's
    splitlines() treats it as a line break — so one scraped page containing it
    made a valid record look like two broken ones.
    """
    log_file = configure_logging(log_dir=tmp_path, run_label="test")

    record_call(
        call_type="extraction",
        request={"url": "https://x.edu/a"},
        response={"preview": "line one\u2028line two\u2029para two"},
        status="success",
        duration_ms=1.0,
    )

    raw = log_file.read_text()

    # One record, however the reader splits.
    assert len([l for l in raw.split("\n") if l.strip()]) == 1
    assert len([l for l in raw.splitlines() if l.strip()]) == 1

    entry = json.loads(raw.strip())
    assert entry["call_type"] == "extraction"
    # The characters survive the round-trip, just escaped on disk.
    assert "\u2028" in entry["response"]["preview"]
