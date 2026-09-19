"""Demo-mode KettleClient behaviour (LAB-58 pilot suite).

Demo mode is the offline contract the TUI relies on for `--demo`: it must serve
projects, a queue, files, diffs, ritual mutations, and a founding progression
entirely in-memory, never touching the network.
"""

from __future__ import annotations

from kettle_tui.client import KettleClient, normalize_phase


def _client() -> KettleClient:
    return KettleClient(demo_mode=True)


def test_ping_reports_demo():
    ping = _client().ping()
    assert ping["app"] == "kettle"
    assert ping["version"] == "demo"


def test_demo_sweep_health_defaults_empty_and_is_writable():
    c = _client()
    assert c.sweep_health("demo-desk") == {}
    c._demo_sweep_health["demo-desk"] = {
        "state": "failed",
        "error": "boom",
        "updated": "2026-09-19T14:02:00",
    }
    assert c.sweep_health("demo-desk")["state"] == "failed"


def test_projects_are_the_demo_set():
    ids = {p["id"] for p in _client().projects()}
    assert {"demo-desk", "notes", "new-desk"} <= ids


def test_queue_returns_entries_for_a_founded_desk():
    q = _client().queue("demo-desk")
    assert q["curated"] == "demo"
    assert isinstance(q["entries"], list)
    assert q["entries"], "founded demo desk should have queue entries"


def test_approve_proposal_sets_founding_true():
    c = _client()
    list(c.interview_events("new-desk", ""))
    list(c.propose_events("new-desk"))
    res = c.approve("new-desk", "desk-proposal")
    assert res.get("founding") is True
    content = c.approve("demo-desk", c.queue("demo-desk")["entries"][0]["id"])
    assert content.get("founding") is not True


def test_approve_marks_entry_approved():
    c = _client()
    entry_id = c.queue("demo-desk")["entries"][0]["id"]
    res = c.approve("demo-desk", entry_id)
    assert res["status"] == "approved"
    again = c.queue("demo-desk")["entries"]
    marked = next(e for e in again if e["id"] == entry_id)
    assert marked["status"] == "approved"
    assert marked.get("approved")  # date stamp set


def test_comment_sets_and_clears():
    c = _client()
    entry_id = c.queue("demo-desk")["entries"][0]["id"]
    c.comment("demo-desk", entry_id, "  needs a tighter lede  ")
    e = next(x for x in c.queue("demo-desk")["entries"] if x["id"] == entry_id)
    assert e["comment"] == "needs a tighter lede"
    c.comment("demo-desk", entry_id, "   ")  # blank clears
    e = next(x for x in c.queue("demo-desk")["entries"] if x["id"] == entry_id)
    assert "comment" not in e


def test_entry_diff_returns_demo_unified_diff():
    c = _client()
    entry_id = c.queue("demo-desk")["entries"][0]["id"]
    diff = c.entry_diff("demo-desk", entry_id)
    assert diff.startswith("--- a/")
    assert "+++ b/" in diff


def test_file_text_has_a_body():
    c = _client()
    entry = c.queue("demo-desk")["entries"][0]
    body = c.file_text("demo-desk", entry["path"])
    assert isinstance(body, str) and body


def test_save_then_read_roundtrips_in_memory():
    c = _client()
    entry = c.queue("demo-desk")["entries"][0]
    c.save_file("demo-desk", entry["path"], "rewritten body\n")
    assert c.file_text("demo-desk", entry["path"]) == "rewritten body\n"


def test_chat_events_stream_text_then_done():
    events = list(_client().chat_events("demo-desk", "hello"))
    assert events[-1] == {"t": "done"}
    assert any(e.get("t") == "text" for e in events)


def test_founding_progression_none_to_founded():
    c = _client()
    # new-desk starts unfounded
    assert normalize_phase(c.interview_status("new-desk")) == "none"

    list(c.interview_events("new-desk", ""))  # kickoff
    list(c.propose_events("new-desk"))
    assert normalize_phase(c.interview_status("new-desk")) == "proposing"

    list(c.found_events("new-desk"))
    status = c.interview_status("new-desk")
    assert status.get("founded") is True
    assert status.get("has_charter") is True
    assert normalize_phase(status) == "founded"
    assert c.queue("new-desk")["entries"], "founding stocks a content queue"


def test_demo_interview_entries_without_charter_are_not_founded():
    c = _client()
    list(c.interview_events("new-desk", ""))
    list(c.propose_events("new-desk"))
    # Content in the queue without a founded phase (failed Found Desk).
    c._demo_queues["new-desk"] = list(c.queue("new-desk")["entries"]) + [
        {"id": "readme", "path": "README.md", "status": "pending"}
    ]
    status = c.interview_status("new-desk")
    assert status["has_entries"] is True
    assert status["founded"] is False
    assert normalize_phase(status) == "proposing"
