"""The sitting's copy catalog, as the TUI reads it.

kettle-core owns every user-facing string in ``kettle/static/ui-copy.json``
(SITTING-FLOWS §7, docs/UI.md § Shared UI copy). The TUI resolves labels, hints
and state messages from that catalog instead of writing its own sentences:

1. live core: ``GET /api/ui-copy`` — the running core's catalog merged with the
   user's overrides, so a relabel in the web sitting's debug mode reaches here too;
2. ``kettle.uicopy`` in process, when kettle-core is importable (demo mode, or a
   core too old to serve ``/api/ui-copy``);
3. the literals below.

The literals are a copy of the catalog entries this viewport uses, kept equal to
core's text (``tests/test_uicopy.py`` checks them against a core checkout when one
is available). They exist so an old core or a missing catalog degrades to the
right words, never to a blank button. Add a key here only when the TUI renders it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

FALLBACK_KEYS: dict[str, dict[str, str]] = {
    "approveLabel": {"text": "Approve", "short": "approve"},
    "approveHint": {
        "text": "Mark your decision on this file. Nothing changes until you press Finish batch.",
        "short": "Mark your decision on this file.",
    },
    "approveHintFounding": {
        "text": "Approve the proposal to set up your project",
        "short": "Edit and approve the proposal",
    },
    "commentLabel": {"text": "Comment"},
    "commentHint": {
        "text": "Leave a note on this file for the agent to act on",
        "short": "Leave a note for the agent",
    },
    "commentTiedHint": {
        "text": "This note stays with the file. The agent reads it when you finish the batch.",
        "short": "The agent reads this when you finish the batch",
    },
    "commentSaveLabel": {"text": "Save"},
    "notThisLabel": {"text": "Not this"},
    "notThisHint": {
        "text": (
            "Set this file aside. The agent takes it as a sign of what you don't want, "
            "and tells you what it learned when you finish the batch."
        ),
        "short": "Set this file aside",
    },
    "notThisUndoLabel": {"text": "Bring back"},
    "notThisUndoHint": {"text": "Put this file back in the batch"},
    "sweepLabel": {"text": "Finish batch", "short": "Finish"},
    "sweepHint": {
        "text": (
            "Apply your decisions, review what changed, note anything else it affects, "
            "and line up the next files"
        ),
        "short": "Apply your decisions and line up the next files",
    },
    "sweepConfirmTitle": {"text": "Finish This Batch?", "short": "Finish now?"},
    "sweepConfirmBody": {
        "text": (
            "Applies the decisions you marked, reviews what changed in each file, notes "
            "anything else it affects, saves a record of this round, and lines up the "
            "next batch."
        ),
        "short": (
            "Applies your decisions, reviews the batch, saves a record, and lines up "
            "the next files."
        ),
    },
    "sweepThrustPlaceholder": {
        "text": "Anything on your mind as you finish? The agent reads this first (optional)",
        "short": "Anything on your mind? (optional)",
    },
    "draftProposalLabel": {"text": "Auto: let the Kettle agent decide", "short": "Auto-mode"},
    "draftProposalHint": {
        "text": "Skip the rest of the questions and let the agent write the proposal now",
        "short": "Skip the interview",
    },
    "foundingHeadTitle": {"text": "Setting up your project"},
    "refoundTitle": {"text": "Set up this project again?", "short": "Set up again?"},
    "addProjectTitle": {"text": "Add Project", "short": "Add project"},
    "addProjectStart": {"text": "Start Interview", "short": "Start"},
    "addProjectFoundingQuestion": {
        "text": "What's the state of this project and how could a second reader help?"
    },
    "addProjectNamePlaceholder": {"text": "Name"},
    "addProjectFolderPlaceholder": {"text": "Folder path"},
    "queueTitle": {"text": "Queue"},
    "spinWorking": {"text": "Working…", "short": "Working"},
}

FALLBACK_STATES: dict[str, dict[str, str]] = {
    "attached": {
        "line": "Pick a file",
        "short": "Pick a file to begin.",
        "full": (
            "Kettle has lined up a batch of files for you. Pick one to read it with the "
            "agent's summary. Mark your decisions as you go, then press Finish batch when "
            "you are done."
        ),
    },
    "interviewing": {
        "line": "Chat with the agent",
        "short": (
            "Chat with the Kettle agent; when you are done it drafts a proposal to review."
        ),
        "full": (
            "Chat with the Kettle agent to tell it what this project is and what you want "
            "from a second reader. When you are ready to proceed, the agent writes a "
            "proposal for you to review and edit. The queue fills after you approve the "
            "proposal."
        ),
    },
    "proposing": {
        "line": "Edit the proposal",
        "short": "Edit the proposal, then approve it to set up your project.",
        "full": (
            "The proposal is open in the editor. This is the document of record for your "
            "Kettle project. Read it, change anything that does not fit, and chat with the "
            "agent to revise or co-write."
        ),
    },
    "founding": {
        "line": "Setting up…",
        "short": "Setting up your project…",
        "full": (
            "The agent is setting up your project and lining up the first batch of files "
            "from your proposal. This may take a few minutes."
        ),
    },
    "founded": {
        "line": "Ready to begin",
        "short": "Your project is ready. Pick a file to begin.",
        "full": (
            "Your project is set up and the first batch of files is ready. Pick one to "
            "read it with the agent's summary, edit and comment as you go, then press "
            "Finish batch when you are done."
        ),
    },
    "sweeping": {
        "line": "Finishing the batch…",
        "short": "Applying your decisions and lining up the next batch.",
        "full": (
            "The agent is applying your decisions, reviewing what changed, noting anything "
            "else it affects, saving a record of this round, and lining up the next batch."
        ),
    },
    "broken": {
        "line": "This project needs attention.",
        "short": "Kettle could not open this project. Nothing has been changed.",
        "full": (
            "Kettle could not open this project. Its folder may have moved, or its records "
            "may be unreadable. Nothing has been changed on disk. Check the project folder, "
            "or set the project up again — your own files are never touched."
        ),
    },
}

REGISTERS = ("line", "short", "full")


def in_process_payload() -> dict[str, Any] | None:
    """The shipped catalog via ``kettle.uicopy`` when kettle-core is importable.

    No user overrides: those live beside the core's settings and are only merged
    by the running server. None when core is not installed or has no catalog.
    """
    try:
        import kettle  # type: ignore[import-not-found]
        from kettle import uicopy  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        static = Path(kettle.__file__).resolve().parent / "static"
        pay = uicopy.payload(static)
    except Exception:
        return None
    if not isinstance(pay, dict) or not pay.get("keys"):
        return None
    return pay


class UiCopy:
    """Resolve catalog keys and flow states, falling back to the literals above.

    A value the catalog has wins; a key or register it lacks falls back to the
    literal, so an older core that predates a key still shows the right words.
    """

    def __init__(self, payload: dict[str, Any] | None = None, *, source: str = "fallback"):
        self.payload: dict[str, Any] = payload if isinstance(payload, dict) else {}
        self.source = source if self.payload else "fallback"

    def _rec(self, key: str) -> dict[str, Any]:
        keys = self.payload.get("keys")
        rec = keys.get(key) if isinstance(keys, dict) else None
        return rec if isinstance(rec, dict) else {}

    def text(self, key: str, *, short: bool = False) -> str:
        """``short`` prefers the terse variant, then the full text (catalog rule)."""
        for rec in (self._rec(key), FALLBACK_KEYS.get(key) or {}):
            if short:
                s = rec.get("short")
                if isinstance(s, str) and s:
                    return s
            t = rec.get("text")
            if isinstance(t, str) and t:
                return t
        return ""

    def state(self, name: str, register: str = "line") -> str:
        """The message for a flow state in ``line`` / ``short`` / ``full``.

        A missing register falls back to the longest one present (SITTING-FLOWS §7).
        """
        states = self.payload.get("states")
        live = states.get(name) if isinstance(states, dict) else None
        for rec in (live if isinstance(live, dict) else {}, FALLBACK_STATES.get(name) or {}):
            v = rec.get(register)
            if isinstance(v, str) and v:
                return v
            for alt in ("full", "short", "line"):
                v = rec.get(alt)
                if isinstance(v, str) and v:
                    return v
        return ""

    def keycap(self, key: str) -> str:
        """A ritual keycap label: the short form, lowercased to the TUI's key style.

        Lowercasing is presentation (the TUI's equivalent of CSS ``text-transform``),
        not a rewrite — the words are the catalog's.
        """
        return self.text(key, short=True).lower()
