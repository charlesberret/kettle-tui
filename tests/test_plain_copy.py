"""Core's plain copy, Not this, and Finish batch in the TUI.

Pins the companion contract from kettle-core's copy pass:

- labels and state lines come from the catalog (``GET /api/ui-copy``), with
  literal fallbacks equal to the catalog's text;
- Not this POSTs ``/api/set-aside`` (``undo: true`` to bring a file back), is off
  during setup and on the proposal, and reads Bring back on a set-aside file;
- the handled set is approved | rework | set-aside;
- no house word ("Sweep", "Found Desk", desk, sitting, curator, …) is rendered.

HTTP tests use an in-process server on an ephemeral port — never 4242.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from textual.widgets import Button

from kettle_tui import uicopy as uicopy_mod
from kettle_tui.app import (
    CommandPalette,
    CommentModal,
    ConfirmActionModal,
    HelpScreen,
    KettleTui,
    SweepModal,
)
from kettle_tui.client import ClientError, KettleClient, is_handled
from kettle_tui.uicopy import FALLBACK_KEYS, FALLBACK_STATES, UiCopy


async def _settle(pilot, cond, tries: int = 80) -> bool:
    for _ in range(tries):
        await pilot.pause()
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


# ── UiCopy resolution ───────────────────────────────────────────────────


def test_fallback_copy_has_the_retired_words_retired():
    c = UiCopy()
    assert c.text("sweepLabel") == "Finish batch"
    assert c.text("sweepLabel", short=True) == "Finish"
    assert c.text("approveLabel") == "Approve"
    assert c.text("notThisLabel") == "Not this"
    assert c.text("notThisUndoLabel") == "Bring back"
    assert c.keycap("sweepLabel") == "finish"


def test_live_catalog_wins_and_missing_keys_fall_back():
    live = UiCopy(
        {
            "keys": {"sweepLabel": {"text": "Wrap up", "short": "Wrap"}},
            "states": {"sweeping": {"line": "Wrapping…"}},
        },
        source="core",
    )
    assert live.text("sweepLabel") == "Wrap up"
    assert live.text("sweepLabel", short=True) == "Wrap"
    # An older core without the key still gets the right words.
    assert live.text("notThisLabel") == "Not this"
    assert live.state("sweeping", "line") == "Wrapping…"
    # A register the live state lacks falls back to the longest it has.
    assert live.state("sweeping", "full") == "Wrapping…"
    assert live.state("founded", "line") == "Ready to begin"


def test_short_falls_back_to_text():
    c = UiCopy()
    assert c.text("notThisLabel", short=True) == "Not this"  # no short in the catalog


def _core_catalog() -> dict | None:
    """Core's catalog, when a checkout or install is available (skip otherwise)."""
    env = os.environ.get("KETTLE_UI_COPY")
    if env and Path(env).is_file():
        return json.loads(Path(env).read_text(encoding="utf-8"))
    pay = uicopy_mod.in_process_payload()
    return pay


def test_fallback_literals_equal_core_catalog():
    """Set KETTLE_UI_COPY=<kettle-core>/kettle/static/ui-copy.json to run in CI."""
    cat = _core_catalog()
    if not cat:
        pytest.skip("no kettle-core catalog available (set KETTLE_UI_COPY)")
    keys = cat.get("keys") or {}
    for key, rec in FALLBACK_KEYS.items():
        assert key in keys, f"core catalog has no {key}"
        for field, value in rec.items():
            assert keys[key].get(field) == value, f"{key}.{field} drifted from core"
    states = cat.get("states") or {}
    for name, rec in FALLBACK_STATES.items():
        for reg, value in rec.items():
            assert states.get(name, {}).get(reg) == value, f"states.{name}.{reg} drifted"


# ── HTTP: /api/ui-copy and /api/set-aside ───────────────────────────────


