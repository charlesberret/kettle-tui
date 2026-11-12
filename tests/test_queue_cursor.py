"""Pilot tests: ``j`` / ``k`` move the highlighted queue row when the queue is focused."""

from __future__ import annotations

import asyncio

from textual.widgets import ListView

from kettle_tui.app import KettleTui, QueueItem
from kettle_tui.client import KettleClient


def _app() -> KettleTui:
    return KettleTui(KettleClient(demo_mode=True), project_id="demo-desk")


async def _settle(pilot, cond, tries: int = 60) -> bool:
    for _ in range(tries):
        await pilot.pause()
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


def _queue_list(app: KettleTui) -> ListView:
    return app.query_one("#queue-list", ListView)


def _highlighted_id(lv: ListView) -> str | None:
    child = lv.highlighted_child
    if isinstance(child, QueueItem):
        return str(child.entry.get("id") or "")
    return None


def test_j_moves_queue_highlight_down():
    async def run():
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _settle(pilot, lambda: len(app.state.entries) >= 2)
            lv = _queue_list(app)
            lv.focus()
            await pilot.pause()

            start = lv.index
            start_id = _highlighted_id(lv)
            assert start_id is not None

            await pilot.press("j")
            await pilot.pause()

            assert lv.index == start + 1
            assert _highlighted_id(lv) != start_id

    asyncio.run(run())


def test_k_moves_queue_highlight_up():
    async def run():
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _settle(pilot, lambda: len(app.state.entries) >= 2)
            lv = _queue_list(app)
            lv.focus()
            await pilot.pause()

            await pilot.press("j")
            await pilot.pause()
            lower_index = lv.index
            lower_id = _highlighted_id(lv)

            await pilot.press("k")
            await pilot.pause()

            assert lv.index == lower_index - 1
            assert _highlighted_id(lv) != lower_id

    asyncio.run(run())


def test_j_k_ignored_when_editor_focused():
    async def run():
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            assert await _settle(pilot, lambda: bool(app.state.entries))
            lv = _queue_list(app)
            await pilot.pause()
            before = lv.index

            app.query_one("#editor").focus()
            await pilot.press("j")
            await pilot.press("k")
            await pilot.pause()

            assert lv.index == before

    asyncio.run(run())
