"""Pure-logic tests for kettle_tui.client helpers (LAB-58 pilot suite).

No network, no TUI — just the queue/phase/ritual functions that the viewport
and the web app must agree on.
"""

from __future__ import annotations

import pytest

from kettle_tui.client import (
    ClientError,
    default_project_id,
    founding_steps,
    normalize_phase,
    ritual_controls,
    sort_queue,
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