class _Core(BaseHTTPRequestHandler):
    posts: list[tuple[str, dict]] = []
    catalog: dict | None = None

    def _send(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/ui-copy") and self.catalog is not None:
            self._send(200, self.catalog)
        else:
            self._send(404, {"error": "not found", "code": "not_found"})

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        type(self).posts.append((self.path, body))
        if self.path == "/api/set-aside":
            if body.get("id") == "desk-proposal":
                self._send(
                    400,
                    {
                        "error": "the proposal is approved or edited, not set aside",
                        "code": "bad_request",
                    },
                )
                return
            self._send(200, {"ok": True, "status": "pending" if body.get("undo") else "set-aside"})
            return
        self._send(404, {"error": "not found", "code": "not_found"})

    def log_message(self, format, *args):  # noqa: A002
        return


@pytest.fixture
def core():
    _Core.posts = []
    _Core.catalog = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Core)
    assert server.server_address[1] != 4242
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield _Core, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_set_aside_posts_and_undo_posts_undo_true(core):
    handler, url = core
    c = KettleClient(url=url, token="t")
    assert c.set_aside("p", "readme")["status"] == "set-aside"
    assert c.set_aside("p", "readme", undo=True)["status"] == "pending"
    assert handler.posts == [
        ("/api/set-aside", {"project": "p", "id": "readme"}),
        ("/api/set-aside", {"project": "p", "id": "readme", "undo": True}),
    ]


def test_set_aside_on_proposal_surfaces_core_refusal(core):
    _, url = core
    c = KettleClient(url=url, token="t")
    with pytest.raises(ClientError) as ei:
        c.set_aside("p", "desk-proposal")
    assert ei.value.status == 400
    assert "proposal" in ei.value.message


def test_ui_copy_reads_core_catalog_with_overrides(core):
    handler, url = core
    handler.catalog = {
        "_schema": 1,
        "keys": {"sweepLabel": {"text": "Close up", "short": "Close"}},
        "states": {},
        "overridden": ["sweepLabel"],
    }
    copy = KettleClient(url=url, token="t").ui_copy()
    assert copy.source == "core"
    assert copy.text("sweepLabel", short=True) == "Close"


def test_ui_copy_falls_back_when_core_predates_it(core, monkeypatch):
    _, url = core  # catalog None → 404
    monkeypatch.setattr("kettle_tui.client.in_process_payload", lambda: None)
    copy = KettleClient(url=url, token="t").ui_copy()
    assert copy.source == "fallback"
    assert copy.text("sweepLabel") == "Finish batch"


# ── demo client ─────────────────────────────────────────────────────────


def test_demo_set_aside_round_trip_and_proposal_refused():
    c = KettleClient(demo_mode=True)
    eid = c.queue("demo-desk")["entries"][0]["id"]
    assert c.set_aside("demo-desk", eid)["status"] == "set-aside"
    e = next(x for x in c.queue("demo-desk")["entries"] if x["id"] == eid)
    assert e["status"] == "set-aside" and e.get("set_aside")
    assert is_handled(e)
    assert c.set_aside("demo-desk", eid, undo=True)["status"] == "pending"
    e = next(x for x in c.queue("demo-desk")["entries"] if x["id"] == eid)
    assert e["status"] == "pending" and "set_aside" not in e

    list(c.interview_events("new-desk", ""))
    list(c.propose_events("new-desk"))
    with pytest.raises(ClientError) as ei:
        c.set_aside("new-desk", "desk-proposal")
    assert ei.value.status == 400


# ── pilot: labels, Not this, approve ────────────────────────────────────


def _label(app: KettleTui, sel: str) -> str:
    return str(app.query_one(sel, Button).label)


def test_ritual_labels_come_from_the_catalog():
    async def run():
        client = KettleClient(demo_mode=True)
        client.ui_copy = lambda: UiCopy(  # type: ignore[method-assign]
            {
                "keys": {
                    "sweepLabel": {"text": "Wrap up", "short": "Wrap up"},
                    "notThisLabel": {"text": "Skip it"},
                    "approveLabel": {"text": "Keep", "short": "keep"},
                }
            },
            source="core",
        )
        app = KettleTui(client, project_id="demo-desk")
        async with app.run_test(size=(140, 40)) as pilot:
            assert await _settle(pilot, lambda: bool(app.state.entries))
            await pilot.pause()
            assert _label(app, "#btn-sweep") == "f·wrap up"
            assert _label(app, "#btn-not-this") == "x·skip it"
            assert _label(app, "#btn-approve") == "a·keep"
            assert _label(app, "#btn-comment") == "c·comment"  # falls back
            footer = app.footer_descriptions()
            assert footer["sweep"] == "Wrap up"
            assert footer["not_this"] == "Skip it"
            assert footer["approve"] == "Keep"

    asyncio.run(run())


