"""kettle-tui — plugin-shaped sitting (Shape B).

Layout (see kettle-core/docs/UI-STRUCTURE.md + desktop v2):

    Queue  ·  Editor  ·  Desk (brief → ritual → stream → composer)
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Markdown,
    OptionList,
    RichLog,
    Static,
    TextArea,
)
from textual.widgets.option_list import Option

# Column width defaults / clamps (terminal cells).
# Desk min = room for the three ritual keycaps without clipping:
#   "a·approve" (9) + "c·comment" (9) + "s·sweep" (7)
#   + inter-button margins (1×2) + button pad (2×3) + desk pad (2) = 35
QUEUE_W_DEFAULT = 28
DESK_STRIP_MIN = 35  # hard floor so a·approve / c·comment / s·sweep always fit
DESK_W_DEFAULT = 52
QUEUE_W_MIN, QUEUE_W_MAX = 18, 48
DESK_W_MIN, DESK_W_MAX = DESK_STRIP_MIN, 80
EDITOR_W_MIN = 24

from .client import (
    ClientError,
    KettleClient,
    default_project_id,
    founding_steps,
    is_missing_project_folder,
    normalize_phase,
    ritual_controls,
    sort_queue,
)

# ── helpers ─────────────────────────────────────────────────────────────


def _file_name(entry: dict[str, Any] | None) -> str:
    if not entry:
        return "—"
    path = entry.get("path") or ""
    if path:
        return Path(str(path)).name
    title = entry.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return str(entry.get("id") or "?")


def _second_line(entry: dict[str, Any]) -> str:
    """Web-like line 2: optional ask_type + title/ask snippet (not path)."""
    title = (entry.get("title") or "").strip()
    ask = (entry.get("ask") or "").strip()
    # Prefer title after em-dash (web does this for path-bearing rows)
    summary = title
    for sep in (" — ", " – ", " - "):
        if sep in title:
            summary = title.split(sep, 1)[1].strip()
            break
    if not summary:
        summary = ask
    tag = entry.get("ask_type") or ""
    if tag:
        return f"{tag}  {summary}" if summary else str(tag)
    return summary


def _diff_label(diff: Any) -> str:
    if not isinstance(diff, dict):
        return ""
    parts = []
    if diff.get("added"):
        parts.append(f"+{diff['added']}")
    if diff.get("removed"):
        parts.append(f"-{diff['removed']}")
    return " ".join(parts)


def _urgency_style(urgency: str | None) -> str:
    u = (urgency or "").lower()
    return {
        "high": "bold #e06c75",
        "medium": "bold #e5c07b",
        "low": "#98c379",
    }.get(u, "dim")


def _brief_md(entry: dict[str, Any] | None) -> str:
    if not entry:
        return ""
    ask = (entry.get("ask") or "").strip()
    brief = (entry.get("brief") or "").strip() or "_No brief._"
    parts = []
    if ask:
        parts.append(f"> {ask}\n")
    parts.append(brief)
    return "\n".join(parts)


def _is_approved(entry: dict[str, Any] | None) -> bool:
    if not entry:
        return False
    st = (entry.get("status") or "").lower()
    return st == "approved" or bool(entry.get("approved"))


# ── resize handles ──────────────────────────────────────────────────────


class VSplitHandle(Static):
    """Full-height hairline drag handle between horizontal panes.

    One cell wide (terminal minimum). Paints ``│`` on **every row** so the
    divider runs the whole column height — not a single glyph in the middle.

    ``side``:
      - ``"queue"``: left of editor — drag changes queue width
      - ``"desk"``:  right of editor — drag changes desk width
    """

    DEFAULT_CSS = """
    VSplitHandle {
        width: 1;
        height: 1fr;
        min-width: 1;
        max-width: 1;
        background: transparent;
        color: #3a3f4f;
        padding: 0;
        margin: 0;
        border: none;
        overflow: hidden;
    }
    VSplitHandle:hover {
        background: transparent;
        color: #5a6078;  /* slight lift from default, not a bright accent */
    }
    VSplitHandle.-dragging {
        background: transparent;
        color: #6c7390;  /* muted cool gray — quieter than hover-blue */
    }
    """

    def __init__(self, side: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.side = side  # "queue" | "desk"
        self._dragging = False
        self._origin_x = 0
        self._origin_w = 0

    def render(self) -> str:
        # Fill the full height with light box-drawing verticals.
        h = max(self.size.height, 1)
        return "\n".join("│" for _ in range(h))

    def on_resize(self, event: events.Resize) -> None:
        self.refresh()

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button != 1:
            return
        event.stop()
        app = self.app
        if not isinstance(app, KettleTui):
            return
        self._dragging = True
        self.add_class("-dragging")
        self.capture_mouse()
        self._origin_x = event.screen_x
        self._origin_w = app.queue_width if self.side == "queue" else app.desk_width

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if not self._dragging:
            return
        event.stop()
        self._dragging = False
        self.remove_class("-dragging")
        self.release_mouse()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if not self._dragging:
            return
        event.stop()
        app = self.app
        if not isinstance(app, KettleTui):
            return
        dx = event.screen_x - self._origin_x
        if self.side == "queue":
            app.set_queue_width(self._origin_w + dx)
        else:
            app.set_desk_width(self._origin_w - dx)


# ── panes ───────────────────────────────────────────────────────────────


class QueueItem(ListItem):
    def __init__(self, entry: dict[str, Any]) -> None:
        self.entry = entry
        super().__init__()

    def compose(self) -> ComposeResult:
        e = self.entry
        fname = _file_name(e)
        ruled = _is_approved(e) or (e.get("status") or "").lower() == "rework"
        style = "dim" if ruled else "bold"
        urg = e.get("urgency")
        note = " [#f9e2af]·[/]" if e.get("comment") else ""
        diff = _diff_label(e.get("diff"))
        # Color square (urgency) · file name · optional note · diffstat
        yield Label(
            f"[{_urgency_style(urg)}]■[/] [{style}]{fname}[/]{note}"
            + (f"  [dim]{diff}[/]" if diff else "")
        )
        line2 = _second_line(e)
        if line2:
            yield Label(f"[dim]{line2}[/]")


class QueuePane(Vertical):
    """Left: project + curated queue (no filter)."""

    entries: reactive[list[dict[str, Any]]] = reactive(list, always_update=True)
    project_label: reactive[str] = reactive("—")

    def compose(self) -> ComposeResult:
        yield Static("queue", classes="pane-title")
        yield Static("—", id="project-label", classes="project-label")
        yield ListView(id="queue-list")

    def watch_project_label(self, value: str) -> None:
        try:
            self.query_one("#project-label", Static).update(value)
        except Exception:
            pass

    def watch_entries(self, entries: list[dict[str, Any]]) -> None:
        self._rebuild()

    def _rebuild(self) -> None:
        lv = self.query_one("#queue-list", ListView)
        lv.clear()
        for e in self.entries:
            lv.append(QueueItem(e))
        title = self.query_one(".pane-title", Static)
        title.update(f"queue  {len(self.entries)}")


class EditorPane(Vertical):
    """Center: file surface (or read-only diff)."""

    def compose(self) -> ComposeResult:
        yield Static("editor", classes="pane-title", id="editor-title")
        yield Static("", id="file-path", classes="file-path")
        yield TextArea(id="editor", language=None, soft_wrap=True)

    def set_mode_title(self, mode: str) -> None:
        title = "diff" if mode == "diff" else "editor"
        self.query_one("#editor-title", Static).update(title)

    def set_path(self, path: str, *, dirty: bool = False, mode: str = "file") -> None:
        mark = " ·" if dirty else ""
        prefix = "diff  " if mode == "diff" else ""
        self.query_one("#file-path", Static).update(f"{prefix}{path}{mark}")
        self.set_mode_title(mode)

    def set_text(self, text: str, *, read_only: bool = False) -> None:
        ta = self.query_one("#editor", TextArea)
        ta.load_text(text)
        ta.read_only = read_only

    def set_read_only(self, read_only: bool) -> None:
        self.query_one("#editor", TextArea).read_only = read_only

    def get_text(self) -> str:
        return self.query_one("#editor", TextArea).text


class DeskPane(Vertical):
    """Right: step banner → brief → ritual / founding actions → stream → composer."""

    def compose(self) -> ComposeResult:
        yield Static("desk", classes="pane-title")
        # Wizard-style step strip (tuillem/dialog pattern: Step N/M · title)
        yield Static("", id="founding-step", classes="founding-step")
        with VerticalScroll(id="brief-scroll"):
            yield Markdown("", id="brief-md")
        # One-row keycap strip — shortcuts are primary; clicks are secondary (TUI density).
        with Horizontal(id="ritual", classes="action-strip"):
            yield Button("a·approve", id="btn-approve", compact=True, flat=True)
            yield Button("c·comment", id="btn-comment", compact=True, flat=True)
            yield Button("s·sweep", id="btn-sweep", compact=True, flat=True)
        with Horizontal(id="founding-actions", classes="action-strip founding-actions"):
            yield Button("i·interview", id="btn-interview", compact=True, flat=True)
            yield Button("d·draft", id="btn-propose", compact=True, flat=True)
            yield Button("found", id="btn-found", compact=True, flat=True)
        yield Static("", id="comment-hint", classes="hint")
        yield RichLog(
            id="stream",
            wrap=True,
            markup=True,
            max_lines=800,
            auto_scroll=True,
        )
        yield Input(placeholder="", id="composer")

    def show_brief(self, entry: dict[str, Any] | None) -> None:
        self.query_one("#brief-md", Markdown).update(_brief_md(entry))

    def set_founding_chrome(self, phase: str) -> None:
        """Show/hide step banner + founding buttons for the current phase."""
        step_i, step_n, title = founding_steps(phase)
        banner = self.query_one("#founding-step", Static)
        actions = self.query_one("#founding-actions")
        ritual = self.query_one("#ritual")
        composer = self.query_one("#composer", Input)
        # Placeholders stay empty — no permanent how-to text on the sitting.
        composer.placeholder = ""

        if phase == "founded":
            banner.display = False
            actions.display = False
            ritual.display = True
            return

        banner.display = True
        actions.display = True
        # During founding, ritual is hidden (except proposing uses approve→found)
        if phase == "proposing":
            ritual.display = True
            actions.display = True
        else:
            ritual.display = False

        # Phase label only (state), not usage instructions
        banner.update(f"  {step_i}/{step_n}  {title}")

        bi = self.query_one("#btn-interview", Button)
        bp = self.query_one("#btn-propose", Button)
        bf = self.query_one("#btn-found", Button)
        bi.display = phase in ("none", "interviewing")
        bp.display = phase in ("none", "interviewing")
        bf.display = phase == "proposing"
        # Primary emphasis via enabled state
        bi.disabled = phase not in ("none", "interviewing")
        bp.disabled = phase not in ("none", "interviewing")
        bf.disabled = phase != "proposing"
        if phase == "none":
            bi.label = "i·interview"
            bp.label = "d·draft"
        elif phase == "interviewing":
            bi.label = "i·again"
            bp.label = "d·draft"
        elif phase == "proposing":
            bf.label = "found"

    def set_ritual(
        self,
        *,
        can_approve: bool,
        can_comment: bool,
        can_sweep: bool,
        approve_label: str = "Approve",
    ) -> None:
        ba = self.query_one("#btn-approve", Button)
        bc = self.query_one("#btn-comment", Button)
        bs = self.query_one("#btn-sweep", Button)
        # Keep key·action labels short; founding uses a·found
        ba.label = "a·found" if approve_label == "Found Desk" else "a·approve"
        ba.disabled = not can_approve
        bc.disabled = not can_comment
        bs.disabled = not can_sweep

    def stream_write(self, text: str, *, style: str | None = None) -> None:
        log = self.query_one("#stream", RichLog)
        if style:
            log.write(f"[{style}]{text}[/]")
        else:
            log.write(text)

    def stream_clear(self) -> None:
        self.query_one("#stream", RichLog).clear()


# ── modals ──────────────────────────────────────────────────────────────


class CommentModal(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, initial: str = "") -> None:
        super().__init__()
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical(id="modal"):
            yield Static("comment — note for the sweep", classes="pane-title")
            yield TextArea(id="comment-body", soft_wrap=True)
            with Horizontal(classes="modal-actions"):
                yield Button("save", id="comment-save", variant="primary")
                yield Button("cancel", id="comment-cancel")

    def on_mount(self) -> None:
        ta = self.query_one("#comment-body", TextArea)
        ta.load_text(self._initial)
        ta.focus()

    @on(Button.Pressed, "#comment-save")
    def save(self) -> None:
        self.dismiss(self.query_one("#comment-body", TextArea).text)

    @on(Button.Pressed, "#comment-cancel")
    def cancel_btn(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class SweepModal(ModalScreen[str | None]):
    """Confirm sweep; optional thrust steers the curator. None = cancel."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def compose(self) -> ComposeResult:
        with Vertical(id="modal"):
            yield Static("sweep — close this sitting", classes="pane-title")
            yield Input(
                placeholder="",
                id="sweep-thrust",
            )
            with Horizontal(classes="modal-actions"):
                yield Button("run sweep", id="sweep-go", variant="primary")
                yield Button("cancel", id="sweep-cancel")

    def on_mount(self) -> None:
        self.query_one("#sweep-thrust", Input).focus()

    @on(Button.Pressed, "#sweep-go")
    def go(self) -> None:
        self.dismiss(self.query_one("#sweep-thrust", Input).value)

    @on(Button.Pressed, "#sweep-cancel")
    def cancel_btn(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted, "#sweep-thrust")
    def submit_thrust(self) -> None:
        self.dismiss(self.query_one("#sweep-thrust", Input).value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class DiscardModal(ModalScreen[str | None]):
    """Unsaved edits: save | discard | cancel. Returns action id."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, message: str = "Unsaved edits in the editor.") -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-sm"):
            yield Static("unsaved changes", classes="pane-title")
            yield Static(self._message, classes="hint")
            with Horizontal(classes="modal-actions"):
                yield Button("save", id="discard-save", variant="primary")
                yield Button("discard", id="discard-drop")
                yield Button("cancel", id="discard-cancel")

    @on(Button.Pressed, "#discard-save")
    def save(self) -> None:
        self.dismiss("save")

    @on(Button.Pressed, "#discard-drop")
    def drop(self) -> None:
        self.dismiss("discard")

    @on(Button.Pressed, "#discard-cancel")
    def cancel_btn(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmActionModal(ModalScreen[str | None]):
    """Single-purpose confirm (dialog/gum pattern). Returns 'ok' or None."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("enter", "ok", "OK", show=False),
        Binding("y", "ok", "OK", show=False),
        Binding("n", "cancel", "Cancel", show=False),
    ]

    def __init__(
        self,
        title: str,
        body: str,
        *,
        ok_label: str = "ok",
        cancel_label: str = "cancel",
    ) -> None:
        super().__init__()
        self._title = title
        self._body = body
        self._ok = ok_label
        self._cancel = cancel_label

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-sm"):
            yield Static(self._title, classes="pane-title")
            if self._body:
                yield Static(self._body, classes="hint")
            with Horizontal(classes="modal-actions"):
                yield Button(self._ok, id="confirm-ok", variant="primary")
                yield Button(self._cancel, id="confirm-cancel")

    def on_mount(self) -> None:
        self.query_one("#confirm-ok", Button).focus()

    @on(Button.Pressed, "#confirm-ok")
    def ok_btn(self) -> None:
        self.dismiss("ok")

    @on(Button.Pressed, "#confirm-cancel")
    def cancel_btn(self) -> None:
        self.dismiss(None)

    def action_ok(self) -> None:
        self.dismiss("ok")

    def action_cancel(self) -> None:
        self.dismiss(None)


