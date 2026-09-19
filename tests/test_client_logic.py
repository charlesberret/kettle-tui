"""Pure-logic tests for kettle_tui.client helpers (LAB-58 pilot suite).

No network, no TUI — just the queue/phase/ritual functions that the viewport
and the web app must agree on.
"""

from __future__ import annotations

import os

import pytest

from kettle_tui.client import (
    ClientError,
    default_project_id,
    founding_steps,
    is_missing_project_folder,
    normalize_phase,
    normalize_sweep_state,
    ritual_controls,
    sort_queue,
    sweep_health_detail,
    sweep_health_status,
)


# ── default_project_id ──────────────────────────────────────────────────

PROJECTS = [{"id": "alpha"}, {"id": "beta"}, {"id": "gamma"}]


def test_default_project_prefers_explicit_preferred():
    assert default_project_id(PROJECTS, "beta") == "beta"


def test_default_project_ignores_preferred_not_present():
    # Falls through to first when the preferred id is unknown.
    assert default_project_id(PROJECTS, "nope") == "alpha"


def test_default_project_uses_env(monkeypatch):
    monkeypatch.setenv("KETTLE_PROJECT", "gamma")
    assert default_project_id(PROJECTS) == "gamma"


def test_default_project_env_must_exist(monkeypatch):
    monkeypatch.setenv("KETTLE_PROJECT", "missing")
    assert default_project_id(PROJECTS) == "alpha"


def test_default_project_none_when_empty(monkeypatch):
    monkeypatch.delenv("KETTLE_PROJECT", raising=False)
    assert default_project_id([]) is None


# ── sort_queue ──────────────────────────────────────────────────────────


def test_sort_queue_unhandled_first_then_urgency():
    entries = [
        {"id": "a", "urgency": "low", "status": "pending"},
        {"id": "b", "urgency": "high", "status": "approved"},  # handled → sinks
        {"id": "c", "urgency": "high", "status": "pending"},
        {"id": "d", "urgency": "medium", "status": "pending"},
    ]
    order = [e["id"] for e in sort_queue(entries)]
    # unhandled high, medium, low first; handled entry last
    assert order == ["c", "d", "a", "b"]


def test_sort_queue_rework_counts_as_handled():
    entries = [
        {"id": "x", "urgency": "low", "status": "rework"},
        {"id": "y", "urgency": "low", "status": "pending"},
    ]
    assert [e["id"] for e in sort_queue(entries)] == ["y", "x"]


def test_sort_queue_stable_and_nondestructive():
    entries = [{"id": "a", "urgency": "high"}, {"id": "b", "urgency": "high"}]
    out = sort_queue(entries)
    assert [e["id"] for e in out] == ["a", "b"]
    assert len(entries) == 2  # original untouched


# ── normalize_phase ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status,expected",
    [
        (None, "none"),
        ({"has_entries": True}, "none"),  # entries without charter are not founded
        ({"has_entries": True, "founded": False, "status": "proposing"}, "proposing"),
        ({"has_entries": True, "founded": True}, "founded"),
        ({"status": "interviewing"}, "interviewing"),
        ({"status": "proposing"}, "proposing"),
        ({"status": "none"}, "none"),
        ({"status": "none", "has_proposal": True}, "proposing"),
        ({"status": "weird"}, "none"),
        ({"status": "weird", "has_proposal": True}, "proposing"),
        ({"status": "founded"}, "founded"),  # legacy core: no boolean, trust status
        ({"status": "founded", "founded": False, "has_proposal": True}, "proposing"),
    ],
)
def test_normalize_phase(status, expected):
    assert normalize_phase(status) == expected


def test_client_error_curator_unavailable_points_at_doctor():
    err = ClientError(
        message="claude: command not found",
        code="curator_unavailable",
        status=500,
    )
    text = str(err)
    assert "kettle doctor" in text
    assert "(curator_unavailable)" in text


