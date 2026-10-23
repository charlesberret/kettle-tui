"""UI-level Textual Pilot tests for the TUI in demo mode (LAB-56/55/57).

Drives the real app headlessly via `App.run_test()` — booting, toggling the
diff view, gating the external-editor action, and streaming a compact. Demo
mode keeps everything offline; no editor is ever launched and no network is hit.

Async tests are wrapped in `asyncio.run` so no pytest-asyncio plugin is needed.
"""

from __future__ import annotations

import asyncio

from textual.widgets import Input, RichLog, TextArea

from kettle_tui.app import (
    ComposerArea,
    ConfirmActionModal,
    DeskPane,
    KettleTui,
    RegisterDeskModal,
)
from kettle_tui.client import KettleClient, sweep_health_status


def _app() -> KettleTui:
    return KettleTui(KettleClient(demo_mode=True), project_id="demo-desk")


async def _settle(pilot, cond, tries: int = 60) -> bool:
    """Pump the event loop until `cond()` holds or we run out of tries."""
    for _ in range(tries):
        await pilot.pause()
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


def test_failed_sweep_health_shows_on_status():
    async def run():
        client = KettleClient(demo_mode=True)
        client._demo_sweep_health["demo-desk"] = {
            "state": "failed",
            "error": "boom",
            "updated": "2026-09-19T14:02:00",
        }
        app = KettleTui(client, project_id="demo-desk")
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _settle(pilot, lambda: bool(app.state.entries))
            assert app.state.sweep_health.get("state") == "failed"
            assert sweep_health_status(app.state.sweep_health) == "last batch did not finish"

    asyncio.run(run())


def test_refound_desk_resets_and_offers_interview():
    async def run():
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _settle(pilot, lambda: app.state.phase == "founded")
            app.action_refound_desk()
            assert await _settle(pilot, lambda: isinstance(app.screen, ConfirmActionModal))
            await pilot.press("y")
            assert await _settle(
                pilot,
                lambda: app.state.phase == "none" and not app.state.streaming,
            )
            assert app.state.entries == []
            assert await _settle(pilot, lambda: isinstance(app.screen, ConfirmActionModal))
            await pilot.press("n")
            await pilot.pause()
            assert app.state.phase == "none"
            assert not isinstance(app.screen, ConfirmActionModal)

    asyncio.run(run())


def test_register_desk_lands_in_interview():
    async def run():
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _settle(pilot, lambda: bool(app.state.entries))
            app.action_register_desk()
            assert await _settle(
                pilot, lambda: isinstance(app.screen, RegisterDeskModal)
            )
            modal = app.screen
            assert isinstance(modal, RegisterDeskModal)
            modal.query_one("#reg-root", Input).value = "/tmp/kettle-tui-reg-pilot"
            modal.query_one("#reg-note", TextArea).load_text(
                "A desk so the TUI can found a folder without leaving."
            )
            modal.go()
            assert await _settle(
                pilot,
                lambda: app.state.project_id not in (None, "demo-desk")
                and app.state.phase == "interviewing"
                and not app.state.streaming,
                tries=80,
            )

    asyncio.run(run())


def test_boots_and_loads_queue():
    async def run():
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            ok = await _settle(pilot, lambda: bool(app.state.entries))
            assert ok, "queue never loaded"
            assert app.state.view_mode == "file"
            assert app.state.selected_id is not None

    asyncio.run(run())


def test_diff_toggle_round_trip():
    """LAB-56: `v` swaps to the /api/diff view and back."""

    async def run():
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _settle(pilot, lambda: bool(app.state.entries))

            await pilot.press("v")
            assert await _settle(pilot, lambda: app.state.view_mode == "diff")
            assert app.state.file_loaded.startswith("--- a/")  # unified diff

            await pilot.press("v")
            assert await _settle(pilot, lambda: app.state.view_mode == "file")

    asyncio.run(run())


def test_external_editor_gated_in_diff_view():
    """LAB-55: Ctrl+E refuses (and never spawns) while showing a diff."""

    async def run():
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _settle(pilot, lambda: bool(app.state.entries))
            await pilot.press("v")
            assert await _settle(pilot, lambda: app.state.view_mode == "diff")

            notices: list[str] = []
            app.notify = lambda msg, **_: notices.append(str(msg))  # type: ignore[assignment]

            await pilot.press("ctrl+e")
            await pilot.pause()
            # Refused with guidance; still in diff view, no editor launched.
            assert any("file view" in n for n in notices), notices
            assert app.state.view_mode == "diff"

    asyncio.run(run())


def test_compact_streams_and_settles():
    """LAB-57: compact consumes the stream and leaves streaming cleared."""

    async def run():
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _settle(pilot, lambda: bool(app.state.entries))

            # Spy on the stream: the demo compact completes near-instantly, so
            # assert it was actually consumed rather than catch the transient
            # streaming=True flag.
            consumed = {"n": 0}
            original = app.client.compact_events

            def spy(pid, **kw):
                consumed["n"] += 1
                yield from original(pid, **kw)

            app.client.compact_events = spy  # type: ignore[assignment]

            app.action_compact()
            ok = await _settle(
                pilot,
                lambda: consumed["n"] > 0 and app.state.streaming is False,
            )
            assert ok, f"compact did not complete (consumed={consumed['n']})"

    asyncio.run(run())