def test_default_labels_are_finish_and_not_this():
    async def run():
        app = KettleTui(KettleClient(demo_mode=True), project_id="demo-desk")
        async with app.run_test(size=(140, 40)) as pilot:
            assert await _settle(pilot, lambda: bool(app.state.entries))
            await pilot.pause()
            assert _label(app, "#btn-approve") == "a·approve"
            assert _label(app, "#btn-comment") == "c·comment"
            assert _label(app, "#btn-not-this") == "x·not this"
            assert _label(app, "#btn-sweep") == "f·finish"
            assert app.footer_descriptions()["sweep"] == "Finish"

    asyncio.run(run())


def test_not_this_sets_aside_then_brings_back():
    async def run():
        client = KettleClient(demo_mode=True)
        calls: list[tuple[str, bool]] = []
        original = client.set_aside

        def spy(pid, eid, *, undo=False):
            calls.append((eid, undo))
            return original(pid, eid, undo=undo)

        client.set_aside = spy  # type: ignore[method-assign]
        app = KettleTui(client, project_id="demo-desk")
        async with app.run_test(size=(140, 40)) as pilot:
            assert await _settle(pilot, lambda: bool(app.state.entries))
            first = str(app.state.selected_id)
            assert await _settle(pilot, lambda: app.state.file_path is not None)

            await pilot.press("x")
            assert await _settle(
                pilot, lambda: (app._entry_by_id(first) or {}).get("status") == "set-aside"
            )
            assert calls == [(first, False)]
            # Selection stays; the control now reads Bring back.
            assert str(app.state.selected_id) == first
            assert _label(app, "#btn-not-this") == "x·bring back"
            # The summary still belongs to the open file after the re-sort.
            await pilot.pause()
            ask = str((app._entry_by_id(first) or {}).get("ask") or "")
            assert await _settle(pilot, lambda: ask[:20] in app.query_one("#brief-md").source)
            # All four keycaps fit, whole, at the narrowest agent pane.
            app.set_desk_width(0)
            await pilot.pause()
            await pilot.pause()
            shown = _screen_text(app)
            for cap in ("a·approve", "c·comment", "x·bring back", "f·finish"):
                assert cap in shown, cap
            # Set-aside sinks with the handled files, below every pending one.
            ids = [str(e.get("id")) for e in app.state.entries]
            pending_idx = [i for i, e in enumerate(app.state.entries) if not is_handled(e)]
            assert ids.index(first) > max(pending_idx)
            assert "not this" not in str(app.query_one("#status").render()).lower()

            await pilot.press("x")
            assert await _settle(
                pilot, lambda: (app._entry_by_id(first) or {}).get("status") == "pending"
            )
            assert calls == [(first, False), (first, True)]
            assert _label(app, "#btn-not-this") == "x·not this"

    asyncio.run(run())


async def _to_proposing(app: KettleTui, pilot) -> None:
    assert await _settle(pilot, lambda: app.state.phase == "none")
    await pilot.press("i")
    assert await _settle(
        pilot, lambda: app.state.phase == "interviewing" and not app.state.streaming
    )
    await pilot.press("d")
    assert await _settle(pilot, lambda: isinstance(app.screen, ConfirmActionModal))
    await pilot.press("y")
    assert await _settle(
        pilot, lambda: app.state.phase == "proposing" and not app.state.streaming
    )


def test_proposal_reads_approve_and_not_this_is_off():
    async def run():
        client = KettleClient(demo_mode=True)
        calls: list[str] = []
        client.set_aside = lambda *a, **k: calls.append("called") or {}  # type: ignore[method-assign]
        app = KettleTui(client, project_id="new-desk")
        async with app.run_test(size=(140, 40)) as pilot:
            await _to_proposing(app, pilot)
            assert app.state.selected_id == "desk-proposal"
            await pilot.pause()
            # The status line is the catalog's state line, never the phase identifier.
            status = str(app.query_one("#status").render())
            assert "Edit the proposal" in status
            assert "proposing" not in status
            assert _label(app, "#btn-approve") == "a·approve"
            assert app.query_one("#btn-approve", Button).disabled is False
            assert app.query_one("#btn-not-this", Button).disabled is True
            assert app.query_one("#btn-sweep", Button).disabled is True
            await pilot.press("x")
            await pilot.pause()
            assert calls == []
            # The hint changes with the phase; the word does not.
            assert app.query_one("#btn-approve", Button).tooltip == (
                "Approve the proposal to set up your project"
            )

    asyncio.run(run())


# ── nothing house-shaped on screen ──────────────────────────────────────

