"""Pilot tests for queue row width, truncation, and styling at narrow queue widths."""

from __future__ import annotations

import asyncio
from typing import Any

from textual.app import App
from textual.widgets import Label, Static

from kettle_tui.app import (
    QUEUE_W_MAX,
    QUEUE_W_MIN,
    KettleTui,
    QueueItem,
    QueuePane,
)
from kettle_tui.client import KettleClient


def _line_contents(entry: dict[str, Any], queue_width: int) -> list[Any]:
    """Mount one ``QueueItem`` and return rendered line ``Content`` objects."""

    class Host(App):
        def __init__(self) -> None:
            super().__init__()
            self.queue_width = queue_width

        def compose(self):
            yield QueueItem(entry)

    async def run() -> list[Any]:
        async with Host().run_test() as pilot:
            await pilot.pause()
            item = pilot.app.query_one(QueueItem)
            return [label.render() for label in item.query(Label)]

    return asyncio.run(run())


def _assert_one_row(plain: str) -> None:
    assert "\n" not in plain, f"row wrapped: {plain!r}"


def _filename_style(entry: dict[str, Any], queue_width: int) -> str:
    line1 = _line_contents(entry, queue_width)[0]
    for span in line1.spans:
        if span.start >= 2:
            return str(span.style)
    return ""


def test_second_line_stays_one_row_at_minimum_queue_width():
    entry = {
        "id": "brief",
        "title": "README — " + ("keep this brief on one row " * 5),
        "path": "README.md",
        "status": "pending",
        "urgency": "low",
        "ask": "fallback ask text",
    }
    lines = _line_contents(entry, QUEUE_W_MIN)
    assert len(lines) == 2
    _assert_one_row(lines[1].plain)
    assert "…" in lines[1].plain


def test_diffstat_stays_on_first_row_at_minimum_queue_width():
    entry = {
        "id": "big-diff",
        "title": "Change log",
        "path": "a.py",
        "status": "pending",
        "urgency": "high",
        "diff": {"added": 42, "removed": 17},
    }
    lines = _line_contents(entry, QUEUE_W_MIN)
    assert len(lines) >= 1
    line1 = lines[0].plain
    _assert_one_row(line1)
    assert "+42" in line1
    assert line1.count("■") == 1


def test_long_title_truncates_with_ellipsis_and_does_not_wrap():
    fname = "x" * 40 + ".py"
    entry = {
        "id": "long-name",
        "title": "ignored for path rows",
        "path": fname,
        "status": "pending",
        "urgency": "medium",
    }
    line1 = _line_contents(entry, QUEUE_W_MIN)[0]
    _assert_one_row(line1.plain)
    assert "…" in line1.plain
    assert fname not in line1.plain


def test_handled_and_set_aside_styling_survives_narrowing():
    approved = {
        "id": "ap",
        "path": "handled.py",
        "title": "Handled",
        "status": "approved",
        "urgency": "low",
    }
    aside = {
        "id": "sa",
        "path": "aside.py",
        "title": "Aside — secondary note",
        "status": "set-aside",
        "urgency": "medium",
        "ask": "set aside for now",
    }
    assert _filename_style(approved, QUEUE_W_MIN) == "dim"
    assert _filename_style(aside, QUEUE_W_MIN) == "dim italic"
    aside_lines = _line_contents(aside, QUEUE_W_MIN)
    assert len(aside_lines) == 2
    second_styles = {str(s.style) for s in aside_lines[1].spans}
    assert "dim italic" in second_styles


def test_widening_restores_full_filename_text():
    fname = "midlength_queue_filename.py"
    entry = {
        "id": "resize",
        "title": "Resize check",
        "path": fname,
        "status": "pending",
        "urgency": "low",
    }
    narrow = _line_contents(entry, QUEUE_W_MIN)[0].plain
    wide = _line_contents(entry, QUEUE_W_MAX)[0].plain
    _assert_one_row(narrow)
    _assert_one_row(wide)
    assert "…" in narrow
    assert "…" not in wide
    assert fname in wide


def test_empty_queue_renders():
    async def run() -> None:
        app = KettleTui(KettleClient(demo_mode=True), project_id="demo-desk")
        async with app.run_test(size=(120, 40)) as pilot:
            for _ in range(40):
                await pilot.pause()
                if app.query_one(QueuePane).entries:
                    break
                await asyncio.sleep(0.05)

            qp = app.query_one(QueuePane)
            qp.entries = []
            await pilot.pause()

            lv = qp.query_one("#queue-list")
            assert list(lv.children) == []
            title = qp.query_one(".pane-title", Static).render().plain
            assert title.endswith("0")

    asyncio.run(run())
