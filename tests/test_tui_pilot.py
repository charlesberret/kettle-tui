"""UI-level Textual Pilot tests for the TUI in demo mode (LAB-56/55/57).

Drives the real app headlessly via `App.run_test()` — booting, toggling the
diff view, gating the external-editor action, and streaming a compact. Demo
mode keeps everything offline; no editor is ever launched and no network is hit.

Async tests are wrapped in `asyncio.run` so no pytest-asyncio plugin is needed.
"""

from __future__ import annotations

import asyncio

from kettle_cli.app import KettleTui
from kettle_cli.client import KettleClient


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