# House words (kettle-core kits/_lexicon.json, audience "house") and retired labels.
FORBIDDEN = [
    r"\bSweep\b",
    r"\bsweep\b",
    r"Found Desk",
    r"\b[Dd]esks?\b",
    r"\b[Ss]itting\b",
    r"\b[Cc]urators?\b",
    r"\b[Rr]ulings?\b",
    r"\b[Cc]harter\b",
    r"\b[Ff]ounding\b",
    r"\b[Rr]efound",
    r"\b[Ee]ntry\b",
    r"\b[Ee]ntries\b",
    r"\bbrief\b",
]
# Identifiers that may legitimately be shown: demo project ids, the app name.
IDENTIFIERS = ("demo-desk", "new-desk", "kettle-tui", "DESK-PROPOSAL")


def _screen_text(app: KettleTui) -> str:
    svg = app.export_screenshot()
    # Join runs with nothing: Textual draws real spaces as characters, and a styled key
    # letter (bold "a" in "a·approve") is its own run.
    text = re.sub(r"<[^>]+>", "", svg)
    text = html.unescape(text).replace("\xa0", " ")
    # Drop the SVG's stylesheet/definitions noise: keep only visible-ish runs.
    for ident in IDENTIFIERS:
        text = text.replace(ident, "")
    return text


def _assert_plain(app: KettleTui, where: str) -> None:
    text = _screen_text(app)
    hits = [p for p in FORBIDDEN if re.search(p, text)]
    assert not hits, f"{where}: house words on screen {hits}"


def test_no_house_words_on_screen_founded_and_modals():
    async def run():
        app = KettleTui(KettleClient(demo_mode=True), project_id="demo-desk")
        async with app.run_test(size=(160, 44)) as pilot:
            assert await _settle(pilot, lambda: app.state.file_path is not None)
            await pilot.pause()
            _assert_plain(app, "founded")
            # The scan reads real screen text (guards against an empty extraction).
            shown = _screen_text(app)
            assert "f·finish" in shown and "x·not this" in shown and "Finish" in shown
            assert "pending" in shown  # the status row is on screen, not under the footer

            await pilot.press("x")
            assert await _settle(
                pilot, lambda: any(e.get("status") == "set-aside" for e in app.state.entries)
            )
            await pilot.pause()
            _assert_plain(app, "after Not this")

            app.action_sweep()
            assert await _settle(pilot, lambda: isinstance(app.screen, SweepModal))
            _assert_plain(app, "finish modal")
            await pilot.press("escape")
            await pilot.pause()

            app.action_comment()
            assert await _settle(pilot, lambda: isinstance(app.screen, CommentModal))
            _assert_plain(app, "comment modal")
            await pilot.press("escape")
            await pilot.pause()

            app.action_palette()
            assert await _settle(pilot, lambda: isinstance(app.screen, CommandPalette))
            _assert_plain(app, "palette")
            await pilot.press("escape")
            await pilot.pause()

            app.action_help()
            assert await _settle(pilot, lambda: isinstance(app.screen, HelpScreen))
            _assert_plain(app, "help")
            await pilot.press("escape")
            await pilot.pause()

            app.action_refound_desk()
            assert await _settle(pilot, lambda: isinstance(app.screen, ConfirmActionModal))
            _assert_plain(app, "set up again modal")
            await pilot.press("n")
            await pilot.pause()

            # Finish the batch and read the stream it leaves behind.
            app.action_sweep()
            assert await _settle(pilot, lambda: isinstance(app.screen, SweepModal))
            app.screen.go()
            assert await _settle(pilot, lambda: not app.state.streaming, tries=120)
            await pilot.pause()
            _assert_plain(app, "after Finish batch")

    asyncio.run(run())


def test_no_house_words_on_screen_through_setup():
    async def run():
        app = KettleTui(KettleClient(demo_mode=True), project_id="new-desk")
        async with app.run_test(size=(160, 44)) as pilot:
            assert await _settle(pilot, lambda: app.state.phase == "none")
            await pilot.pause()
            _assert_plain(app, "not set up")
            await _to_proposing(app, pilot)
            await pilot.pause()
            _assert_plain(app, "proposing")

            app.action_approve()
            assert await _settle(pilot, lambda: isinstance(app.screen, ConfirmActionModal))
            _assert_plain(app, "approve proposal modal")
            await pilot.press("y")
            assert await _settle(
                pilot, lambda: app.state.phase == "founded" and not app.state.streaming
            )
            await pilot.pause()
            _assert_plain(app, "set up")

    asyncio.run(run())