class ProjectPicker(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss_picker", "Close", show=False)]

    def __init__(self, projects: list[dict[str, Any]], current: str | None) -> None:
        super().__init__()
        self._projects = projects
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="modal"):
            yield Static("projects", classes="pane-title")
            yield OptionList(id="project-list")

    def on_mount(self) -> None:
        ol = self.query_one("#project-list", OptionList)
        for p in self._projects:
            pid = str(p.get("id") or "")
            name = str(p.get("name") or pid)
            mark = " ←" if pid == self._current else ""
            kit = p.get("kit") or ""
            label = f"{name}  [dim]{pid}" + (f" · {kit}" if kit else "") + f"{mark}[/]"
            ol.add_option(Option(label, id=pid))
        ol.focus()

    @on(OptionList.OptionSelected, "#project-list")
    def on_pick(self, event: OptionList.OptionSelected) -> None:
        opt = event.option
        self.dismiss(str(opt.id) if opt and opt.id else None)

    def action_dismiss_picker(self) -> None:
        self.dismiss(None)


class CommandPalette(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss_palette", "Close", show=False)]

    def compose(self) -> ComposeResult:
        with Vertical(id="modal"):
            yield Static("command palette", classes="pane-title")
            yield Input(placeholder="", id="palette-filter")
            yield OptionList(id="palette-list")

    def on_mount(self) -> None:
        self._cmds = [
            ("refresh", "Refresh queue", "r"),
            ("save", "Save file", "Ctrl+S"),
            ("approve", "Approve entry", "a"),
            ("comment", "Comment on entry", "c"),
            ("sweep", "Sweep sitting", "s"),
            ("toggle-diff", "Toggle diff", "v"),
            ("external-editor", "External editor", "Ctrl+E"),
            ("compact", "Compact session", ""),
            ("clear-session", "Clear session", ""),
            ("begin-interview", "Begin founding interview", "i"),
            ("draft-proposal", "Draft desk proposal", "d"),
            ("found-desk", "Found desk", ""),
            ("pick-project", "Switch project", "p"),
            ("focus-queue", "Focus queue", ""),
            ("focus-editor", "Focus editor", ""),
            ("focus-composer", "Focus composer", ""),
            ("help", "Help", "?"),
            ("quit", "Quit", "q"),
        ]
        self._fill("")
        self.query_one("#palette-filter", Input).focus()

    def _fill(self, filt: str) -> None:
        f = filt.strip().lower()
        ol = self.query_one("#palette-list", OptionList)
        ol.clear_options()
        for cid, label, hint in self._cmds:
            if f and f not in label.lower() and f not in cid:
                continue
            prompt = f"{label}" if not hint else f"{label}  [dim]{hint}[/]"
            ol.add_option(Option(prompt, id=cid))

    @on(Input.Changed, "#palette-filter")
    def on_filter(self, event: Input.Changed) -> None:
        self._fill(event.value)

    @on(OptionList.OptionSelected, "#palette-list")
    def on_pick(self, event: OptionList.OptionSelected) -> None:
        opt = event.option
        self.dismiss(str(opt.id) if opt and opt.id else None)

    @on(Input.Submitted, "#palette-filter")
    def on_submit(self) -> None:
        ol = self.query_one("#palette-list", OptionList)
        if ol.option_count == 0:
            return
        idx = ol.highlighted if ol.highlighted is not None else 0
        opt = ol.get_option_at_index(idx)
        self.dismiss(str(opt.id) if opt and opt.id else None)

    def action_dismiss_palette(self) -> None:
        self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "close", show=False),
        Binding("q", "close", show=False),
        Binding("question_mark", "close", show=False),
    ]

    def compose(self) -> ComposeResult:
        # Help is the only place for usage copy — never on the sitting chrome.
        text = """\
# kettle-tui

| Key | Action |
|-----|--------|
| `j` / `k` | Queue |
| `Enter` | Open entry |
| `a` | Approve / Found desk |
| `c` | Comment |
| `s` | Sweep |
| `v` | Diff / file |
| `Ctrl+E` | `$EDITOR` |
| `i` | Interview |
| `d` | Draft proposal |
| `Ctrl+S` | Save |
| `p` | Project |
| `r` | Refresh |
| `Ctrl+K` | Palette |
| `Tab` | Cycle focus |
| `q` / `Ctrl+C` | Quit |
"""
        with Vertical(id="modal-help"):
            yield Markdown(text)

    def action_close(self) -> None:
        self.dismiss(None)