def test_client_error_queue_corrupt_is_desk_local():
    err = ClientError(message="queue.json is not valid JSON", code="queue_corrupt", status=409)
    assert "this desk only" in str(err)


def test_client_error_busy_asks_to_wait():
    err = ClientError(message="curator turn in flight", code="busy", status=409)
    assert "wait" in str(err).lower()


def test_sweep_health_quiet_when_done_or_idle():
    assert sweep_health_status({"state": "done"}) is None
    assert sweep_health_status({"state": "idle"}) is None
    assert sweep_health_status({}) is None
    assert sweep_health_detail({"state": "done"}) is None


def test_sweep_health_failed_is_visible():
    h = {
        "state": "failed",
        "error": "client disconnected; curator stopped",
        "updated": "2026-09-19T14:02:00-04:00",
    }
    assert sweep_health_status(h) == "last sweep failed"
    note = sweep_health_detail(h)
    assert note is not None
    assert "did not finish" in note
    assert "14:02" in note


def test_sweep_health_running_is_visible():
    h = {"state": "running", "pid": os.getpid(), "updated": "2026-09-19T14:02:00"}
    assert normalize_sweep_state(h) == "running"
    assert sweep_health_status(h) == "sweep running"


def test_stale_running_pid_reads_as_failed(monkeypatch):
    monkeypatch.setattr("kettle_tui.client._process_alive", lambda pid: False)
    h = {"state": "running", "pid": 1, "error": "killed", "updated": "t"}
    assert normalize_sweep_state(h) == "failed"
    assert sweep_health_status(h) == "last sweep failed"


def test_missing_project_folder_detects_core_copy():
    err = ClientError(
        message="project folder is missing: /tmp/gone — was it moved or deleted?",
        code="not_found",
        status=404,
    )
    assert is_missing_project_folder(err)
    assert not is_missing_project_folder(
        ClientError(message="unknown entry id: x", code="not_found", status=404)
    )


# ── ritual_controls ─────────────────────────────────────────────────────


def test_ritual_controls_founded_open_sitting():
    rc = ritual_controls(
        has_project=True, has_entry=True, approved=False, phase="founded"
    )
    assert rc["can_approve"] is True
    assert rc["can_comment"] is True
    assert rc["can_sweep"] is True
    assert rc["approve_label"] == "Approve"
    assert rc["interviewing"] is False and rc["proposing"] is False


def test_ritual_controls_approved_entry_cannot_reapprove():
    rc = ritual_controls(
        has_project=True, has_entry=True, approved=True, phase="founded"
    )
    assert rc["can_approve"] is False


def test_ritual_controls_streaming_locks_actions():
    rc = ritual_controls(
        has_project=True, has_entry=True, approved=False, streaming=True,
        phase="founded",
    )
    assert rc["can_approve"] is False
    assert rc["can_comment"] is False
    assert rc["can_sweep"] is False


def test_ritual_controls_proposing_relabels_approve():
    rc = ritual_controls(
        has_project=True, has_entry=True, approved=False, phase="proposing"
    )
    assert rc["proposing"] is True
    assert rc["approve_label"] == "Found Desk"
    assert rc["can_sweep"] is False  # no sweep mid-proposal


def test_ritual_controls_interviewing_blocks_sweep_and_file():
    rc = ritual_controls(
        has_project=True, has_entry=True, approved=False, phase="interviewing"
    )
    assert rc["interviewing"] is True
    assert rc["can_sweep"] is False
    assert rc["can_comment"] is False


# ── founding_steps ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "phase,expected",
    [
        ("none", (1, 3, "Begin interview")),
        ("interviewing", (1, 3, "Founding interview")),
        ("proposing", (2, 3, "Edit desk proposal")),
        ("founded", (3, 3, "Sitting open")),
    ],
)
def test_founding_steps(phase, expected):
    assert founding_steps(phase) == expected