def test_post_found_selects_a_content_entry():
    """LAB-53: after founding, an entry is auto-selected (proposal is gone)."""

    async def run():
        app = KettleTui(KettleClient(demo_mode=True), project_id="new-desk")
        async with app.run_test(size=(120, 40)) as pilot:
            # new-desk boots unfounded
            assert await _settle(pilot, lambda: app.state.phase == "none")

            await pilot.press("i")  # begin founding interview
            assert await _settle(
                pilot,
                lambda: app.state.phase == "interviewing" and not app.state.streaming,
            )

            await pilot.press("d")  # draft proposal → confirm modal
            assert await _settle(pilot, lambda: isinstance(app.screen, ConfirmActionModal))
            await pilot.press("y")
            assert await _settle(
                pilot,
                lambda: app.state.phase == "proposing" and not app.state.streaming,
            )
            assert app.state.selected_id == "desk-proposal"

            app.action_found_desk()  # found → confirm modal (no key binding)
            assert await _settle(pilot, lambda: isinstance(app.screen, ConfirmActionModal))
            await pilot.press("y")
            assert await _settle(
                pilot,
                lambda: app.state.phase == "founded" and not app.state.streaming,
            )

            # Post-found: proposal is gone; a real content entry is selected.
            assert app.state.selected_id not in (None, "desk-proposal")
            ids = {str(e.get("id")) for e in app.state.entries}
            assert str(app.state.selected_id) in ids

    asyncio.run(run())


def test_found_without_charter_keeps_proposing():
    """Core can file content and still fail PROJECT.md — viewport must not announce founded."""

    async def run():
        app = KettleTui(KettleClient(demo_mode=True), project_id="new-desk")
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _settle(pilot, lambda: app.state.phase == "none")
            await pilot.press("i")
            assert await _settle(
                pilot,
                lambda: app.state.phase == "interviewing" and not app.state.streaming,
            )
            await pilot.press("d")
            assert await _settle(pilot, lambda: isinstance(app.screen, ConfirmActionModal))
            await pilot.press("y")
            assert await _settle(
                pilot,
                lambda: app.state.phase == "proposing" and not app.state.streaming,
            )

            def fake_found(pid, **kw):
                app.client._demo_queues[pid] = [
                    {
                        "id": "readme",
                        "title": "README",
                        "path": "README.md",
                        "status": "pending",
                        "urgency": "low",
                    }
                ]
                # Phase stays proposing: charter was not written.
                yield {"t": "text", "s": "queued files; charter missing\n"}
                yield {"t": "done"}

            app.client.found_events = fake_found  # type: ignore[assignment]
            orig_iv = app.client.interview_status

            def iv_no_charter(pid: str, **_kw):
                data = orig_iv(pid)
                data["founded"] = False
                data["has_charter"] = False
                data["status"] = "proposing"
                data["has_entries"] = True
                data["has_proposal"] = True
                return data

            app.client.interview_status = iv_no_charter  # type: ignore[assignment]

            app.action_found_desk()
            assert await _settle(pilot, lambda: isinstance(app.screen, ConfirmActionModal))
            await pilot.press("y")
            assert await _settle(pilot, lambda: not app.state.streaming)
            assert app.state.phase == "proposing"

    asyncio.run(run())


def test_approve_founding_true_opens_found_confirm():
    """D3: Approve reads founding:true from the response, not local phase."""

    async def run():
        app = KettleTui(KettleClient(demo_mode=True), project_id="new-desk")
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _settle(pilot, lambda: app.state.phase == "none")
            await pilot.press("i")
            assert await _settle(
                pilot,
                lambda: app.state.phase == "interviewing" and not app.state.streaming,
            )
            await pilot.press("d")
            assert await _settle(pilot, lambda: isinstance(app.screen, ConfirmActionModal))
            await pilot.press("y")
            assert await _settle(
                pilot,
                lambda: app.state.phase == "proposing" and not app.state.streaming,
            )
            # Local phase is proposing, but the fork must be the approve response.
            app.state.phase = "founded"
            app.action_approve()
            assert await _settle(pilot, lambda: isinstance(app.screen, ConfirmActionModal))
            await pilot.press("y")
            assert await _settle(
                pilot,
                lambda: app.state.phase == "founded" and not app.state.streaming,
            )
            assert app.state.selected_id not in (None, "desk-proposal")

    asyncio.run(run())


def test_stream_wraps_long_lines():
    """Agent output wraps at the pane's real width, not RichLog's 78 default."""

    async def run():
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _settle(pilot, lambda: bool(app.state.entries))
            pane = app.query_one(DeskPane)
            log = pane.query_one("#stream", RichLog)
            pane.stream_write("alpha " + "x" * 300 + " omega")
            await pilot.pause()
            width = log.scrollable_content_region.width
            assert width > 0
            clipped = [s.cell_length for s in log.lines if s.cell_length > width]
            assert not clipped, f"stream lines exceed pane width {width}: {clipped}"

    asyncio.run(run())


def test_composer_grows_and_enter_sends():
    """The composer wraps into a block; Enter sends, Ctrl+J newline."""

    async def run():
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _settle(pilot, lambda: bool(app.state.entries))
            composer = app.query_one("#composer", ComposerArea)
            composer.focus()
            await pilot.pause()

            composer.load_text("hello " + "word " * 40)
            await pilot.pause()
            assert composer.wrapped_document.height > 1
            assert composer.styles.height.value > 1

            await pilot.press("ctrl+j")
            await pilot.pause()
            assert "\n" in composer.text

            await pilot.press("enter")
            await pilot.pause()
            assert composer.text == ""
            assert composer.styles.height.value == 1

    asyncio.run(run())