# ── app state ───────────────────────────────────────────────────────────


@dataclass
class DeskState:
    projects: list[dict[str, Any]] = field(default_factory=list)
    project_id: str | None = None
    entries: list[dict[str, Any]] = field(default_factory=list)
    selected_id: str | None = None
    file_path: str | None = None
    file_loaded: str = ""
    dirty: bool = False
    streaming: bool = False
    error: str | None = None
    ping: dict[str, Any] | None = None
    phase: str = "founded"  # none | interviewing | proposing | founded
    interview_status: dict[str, Any] | None = None
    view_mode: str = "file"  # file | diff
    file_abs: str | None = None  # absolute path when known (for $EDITOR)


class KettleTui(App[None]):
    TITLE = "kettle-tui"
    SUB_TITLE = "sitting"

    CSS = """
    Screen {
        background: #1e1e2e;
        color: #cdd6f4;
        overflow: hidden;
    }
    Header { background: #181825; }
    Footer { background: #181825; }

    #body {
        height: 1fr;
        width: 100%;
        overflow: hidden;
    }

    /* Fixed-width side panes; editor takes the rest. Never allow x-scroll. */
    #queue-pane {
        width: 28;
        min-width: 18;
        max-width: 48;
        background: #181825;
        overflow-x: hidden;
        overflow-y: auto;
    }
    #editor-pane {
        width: 1fr;
        min-width: 24;
        background: #1e1e2e;
        padding: 0 1;
        overflow-x: hidden;
        overflow-y: hidden;
    }
    #desk-pane {
        width: 52;
        /* Must stay ≥ DESK_STRIP_MIN so a·approve / c·comment / s·sweep fit */
        min-width: 35;
        max-width: 80;
        background: #181825;
        padding: 0 1;
        overflow-x: hidden;
        overflow-y: hidden;
    }

    .pane-title {
        text-style: bold;
        color: #a6adc8;
        padding: 1 1 0 1;
        width: 100%;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .project-label {
        color: #89b4fa;
        padding: 0 1 1 1;
        text-style: bold;
        width: 100%;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .file-path {
        color: #a6adc8;
        padding: 0 0 1 0;
        width: 100%;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .hint {
        color: #6c7086;
        padding: 0 1;
        height: 1;
        width: 100%;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .founding-step {
        height: 1;
        width: 100%;
        color: #cba6f7;
        background: #1a1625;
        padding: 0 1;
        text-style: bold;
        overflow: hidden;
        text-overflow: ellipsis;
    }


    #queue-list {
        height: 1fr;
        background: transparent;
        border: none;
        padding: 0 1;
        overflow-x: hidden;
        overflow-y: auto;
    }
    #queue-list > ListItem {
        padding: 0 0 1 0;
        background: transparent;
        width: 100%;
        overflow: hidden;
    }
    #queue-list > ListItem.--highlight { background: #313244; }
    #queue-list Label {
        width: 100%;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    #editor {
        height: 1fr;
        width: 100%;
        border: none;
        background: #1e1e2e;
        overflow-x: hidden;
    }

    #brief-scroll {
        height: 8;
        max-height: 12;
        width: 100%;
        border-bottom: solid #45475a;
        margin-bottom: 0;
        padding-bottom: 1;
        overflow-x: hidden;
        overflow-y: auto;
    }
    #brief-md {
        padding: 0 0 1 0;
        width: 100%;
        overflow-x: hidden;
    }

    /* Single-row keycaps — 1 cell tall; keys first (a·approve). No tall borders. */
    .action-strip {
        height: 1;
        min-height: 1;
        max-height: 1;
        width: 100%;
        padding: 0;
        margin: 0 0 1 0;
        align: left middle;
        overflow: hidden;
        background: #181825;
    }
    .action-strip Button {
        width: auto;
        height: 1;
        min-height: 1;
        max-height: 1;
        margin: 0 1 0 0;
        padding: 0 1;
        border: none;
        background: transparent;
        text-style: none;
        content-align: left middle;
        overflow: hidden;
    }
    /* Natural widths for keycaps — desk min-width is sized to their sum */
    #btn-approve { min-width: 11; }   /* a·approve + pad */
    #btn-comment { min-width: 11; }   /* c·comment + pad */
    #btn-sweep { min-width: 9; }      /* s·sweep + pad */
    #btn-interview { min-width: 12; } /* i·interview + pad */
    #btn-propose { min-width: 9; }
    #btn-found { min-width: 7; }
    .action-strip Button:last-of-type { margin-right: 0; }
    .action-strip Button:hover {
        background: #313244;
        border: none;
    }
    .action-strip Button:disabled {
        color: #585b70;
        text-style: dim;
        background: transparent;
        border: none;
    }
    .action-strip Button.-active {
        background: #45475a;
        border: none;
    }
    #btn-approve { color: #74c7ec; }
    #btn-comment { color: #f9e2af; }
    #btn-sweep { color: #a6e3a1; }
    #btn-interview { color: #cba6f7; }
    #btn-propose { color: #cba6f7; }
    #btn-found { color: #cba6f7; }

    #stream {
        height: 1fr;
        min-height: 6;
        width: 100%;
        border: none;
        background: #11111b;
        padding: 0 1;
        overflow-x: hidden;
        overflow-y: auto;
    }
    #composer {
        margin: 1 0;
        width: 100%;
        background: #313244;
        border: none;
        overflow: hidden;
    }
    #composer:focus { background: #45475a; }

    #status {
        dock: bottom;
        height: 1;
        width: 100%;
        background: #11111b;
        color: #6c7086;
        padding: 0 1;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    #modal, #modal-help {
        width: 64;
        max-width: 90%;
        height: auto;
        max-height: 80%;
        background: #181825;
        border: solid #89b4fa;
        padding: 1 2;
        overflow-x: hidden;
    }
    #modal { height: 18; }
    #modal-sm {
        width: 56;
        max-width: 90%;
        height: auto;
        background: #181825;
        border: solid #89b4fa;
        padding: 1 2;
        overflow-x: hidden;
    }
    #modal-help { height: auto; padding: 1 2; }
    #sweep-thrust {
        margin: 1 0;
        background: #313244;
        border: none;
    }
    #palette-filter, #comment-body {
        margin: 1 0;
        background: #313244;
        border: none;
    }
    #comment-body { height: 8; }
    .modal-actions { height: 3; align: left middle; }
    .modal-actions Button { margin-right: 1; }
    #palette-list, #project-list {
        height: 1fr;
        border: none;
        background: transparent;
        overflow-x: hidden;
    }
    Markdown {
        background: transparent;
        overflow-x: hidden;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        # Override Textual's default ctrl+c → help_quit nag; exit is a normal shortcut.
        Binding("ctrl+c", "quit", "Quit", show=False, priority=True),
        Binding("question_mark", "help", "Help"),
        Binding("r", "refresh", "Refresh"),
        Binding("p", "pick_project", "Project"),
        Binding("a", "approve", "Approve"),
        Binding("c", "comment", "Comment"),
        Binding("s", "sweep", "Sweep"),
        Binding("v", "toggle_diff", "Diff"),
        Binding("ctrl+e", "external_editor", "Editor"),
        Binding("i", "begin_interview", "Interview"),
        Binding("d", "draft_proposal", "Propose"),
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+k", "palette", "Palette"),
        Binding("colon", "palette", "Palette", show=False),
        Binding("tab", "cycle_focus", "Cycle", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("enter", "open_entry", "Open", show=False),
    ]

    def __init__(
        self,
        client: KettleClient,
        *,
        project_id: str | None = None,
        here: str | None = None,
        require_here: bool = False,
    ) -> None:
        super().__init__()
        self.client = client
        self._preferred_project = project_id
        self._here = here
        self._require_here = require_here
        self.state = DeskState()
        self.queue_width = QUEUE_W_DEFAULT
        self.desk_width = DESK_W_DEFAULT

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield QueuePane(id="queue-pane")
            yield VSplitHandle("queue", id="split-queue")
            yield EditorPane(id="editor-pane")
            yield VSplitHandle("desk", id="split-desk")
            yield DeskPane(id="desk-pane")
        yield Static("connecting…", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self._apply_column_widths()
        self.load_desk()

    def on_resize(self, event: events.Resize) -> None:
        # Keep side panes from starving the editor after a terminal shrink.
        self._clamp_widths_to_terminal()
        self._apply_column_widths()

    def set_queue_width(self, width: int) -> None:
        self.queue_width = max(QUEUE_W_MIN, min(QUEUE_W_MAX, int(width)))
        self._clamp_widths_to_terminal()
        self._apply_column_widths()

    def set_desk_width(self, width: int) -> None:
        # Never below the ritual keycap strip; drag cannot shrink past the buttons.
        self.desk_width = max(DESK_W_MIN, min(DESK_W_MAX, int(width)))
        self._clamp_widths_to_terminal()
        self._apply_column_widths()

    def _clamp_widths_to_terminal(self) -> None:
        """Ensure queue + desk + splits leave room for the editor.

        Desk never goes below DESK_W_MIN (three keycaps). Prefer shrinking queue
        first when the terminal is tight.
        """
        term_w = self.size.width if self.size.width else 120
        # body is full width under header; splits take 2 cols
        budget = max(term_w - 2, QUEUE_W_MIN + DESK_W_MIN + EDITOR_W_MIN)
        max_sides = budget - EDITOR_W_MIN
        q, d = self.queue_width, self.desk_width
        d = max(DESK_W_MIN, d)  # floor first — buttons must always fit
        if q + d > max_sides:
            overflow = q + d - max_sides
            # Shrink queue before desk so the keycap strip stays intact
            take_q = min(overflow, max(0, q - QUEUE_W_MIN))
            q -= take_q
            overflow -= take_q
            if overflow > 0:
                # Last resort only: still never under DESK_W_MIN
                d = max(DESK_W_MIN, d - overflow)
        self.queue_width = q
        self.desk_width = d

    def _apply_column_widths(self) -> None:
        try:
            queue = self.query_one("#queue-pane")
            desk = self.query_one("#desk-pane")
        except Exception:
            return
        queue.styles.width = self.queue_width
        desk.styles.width = self.desk_width
        desk.styles.min_width = DESK_W_MIN

    # ── data ────────────────────────────────────────────────────────────

    @work(exclusive=True, thread=True)
    def load_desk(self) -> None:
        try:
            if not self.client.ensure_reachable():
                raise ClientError(
                    message="cannot reach kettle core (and failed to spawn kettle --web)"
                )
            ping = self.client.ping()
            projects = self.client.projects()
            pid = default_project_id(
                projects,
                self._preferred_project,
                here=self._here,
                require_here=self._require_here,
            )
            entries: list[dict[str, Any]] = []
            iv: dict[str, Any] | None = None
            err: str | None = None
            if pid:
                try:
                    q = self.client.queue(str(pid), mutate=False)
                    entries = sort_queue(list(q.get("entries") or []))
                except ClientError as e:
                    if is_missing_project_folder(e):
                        try:
                            projects = self.client.projects()
                        except ClientError:
                            pass
                        entries = []
                        err = str(e)
                    elif e.code != "queue_corrupt":
                        raise
                    else:
                        entries = []
                        err = str(e)
                try:
                    iv = self.client.interview_status(str(pid))
                except ClientError as e:
                    iv = {"founded": False, "status": "none"}
                    if err is None:
                        err = str(e)
        except ClientError as e:
            ping, projects, pid, entries, iv, err = None, [], None, [], None, str(e)

        def apply() -> None:
            self.state.projects = projects
            self.state.project_id = str(pid) if pid else None
            self.state.entries = entries
            self.state.selected_id = entries[0].get("id") if entries else None
            self.state.ping = ping
            self.state.error = err
            self.state.interview_status = iv
            self.state.phase = normalize_phase(iv)
            self.state.view_mode = "file"
            self._preferred_project = self.state.project_id
            self._paint_shell()
            self._announce_phase()
            if self.state.selected_id:
                self.load_entry(str(self.state.selected_id))
            else:
                self._paint_empty_entry()

        self.call_from_thread(apply)

    @work(exclusive=True, thread=True)
    def load_queue_only(self, heal_founding: bool = False) -> None:
        pid = self.state.project_id
        if not pid:
            return
        projects = self.state.projects
        folder_gone = False
        try:
            q = self.client.queue(pid, mutate=False)
            entries = sort_queue(list(q.get("entries") or []))
            try:
                iv = self.client.interview_status(pid, mutate=heal_founding)
            except ClientError as e:
                if is_missing_project_folder(e):
                    raise
                iv = self.state.interview_status
            err = None
        except ClientError as e:
            if is_missing_project_folder(e):
                folder_gone = True
                try:
                    projects = self.client.projects()
                except ClientError:
                    pass
                entries, iv, err = [], self.state.interview_status, str(e)
            else:
                entries, iv, err = self.state.entries, self.state.interview_status, str(e)

        def apply() -> None:
            self.state.projects = projects
            if folder_gone:
                ids = {p.get("id") for p in projects}
                self.state.error = err
                if pid not in ids:
                    self.state.project_id = None
                    self.state.entries = []
                    self.state.selected_id = None
                    self._paint_empty_entry()
                    self._paint_shell()
                    return
            sel = self.state.selected_id
            ids = {e.get("id") for e in entries}
            if sel not in ids:
                sel = entries[0].get("id") if entries else None
            self.state.entries = entries
            self.state.selected_id = sel
            self.state.error = err
            self.state.interview_status = iv
            self.state.phase = normalize_phase(iv)
            self._paint_shell()
            if sel:
                self.load_entry(str(sel))
            else:
                self._paint_empty_entry()

        self.call_from_thread(apply)

    def _refresh_phase(self) -> None:
        """Re-read founding status and update chrome (after interview/propose/found)."""
        pid = self.state.project_id
        if not pid:
            return
        try:
            iv = self.client.interview_status(pid)
            q = self.client.queue(pid, mutate=False)
            entries = sort_queue(list(q.get("entries") or []))
        except ClientError as e:
            self.state.error = str(e)
            self._paint_status()
            return
        self.state.interview_status = iv
        self.state.phase = normalize_phase(iv)
        self.state.entries = entries
        # Prefer proposal entry when proposing
        if self.state.phase == "proposing":
            prop = next(
                (
                    e
                    for e in entries
                    if e.get("id") == "desk-proposal"
                    or e.get("founding_phase") == "proposal"
                ),
                None,
            )
            if prop:
                self.state.selected_id = prop.get("id")
                self.load_entry(str(prop.get("id")))
                self._paint_shell()
                return
        if self.state.selected_id and not any(
            str(e.get("id")) == str(self.state.selected_id) for e in entries
        ):
            self.state.selected_id = entries[0].get("id") if entries else None
        self._paint_shell()
        if self.state.selected_id:
            self.load_entry(str(self.state.selected_id))
        else:
            self._paint_empty_entry()

    def _announce_phase(self) -> None:
        # Phase is shown on the desk step strip only — no how-to spam in the stream.
        return

    @work(exclusive=True, thread=True)
    def load_entry(self, entry_id: str) -> None:
        entry = self._entry_by_id(entry_id)
        pid = self.state.project_id
        if not entry or not pid:
            self.call_from_thread(self._paint_empty_entry)
            return
        path = str(entry.get("path") or "")
        mode = self.state.view_mode if self.state.view_mode in ("file", "diff") else "file"
        try:
            if mode == "diff":
                text = self.client.entry_diff(pid, entry_id)
            else:
                text = self.client.file_text(pid, path) if path else ""
            err = None
        except ClientError as e:
            text, err = f"⚠ {e}\n", str(e)

        # Resolve absolute path for $EDITOR when possible
        abs_path = None
        if path and not self.client.demo_mode:
            root = None
            for p in self.state.projects:
                if p.get("id") == pid:
                    root = p.get("root")
                    break
            if root and not path.startswith("/"):
                abs_path = str(Path(str(root)) / path)
            elif path.startswith("/"):
                abs_path = path

        def apply() -> None:
            self.state.selected_id = entry_id
            self.state.file_path = path or None
            self.state.file_abs = abs_path
            self.state.file_loaded = text
            self.state.dirty = False
            self.state.error = err
            self.state.view_mode = mode
            self._paint_entry(entry, text, mode=mode)

        self.call_from_thread(apply)

    def _entry_by_id(self, entry_id: str | None) -> dict[str, Any] | None:
        if not entry_id:
            return None
        for e in self.state.entries:
            if str(e.get("id")) == str(entry_id):
                return e
        return None

    def _selected(self) -> dict[str, Any] | None:
        return self._entry_by_id(self.state.selected_id)

    def _project_name(self) -> str:
        pid = self.state.project_id
        for p in self.state.projects:
            if p.get("id") == pid:
                return str(p.get("name") or pid)
        return pid or "—"

    # ── paint ───────────────────────────────────────────────────────────

    def _paint_shell(self) -> None:
        qp = self.query_one(QueuePane)
        pid = self.state.project_id or ""
        qp.project_label = f"{self._project_name()}" + (f"  ({pid})" if pid else "")
        qp.entries = list(self.state.entries)
        self._sync_queue_index()
        self.query_one(DeskPane).set_founding_chrome(self.state.phase)
        self._paint_status()
        self._paint_titles()
        self._paint_ritual()

    def _sync_queue_index(self) -> None:
        lv = self.query_one("#queue-list", ListView)
        for i, e in enumerate(self.state.entries):
            if str(e.get("id")) == str(self.state.selected_id):
                try:
                    lv.index = i
                except Exception:
                    pass
                break

    def _paint_entry(
        self, entry: dict[str, Any], text: str, *, mode: str = "file"
    ) -> None:
        ed = self.query_one(EditorPane)
        path = str(entry.get("path") or "—")
        ed.set_path(path, dirty=False, mode=mode)
        ed.set_text(text, read_only=(mode == "diff"))
        desk = self.query_one(DeskPane)
        desk.show_brief(entry)
        note = entry.get("comment")
        hint = self.query_one("#comment-hint", Static)
        if note:
            hint.update(f"[#f9e2af]·[/] {str(note)[:72]}")
        else:
            hint.update("")
        self._paint_ritual()
        self._paint_status()
        self._sync_queue_index()

    def _paint_empty_entry(self) -> None:
        ed = self.query_one(EditorPane)
        ed.set_path("", mode="file")
        ed.set_text("", read_only=False)
        self.state.view_mode = "file"
        self.state.file_abs = None
        desk = self.query_one(DeskPane)
        desk.show_brief(None)
        self.query_one("#comment-hint", Static).update("")
        self._paint_ritual()
        self._paint_status()

    def _paint_ritual(self) -> None:
        entry = self._selected()
        rc = ritual_controls(
            has_project=bool(self.state.project_id),
            has_entry=bool(entry),
            approved=_is_approved(entry),
            streaming=self.state.streaming,
            phase=self.state.phase,
        )
        self.query_one(DeskPane).set_ritual(
            can_approve=rc["can_approve"],
            can_comment=rc["can_comment"],
            can_sweep=rc["can_sweep"],
            approve_label=rc["approve_label"],
        )

    def _paint_titles(self) -> None:
        mode = "demo" if self.client.demo_mode else "live"
        self.title = f"kettle-tui · {self._project_name()}"
        self.sub_title = mode

    def _paint_status(self) -> None:
        status = self.query_one("#status", Static)
        if self.state.error:
            status.update(f"[bold #e06c75]error:[/] {self.state.error}")
            return
        bits: list[str] = []
        if self.client.demo_mode:
            bits.append("demo")
        else:
            d = self.client.discovery
            bits.append(d.url.replace("http://", ""))
        ping = self.state.ping
        if isinstance(ping, dict) and ping.get("api_version") is not None:
            bits.append(f"api {ping.get('api_version')}")
        pending = sum(
            1
            for e in self.state.entries
            if (e.get("status") or "pending") == "pending"
        )
        if self._require_here and not self.state.project_id:
            loc = self._here or os.getcwd()
            try:
                loc = str(Path(loc).expanduser().resolve())
            except (OSError, RuntimeError):
                loc = str(loc)
            home = str(Path.home())
            if loc.startswith(home + os.sep):
                loc = "~" + loc[len(home) :]
            bits.append(f"no desk for {loc}")
        if self.state.phase != "founded":
            bits.append(f"[#cba6f7]{self.state.phase}[/]")
        bits.append(f"{pending}/{len(self.state.entries)} pending")
        if self.state.selected_id:
            bits.append(str(self.state.selected_id))
        if self.state.view_mode == "diff":
            bits.append("diff")
        if self.state.dirty:
            bits.append("[#e5c07b]dirty[/]")
        if self.state.streaming:
            bits.append("streaming…")
        status.update("  ·  ".join(bits))

    # ── events ──────────────────────────────────────────────────────────

    @on(ListView.Highlighted, "#queue-list")
    def on_queue_highlight(self, event: ListView.Highlighted) -> None:
        item = event.item
        if not isinstance(item, QueueItem):
            return
        # Brief preview only while browsing — file load waits for Select/Enter
        # so we don't thrash the editor (or lose dirty buffer) on every j/k.
        eid = str(item.entry.get("id") or "")
        if eid:
            self.query_one(DeskPane).show_brief(item.entry)

    @on(ListView.Selected, "#queue-list")
    def on_queue_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, QueueItem):
            self._open_entry_guarded(str(item.entry.get("id")))

    @on(TextArea.Changed, "#editor")
    def on_editor_changed(self) -> None:
        ed = self.query_one(EditorPane)
        if ed.get_text() != self.state.file_loaded:
            if not self.state.dirty:
                self.state.dirty = True
                if self.state.file_path:
                    ed.set_path(self.state.file_path, dirty=True)
                self._paint_status()

    @on(Button.Pressed, "#btn-approve")
    def on_approve_btn(self) -> None:
        self.action_approve()

    @on(Button.Pressed, "#btn-comment")
    def on_comment_btn(self) -> None:
        self.action_comment()

    @on(Button.Pressed, "#btn-sweep")
    def on_sweep_btn(self) -> None:
        self.action_sweep()

    @on(Button.Pressed, "#btn-interview")
    def on_interview_btn(self) -> None:
        self.action_begin_interview()

    @on(Button.Pressed, "#btn-propose")
    def on_propose_btn(self) -> None:
        self.action_draft_proposal()

    @on(Button.Pressed, "#btn-found")
    def on_found_btn(self) -> None:
        self.action_found_desk()

    @on(Input.Submitted, "#composer")
    def on_composer_submit(self, event: Input.Submitted) -> None:
        msg = event.value.strip()
        if not msg:
            return
        event.input.value = ""
        # Founding interview turns go to /api/interview; else project chat
        if self.state.phase in ("none", "interviewing"):
            self.send_interview(msg)
        else:
            self.send_chat(msg)

    # ── actions ─────────────────────────────────────────────────────────

    def action_refresh(self) -> None:
        self.notify("refreshing…", timeout=1)
        # D1: one mutating interview read on explicit refresh of an unfounded desk.
        self.load_queue_only(heal_founding=self.state.phase != "founded")

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_pick_project(self) -> None:
        def done(pid: str | None) -> None:
            if not pid or pid == self.state.project_id:
                return
            self._preferred_project = pid
            self.state.project_id = pid
            self.notify(f"project → {pid}", timeout=2)
            self.load_queue_only()

        self.push_screen(ProjectPicker(self.state.projects, self.state.project_id), done)

    def action_palette(self) -> None:
        def done(cmd: str | None) -> None:
            if cmd:
                self._run_command(cmd)

        self.push_screen(CommandPalette(), done)

    def action_cycle_focus(self) -> None:
        order = [
            self.query_one("#queue-list"),
            self.query_one("#editor"),
            self.query_one("#composer"),
        ]
        focused = self.focused
        if focused in order:
            i = order.index(focused)
            order[(i + 1) % len(order)].focus()
        else:
            order[0].focus()

    def action_cursor_down(self) -> None:
        lv = self.query_one("#queue-list", ListView)
        if self.focused is lv or isinstance(self.focused, QueueItem):
            lv.action_cursor_down()

    def action_cursor_up(self) -> None:
        lv = self.query_one("#queue-list", ListView)
        if self.focused is lv or isinstance(self.focused, QueueItem):
            lv.action_cursor_up()

    def action_open_entry(self) -> None:
        # Prefer the highlighted queue row (may differ from last loaded entry).
        lv = self.query_one("#queue-list", ListView)
        item = lv.highlighted_child
        if isinstance(item, QueueItem):
            eid = str(item.entry.get("id") or "")
        else:
            entry = self._selected()
            eid = str(entry.get("id")) if entry else ""
        if eid:
            self._open_entry_guarded(eid, focus_editor=True)

    def _open_entry_guarded(self, entry_id: str, *, focus_editor: bool = False) -> None:
        if str(self.state.selected_id or "") == str(entry_id) and not self.state.dirty:
            if focus_editor:
                self.query_one("#editor", TextArea).focus()
            return

        def proceed() -> None:
            self.load_entry(entry_id)
            if focus_editor:
                self.set_timer(0.05, lambda: self.query_one("#editor", TextArea).focus())

        if not self.state.dirty:
            proceed()
            return

        def on_choice(choice: str | None) -> None:
            if choice is None:
                return
            if choice == "save":
                # Save then open; save worker is async — chain via callback style
                self._save_then(proceed)
            elif choice == "discard":
                self.state.dirty = False
                proceed()

        self.push_screen(
            DiscardModal("Open another entry? Unsaved editor changes will be lost."),
            on_choice,
        )

    def _save_then(self, then: Any) -> None:
        pid = self.state.project_id
        path = self.state.file_path
        if not pid or not path:
            then()
            return
        text = self.query_one(EditorPane).get_text()
        self._pending_after_save = then
        self._save_worker(pid, path, text, then_continue=True)

    # set by _save_then; consumed once after a successful save
    _pending_after_save: Any = None

    def action_sweep(self) -> None:
        if not self.state.project_id:
            self.notify("no project", severity="warning")
            return
        if self.state.streaming:
            self.notify("already streaming", severity="warning")
            return

        def on_confirm(thrust: str | None) -> None:
            if thrust is None:
                return
            if self.state.dirty:
                def after_dirty(choice: str | None) -> None:
                    if choice is None:
                        return
                    if choice == "save":
                        self._save_then(lambda: self._start_sweep(thrust))
                    elif choice == "discard":
                        self.state.dirty = False
                        self._start_sweep(thrust)

                self.push_screen(
                    DiscardModal("Sweep with unsaved editor changes?"),
                    after_dirty,
                )
            else:
                self._start_sweep(thrust)

        self.push_screen(SweepModal(), on_confirm)

    def _start_sweep(self, thrust: str) -> None:
        pid = self.state.project_id
        if not pid:
            return
        desk = self.query_one(DeskPane)
        if thrust.strip():
            desk.stream_write(f"steer for this sweep: {thrust.strip()}", style="#a6e3a1")
        desk.stream_write(
            "— sweep started: rulings · diff · assess · ripple · log · restock —",
            style="dim",
        )
        self.state.streaming = True
        self.query_one(EditorPane).set_read_only(True)
        self._paint_ritual()
        self._paint_status()
        self._sweep_worker(pid, thrust)

    @work(thread=True, exclusive=True)
    def _sweep_worker(self, pid: str, thrust: str) -> None:
        try:
            for ev in self.client.sweep_events(pid, thrust=thrust):
                self._relay_ndjson_event(ev)
            err = None
        except ClientError as e:
            err = str(e)

        # Restock queue after sweep (like web sitting)
        entries: list[dict[str, Any]] = []
        try:
            q = self.client.queue(pid, mutate=False)
            entries = sort_queue(list(q.get("entries") or []))
            q_err = None
        except ClientError as e:
            q_err = str(e)

        def done() -> None:
            self.state.streaming = False
            self.query_one(EditorPane).set_read_only(self.state.view_mode == "diff")
            if err:
                self.query_one(DeskPane).stream_write(
                    f"sweep error: {err}", style="#e06c75"
                )
            else:
                self.query_one(DeskPane).stream_write("— sweep finished —", style="dim")
            if q_err:
                self.query_one(DeskPane).stream_write(
                    f"queue reload failed: {q_err}", style="#e06c75"
                )
            else:
                self.state.entries = entries
                self._reselect_after_restock(entries)
                self.query_one(QueuePane).entries = list(entries)
                pending = [
                    e
                    for e in entries
                    if (e.get("status") or "pending") == "pending"
                ]
                desk = self.query_one(DeskPane)
                if pending:
                    lines = [
                        f"• {_file_name(e)} ({e.get('urgency') or '?'}) — {e.get('ask') or ''}"
                        for e in pending[:12]
                    ]
                    desk.stream_write("now in the queue:", style="dim")
                    desk.stream_write("\n".join(lines))
                    if len(pending) > 12:
                        desk.stream_write(f"… +{len(pending) - 12} more", style="dim")
                else:
                    desk.stream_write(
                        "queue empty — review program may be exhausted",
                        style="dim",
                    )
            self._paint_shell()
            if self.state.selected_id:
                self.load_entry(str(self.state.selected_id))
            else:
                self._paint_empty_entry()
            self._paint_status()
            self.notify("sweep finished" if not err else "sweep failed", timeout=2)

        self.call_from_thread(done)

    def _reselect_after_restock(self, entries: list[dict[str, Any]]) -> None:
        """Keep the open entry if it survived restock; otherwise first pending."""
        ids = {str(e.get("id")) for e in entries}
        cur = str(self.state.selected_id) if self.state.selected_id else ""
        if cur and cur in ids:
            return
        pending = [
            e for e in entries if (e.get("status") or "pending") == "pending"
        ]
        pick = pending[0] if pending else (entries[0] if entries else None)
        self.state.selected_id = pick.get("id") if pick else None
        if not self.state.selected_id:
            self.state.file_path = None
            self.state.file_loaded = ""
            self.state.dirty = False

    def action_toggle_diff(self) -> None:
        entry = self._selected()
        if not entry or not self.state.project_id:
            return
        if self.state.dirty and self.state.view_mode == "file":
            self.notify("save or discard before diff", severity="warning")
            return
        self.state.view_mode = "file" if self.state.view_mode == "diff" else "diff"
        self.load_entry(str(entry.get("id")))

    def action_external_editor(self) -> None:
        """Open current file in $VISUAL / $EDITOR (git-commit style)."""
        if self.state.streaming:
            self.notify("wait for the stream to finish", severity="warning")
            return
        if self.state.view_mode == "diff":
            self.notify("switch to file view first", severity="warning")
            return
        path = self.state.file_abs or self.state.file_path
        if not path:
            self.notify("no file", severity="warning")
            return
        # Prefer absolute path; fall back to temp with current buffer
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
        use_temp = not (path.startswith("/") and Path(path).is_file())
        if self.client.demo_mode or use_temp:
            self._external_editor_temp(editor)
            return
        self._external_editor_path(editor, path)

    def _external_editor_path(self, editor: str, path: str) -> None:
        if self.state.dirty:
            # Write buffer first so $EDITOR sees latest
            try:
                Path(path).write_text(
                    self.query_one(EditorPane).get_text(), encoding="utf-8"
                )
                self.state.dirty = False
            except OSError as e:
                self.notify(f"write failed: {e}", severity="error")
                return

        def run() -> None:
            with self.suspend():
                try:
                    subprocess.call([editor, path])  # noqa: S603
                except OSError as e:
                    self.call_from_thread(
                        lambda: self.notify(f"editor failed: {e}", severity="error")
                    )
                    return
            # Reload from disk
            if self.state.selected_id:
                self.load_entry(str(self.state.selected_id))

        self.run_worker(run, exclusive=True, thread=True)

    def _external_editor_temp(self, editor: str) -> None:
        text = self.query_one(EditorPane).get_text()
        suffix = Path(self.state.file_path or "file.txt").suffix or ".txt"

        def run() -> None:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=suffix, delete=False, encoding="utf-8"
            ) as f:
                f.write(text)
                tmp = f.name
            try:
                with self.suspend():
                    subprocess.call([editor, tmp])  # noqa: S603
                new_text = Path(tmp).read_text(encoding="utf-8")
            except OSError as e:
                self.call_from_thread(
                    lambda: self.notify(f"editor failed: {e}", severity="error")
                )
                return
            finally:
                try:
                    Path(tmp).unlink(missing_ok=True)
                except OSError:
                    pass

            def apply() -> None:
                ed = self.query_one(EditorPane)
                ed.set_text(new_text, read_only=False)
                if new_text != self.state.file_loaded:
                    self.state.dirty = True
                    if self.state.file_path:
                        ed.set_path(self.state.file_path, dirty=True, mode="file")
                self._paint_status()

            self.call_from_thread(apply)

        self.run_worker(run, exclusive=True, thread=True)

    def action_compact(self) -> None:
        pid = self.state.project_id
        if not pid or self.state.streaming:
            return
        desk = self.query_one(DeskPane)
        desk.stream_write("— compact —", style="dim")
        self.state.streaming = True
        self._paint_ritual()
        self._compact_worker(pid)

    @work(thread=True, exclusive=True)
    def _compact_worker(self, pid: str) -> None:
        try:
            for ev in self.client.compact_events(pid):
                self._relay_ndjson_event(ev)
            err = None
        except ClientError as e:
            err = str(e)

        def done() -> None:
            self.state.streaming = False
            if err:
                self.query_one(DeskPane).stream_write(
                    f"compact error: {err}", style="#e06c75"
                )
            self._paint_ritual()
            self._paint_status()

        self.call_from_thread(done)

    def action_clear_session(self) -> None:
        pid = self.state.project_id
        if not pid:
            return

        def on_confirm(choice: str | None) -> None:
            if choice != "ok":
                return
            self._clear_session_worker(pid)

        self.push_screen(
            ConfirmActionModal("Clear session?", "", ok_label="clear"),
            on_confirm,
        )

    @work(thread=True)
    def _clear_session_worker(self, pid: str) -> None:
        try:
            self.client.clear_session(pid)
            err = None
        except ClientError as e:
            err = str(e)

        def apply() -> None:
            if err:
                self.notify(str(err), severity="error")
            else:
                self.query_one(DeskPane).stream_write("— session cleared —", style="dim")
                self.notify("session cleared", timeout=1.5)

        self.call_from_thread(apply)

    def action_save(self) -> None:
        if self.state.streaming:
            self.notify("wait for the stream to finish", severity="warning")
            return
        if self.state.view_mode == "diff":
            self.notify("diff is read-only", severity="warning")
            return
        pid = self.state.project_id
        path = self.state.file_path
        if not pid or not path:
            self.notify("nothing to save", severity="warning")
            return
        text = self.query_one(EditorPane).get_text()
        self._save_worker(pid, path, text, then_continue=False)

    @work(thread=True)
    def _save_worker(
        self, pid: str, path: str, text: str, then_continue: bool = False
    ) -> None:
        try:
            self.client.save_file(pid, path, text)
            err = None
        except ClientError as e:
            err = str(e)

        def apply() -> None:
            if err:
                self.state.error = err
                self.notify(f"save failed: {err}", severity="error")
                self._pending_after_save = None
            else:
                self.state.file_loaded = text
                self.state.dirty = False
                self.state.error = None
                self.query_one(EditorPane).set_path(path, dirty=False)
                self.notify("saved", timeout=1.5)
                self.query_one(DeskPane).stream_write(f"saved {path}", style="dim")
                cont = self._pending_after_save if then_continue else None
                self._pending_after_save = None
                if cont:
                    cont()
            self._paint_status()

        self.call_from_thread(apply)

    def action_approve(self) -> None:
        entry = self._selected()
        pid = self.state.project_id
        if not entry or not pid:
            return
        if self.state.streaming:
            self.notify("already streaming", severity="warning")
            return
        if _is_approved(entry) and not (
            entry.get("id") == "desk-proposal" or entry.get("founding_phase") == "proposal"
        ):
            self.notify("already approved", timeout=1.5)
            return
        self._approve_worker(pid, str(entry.get("id")))

    def action_begin_interview(self) -> None:
        if not self.state.project_id:
            return
        if self.state.phase == "founded":
            self.notify("desk already founded", timeout=2)
            return
        if self.state.streaming:
            self.notify("already streaming", severity="warning")
            return
        self.send_interview("")  # empty = kickoff

    def action_draft_proposal(self) -> None:
        pid = self.state.project_id
        if not pid:
            return
        if self.state.phase == "founded":
            self.notify("desk already founded", timeout=2)
            return
        if self.state.streaming:
            self.notify("already streaming", severity="warning")
            return

        def on_confirm(choice: str | None) -> None:
            if choice != "ok":
                return
            desk = self.query_one(DeskPane)
            desk.stream_write("— drafting desk proposal —", style="#cba6f7")
            self.state.streaming = True
            self._paint_ritual()
            self._paint_status()
            self._propose_worker(pid)

        self.push_screen(
            ConfirmActionModal("Draft desk proposal?", "", ok_label="draft proposal"),
            on_confirm,
        )

    def action_found_desk(self) -> None:
        pid = self.state.project_id
        if not pid:
            return
        if self.state.phase != "proposing":
            self.notify("need a desk proposal first", severity="warning")
            return
        if self.state.streaming:
            self.notify("already streaming", severity="warning")
            return

        def on_confirm(choice: str | None) -> None:
            if choice != "ok":
                return
            entry = self._selected()
            if not entry or not (
                entry.get("id") == "desk-proposal"
                or entry.get("founding_phase") == "proposal"
            ):
                entry = next(
                    (
                        e
                        for e in self.state.entries
                        if e.get("id") == "desk-proposal"
                        or e.get("founding_phase") == "proposal"
                    ),
                    None,
                )
            if not entry:
                self.notify("need a desk proposal first", severity="warning")
                return
            try:
                res = self.client.approve(pid, str(entry.get("id")))
            except ClientError as e:
                self.notify(f"approve failed: {e}", severity="error")
                return
            if res.get("founding") is not True:
                self.notify("approve was not a founding bookmark", severity="warning")
                return
            self._begin_found_stream(pid)

        self.push_screen(
            ConfirmActionModal("Found this desk?", "", ok_label="found desk"),
            on_confirm,
        )

    def send_interview(self, message: str) -> None:
        pid = self.state.project_id
        if not pid:
            return
        desk = self.query_one(DeskPane)
        if message.strip():
            desk.stream_write(f"you: {message}", style="#89b4fa")
        else:
            desk.stream_write("— starting founding interview —", style="#cba6f7")
        self.state.streaming = True
        self._paint_ritual()
        self._paint_status()
        self._interview_worker(pid, message)

    @work(thread=True, exclusive=True)
    def _interview_worker(self, pid: str, message: str) -> None:
        try:
            for ev in self.client.interview_events(pid, message):
                self._relay_ndjson_event(ev)
            err = None
        except ClientError as e:
            err = str(e)

        def done() -> None:
            self.state.streaming = False
            if err:
                self.query_one(DeskPane).stream_write(
                    f"interview error: {err}", style="#e06c75"
                )
            self._refresh_phase()
            self._paint_status()

        self.call_from_thread(done)

    @work(thread=True, exclusive=True)
    def _propose_worker(self, pid: str) -> None:
        try:
            for ev in self.client.propose_events(pid):
                self._relay_ndjson_event(ev)
            err = None
        except ClientError as e:
            err = str(e)

        def done() -> None:
            self.state.streaming = False
            if err:
                self.query_one(DeskPane).stream_write(
                    f"propose error: {err}", style="#e06c75"
                )
            else:
                self.query_one(DeskPane).stream_write(
                    "— proposal ready — edit center, then Found Desk —",
                    style="dim",
                )
            self._refresh_phase()
            self._paint_status()
            self.notify("proposal drafted", timeout=2)

        self.call_from_thread(done)

    @work(thread=True, exclusive=True)
    def _found_worker(self, pid: str) -> None:
        try:
            for ev in self.client.found_events(pid):
                self._relay_ndjson_event(ev)
            err = None
        except ClientError as e:
            err = str(e)

        def done() -> None:
            self.state.streaming = False
            if err:
                self.query_one(DeskPane).stream_write(
                    f"found error: {err}", style="#e06c75"
                )
                self._refresh_phase()
                self._paint_status()
                self.notify("found failed", timeout=2)
                return
            self._refresh_phase()
            if self.state.phase == "founded":
                self.query_one(DeskPane).stream_write(
                    "— desk founded — sitting open —", style="#a6e3a1"
                )
                self.notify("desk founded", timeout=2)
            else:
                self.query_one(DeskPane).stream_write(
                    "— found did not write the charter — proposal still open —",
                    style="#e5c07b",
                )
                self.notify("desk not founded — keep the proposal open", timeout=3)
            self._paint_status()

        self.call_from_thread(done)

    def _relay_ndjson_event(self, ev: dict[str, Any]) -> None:
        t = ev.get("t")
        if t == "text" and isinstance(ev.get("s"), str):
            chunk = ev["s"]

            def write_chunk(c: str = chunk) -> None:
                self.query_one(DeskPane).stream_write(
                    c.rstrip("\n") if c.endswith("\n") else c
                )

            self.call_from_thread(write_chunk)
        elif t in ("err", "error"):
            msg = str(ev.get("s") or ev.get("error") or ev)

            def write_err(m: str = msg) -> None:
                self.query_one(DeskPane).stream_write(m, style="#e06c75")

            self.call_from_thread(write_err)
        elif t == "done" and ev.get("error"):
            msg = str(ev.get("error"))

            def write_done_err(m: str = msg) -> None:
                self.query_one(DeskPane).stream_write(m, style="#e06c75")

            self.call_from_thread(write_done_err)

    def _begin_found_stream(self, pid: str) -> None:
        desk = self.query_one(DeskPane)
        desk.stream_write("— founding desk —", style="#cba6f7")
        self.state.streaming = True
        self._paint_ritual()
        self._paint_status()
        self._found_worker(pid)

    @work(thread=True)
    def _approve_worker(self, pid: str, eid: str) -> None:
        try:
            res = self.client.approve(pid, eid)
            err = None
        except ClientError as e:
            res, err = {}, str(e)

        def apply() -> None:
            if err:
                self.notify(f"approve failed: {err}", severity="error")
                return
            for e in self.state.entries:
                if str(e.get("id")) == eid:
                    e["status"] = "approved"
                    break
            if res.get("founding") is True:
                def on_confirm(choice: str | None) -> None:
                    if choice != "ok":
                        self._paint_ritual()
                        self._paint_status()
                        return
                    self._begin_found_stream(pid)

                self.push_screen(
                    ConfirmActionModal("Found this desk?", "", ok_label="found desk"),
                    on_confirm,
                )
                return
            self.state.entries = sort_queue(self.state.entries)
            self.query_one(QueuePane).entries = list(self.state.entries)
            self._paint_ritual()
            self._paint_status()
            self.query_one(DeskPane).stream_write(
                f"approved {eid} (bookmark only — not applied)",
                style="#74c7ec",
            )
            self.notify("approved", timeout=1.5)

        self.call_from_thread(apply)

    def action_comment(self) -> None:
        entry = self._selected()
        pid = self.state.project_id
        if not entry or not pid:
            return
        initial = str(entry.get("comment") or "")
        eid = str(entry.get("id"))

        def done(text: str | None) -> None:
            if text is None:
                return
            self._comment_worker(pid, eid, text)

        self.push_screen(CommentModal(initial), done)

    @work(thread=True)
    def _comment_worker(self, pid: str, eid: str, text: str) -> None:
        try:
            self.client.comment(pid, eid, text)
            err = None
        except ClientError as e:
            err = str(e)

        def apply() -> None:
            if err:
                self.notify(f"comment failed: {err}", severity="error")
                return
            for e in self.state.entries:
                if str(e.get("id")) == eid:
                    if text.strip():
                        e["comment"] = text.strip()
                    else:
                        e.pop("comment", None)
                    break
            hint = self.query_one("#comment-hint", Static)
            if text.strip():
                hint.update(f"[#f9e2af]note:[/] {text.strip()[:80]}")
            else:
                hint.update("")
            self.query_one(DeskPane).stream_write(
                "comment saved for sweep" if text.strip() else "comment cleared",
                style="#f9e2af",
            )
            self.notify("comment saved", timeout=1.5)

        self.call_from_thread(apply)

    def send_chat(self, message: str) -> None:
        pid = self.state.project_id
        if not pid:
            self.notify("no project", severity="warning")
            return
        entry = self._selected()
        eid = str(entry.get("id")) if entry else None
        desk = self.query_one(DeskPane)
        desk.stream_write(f"you: {message}", style="#89b4fa")
        self.state.streaming = True
        self._paint_ritual()
        self._paint_status()
        self._chat_worker(pid, message, eid)

    @work(thread=True)
    def _chat_worker(self, pid: str, message: str, eid: str | None) -> None:
        try:
            for ev in self.client.chat_events(pid, message, entry_id=eid):
                self._relay_ndjson_event(ev)
            err = None
        except ClientError as e:
            err = str(e)

        def done() -> None:
            self.state.streaming = False
            if err:
                self.query_one(DeskPane).stream_write(
                    f"chat error: {err}", style="#e06c75"
                )
            self._paint_ritual()
            self._paint_status()

        self.call_from_thread(done)

    def action_quit(self) -> None:
        if not self.state.dirty:
            self.exit()
            return

        def on_choice(choice: str | None) -> None:
            if choice is None:
                return
            if choice == "save":
                self._save_then(self.exit)
            elif choice == "discard":
                self.exit()

        self.push_screen(DiscardModal("Quit with unsaved editor changes?"), on_choice)

    def _run_command(self, cmd: str) -> None:
        actions = {
            "refresh": self.action_refresh,
            "save": self.action_save,
            "approve": self.action_approve,
            "comment": self.action_comment,
            "sweep": self.action_sweep,
            "toggle-diff": self.action_toggle_diff,
            "external-editor": self.action_external_editor,
            "compact": self.action_compact,
            "clear-session": self.action_clear_session,
            "begin-interview": self.action_begin_interview,
            "draft-proposal": self.action_draft_proposal,
            "found-desk": self.action_found_desk,
            "pick-project": self.action_pick_project,
            "focus-queue": lambda: self.query_one("#queue-list", ListView).focus(),
            "focus-editor": lambda: self.query_one("#editor", TextArea).focus(),
            "focus-composer": lambda: self.query_one("#composer", Input).focus(),
            "help": self.action_help,
            "quit": self.action_quit,
        }
        fn = actions.get(cmd)
        if fn:
            fn()
