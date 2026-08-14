"""Offline sample desk data for the TUI (Shape B)."""

from __future__ import annotations

from copy import deepcopy

DEMO_PROJECTS = [
    {
        "id": "demo-desk",
        "name": "Demo Desk",
        "root": "/tmp/kettle-demo-desk",
        "kit": "code",
    },
    {
        "id": "notes",
        "name": "Notes",
        "root": "/tmp/kettle-demo-notes",
        "kit": "writing",
    },
    {
        "id": "new-desk",
        "name": "New Desk (founding)",
        "root": "/tmp/kettle-demo-new",
        "kit": "code",
    },
]

# Per-project founding phase for offline wizard demos.
# none → interviewing → proposing → founded
DEMO_PHASES: dict[str, str] = {
    "demo-desk": "founded",
    "notes": "founded",
    "new-desk": "none",
}

PROPOSAL_PATH = ".kettle/DESK-PROPOSAL.md"

PROPOSAL_BODY = """# Desk Proposal

## What This Desk Is For

A local-first review instrument for agent-prepared work on **New Desk**.

## Who Uses It

You — reviewing work the curator prepares.

## First Queue

1. README — orient and correct identity claims
2. Architecture note — confirm desk-is-files

## Notes From The Interview

(Demo proposal — edit freely, then Found Desk.)
"""

_DEMO_QUEUES: dict[str, list[dict]] = {
    "new-desk": [],
    "demo-desk": [
        {
            "id": "readme-overview",
            "title": "README — Identity And How To Run",
            "path": "README.md",
            "brief": (
                "This is the front door — the README that tells someone what the "
                "project is and how to run it. Tighten identity, install, and "
                "quick start against the real CLI. Adjust wording until it feels "
                "accurate, then accept; send back notes if a claim is wrong."
            ),
            "ask": "Edit until install, quick start, and status match reality",
            "urgency": "high",
            "status": "pending",
            "ask_type": "tuning",
            "diff": {"added": 10, "removed": 0},
        },
        {
            "id": "architecture-map",
            "title": "Architecture Map",
            "path": "docs/ARCHITECTURE.md",
            "brief": (
                "A one-page map of packages and viewports. Check that the desk "
                "boundary (files vs adapters) is stated clearly, and that the "
                "API contract is named as the single authority."
            ),
            "ask": "Confirm the viewport list and desk-is-files rule",
            "urgency": "medium",
            "status": "pending",
            "ask_type": "review",
            "diff": {"added": 4, "removed": 1},
        },
        {
            "id": "theme-roles",
            "title": "Theme Role Ladder",
            "path": "docs/UI.md",
            "brief": (
                "Chrome should stay gray; primary ink is scarce. Read the type "
                "hierarchy table and decide whether the queue second lines and "
                "pane titles follow the ladder."
            ),
            "ask": "Mark any chrome that still paints body-white",
            "urgency": "low",
            "status": "pending",
            "ask_type": "review",
            "diff": None,
        },
        {
            "id": "approved-sample",
            "title": "License Header (already ruled)",
            "path": "LICENSE",
            "brief": "MIT header checked against upstream; no further action.",
            "ask": "No ask — sample approved row",
            "urgency": "low",
            "status": "approved",
            "ask_type": "review",
            "diff": None,
        },
    ],
    "notes": [
        {
            "id": "weekly-note",
            "title": "Weekly Note Draft",
            "path": "notes/2026-08-11.md",
            "brief": "Draft for the week. Trim repetition; keep the decision log.",
            "ask": "Cut to one screen, keep open questions",
            "urgency": "medium",
            "status": "pending",
            "ask_type": "edit",
            "diff": {"added": 40, "removed": 12},
        },
    ],
}

_DEMO_FILES: dict[str, str] = {
    "README.md": (
        "# Demo Desk\n\n"
        "Local-first review instrument for agent-prepared work.\n\n"
        "## Install\n\n"
        "```sh\npip install -e .\nkettle doctor\n```\n\n"
        "## Quick start\n\n"
        "```sh\nkettle add ~/path/to/project\nkettle --web\n```\n"
    ),
    "docs/ARCHITECTURE.md": (
        "# Architecture\n\n"
        "Desk-is-files. Viewports attach to the localhost API.\n\n"
        "| Viewport | Role |\n|----------|------|\n"
        "| Browser | reference sitting |\n"
        "| Desktop v2 | plugin-shaped |\n"
        "| VS Code | host editor + Desk |\n"
    ),
    "docs/UI.md": (
        "# Sitting UI conventions\n\n"
        "Type hierarchy: heading, label, secondary, primary, muted.\n"
        "Chrome is gray. Primary ink is scarce.\n"
    ),
    "LICENSE": "MIT License\n\nCopyright (c) 2026\n",
    "notes/2026-08-11.md": (
        "# Week of 2026-08-11\n\n"
        "## Decisions\n\n- Prefer plugin-shaped sitting for new viewports.\n\n"
        "## Open\n\n- Theme packs in the TUI?\n"
    ),
    PROPOSAL_PATH: PROPOSAL_BODY,
}


def proposal_entry() -> dict:
    return {
        "id": "desk-proposal",
        "title": "Desk Proposal",
        "path": PROPOSAL_PATH,
        "brief": "Edit until the proposal matches your intent, then Found Desk.",
        "ask": "Approve when ready — that founds the desk and stocks the first queue",
        "urgency": "high",
        "status": "pending",
        "ask_type": "founding",
        "founding_phase": "proposal",
        "diff": None,
    }


def first_content_batch() -> list[dict]:
    return [
        {
            "id": "readme-overview",
            "title": "README — Identity And How To Run",
            "path": "README.md",
            "brief": "Front door for the new desk — tighten install and identity.",
            "ask": "Edit until install and status match reality",
            "urgency": "high",
            "status": "pending",
            "ask_type": "tuning",
            "diff": {"added": 3, "removed": 0},
        },
        {
            "id": "architecture-map",
            "title": "Architecture Map",
            "path": "docs/ARCHITECTURE.md",
            "brief": "Confirm desk-is-files and viewport list.",
            "ask": "Confirm the desk boundary wording",
            "urgency": "medium",
            "status": "pending",
            "ask_type": "review",
            "diff": None,
        },
    ]


def fresh_queues() -> dict[str, list[dict]]:
    return deepcopy(_DEMO_QUEUES)


def file_content(path: str) -> str:
    # Match by basename or full relative path
    if path in _DEMO_FILES:
        return _DEMO_FILES[path]
    name = path.rsplit("/", 1)[-1]
    for k, v in _DEMO_FILES.items():
        if k == path or k.endswith("/" + name) or k == name:
            return v
    return f"(demo file)\n# {path}\n\nNo sample body for this path.\n"
