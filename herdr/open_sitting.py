#!/usr/bin/env python3
"""Open or focus a Kettle sitting tab for the caller's directory.

Herdr action (prefix+k / kettle-tui.open). Stdlib only. Does not import
kettle_tui — this process may run before the package is on PYTHONPATH.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PLUGIN_ID = "kettle-tui"
SITTING_ENTRY = "sitting"
SITTING_LABEL = "kettle"
HERDR_CONFIG = Path("~/.config/herdr/config.toml").expanduser()
BLOCK_BEGIN = "# >>> kettle-tui keybindings >>>"
BLOCK_END = "# <<< kettle-tui keybindings <<<"
KEY_BLOCK = [
    BLOCK_BEGIN,
    "[[keys.command]]",
    'key = "prefix+k"',
    'type = "plugin_action"',
    'command = "kettle-tui.open"',
    'description = "kettle: project for this directory"',
    BLOCK_END,
]
CTX_KEYS = (
    "workspace_id",
    "workspace_cwd",
    "tab_id",
    "tab_label",
    "focused_pane_id",
    "focused_pane_cwd",
    "focused_pane_agent",
)


class HerdrError(RuntimeError):
    pass


def herdr_bin(env: dict | None = None) -> str:
    env = os.environ if env is None else env
    return env.get("HERDR_BIN_PATH") or "herdr"


def herdr(*args: str, env: dict | None = None) -> dict:
    proc = subprocess.run(
        [herdr_bin(env), *args],
        capture_output=True,
        text=True,
        env=None if env is None else env,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"herdr {' '.join(args)} failed"
        raise HerdrError(err)
    raw = (proc.stdout or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HerdrError(f"herdr {' '.join(args)}: not JSON") from e
    if isinstance(data, dict) and "result" in data:
        result = data["result"]
        return result if isinstance(result, dict) else {"value": result}
    return data if isinstance(data, dict) else {}


def resolve_path(path: str | None) -> Path | None:
    if not path:
        return None
    try:
        return Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return None


def same_place(a: str | None, b: str | None) -> bool:
    """True if either path is the other, or one is inside the other."""
    pa, pb = resolve_path(a), resolve_path(b)
    if pa is None or pb is None:
        return False
    if pa == pb:
        return True
    try:
        pa.relative_to(pb)
        return True
    except ValueError:
        pass
    try:
        pb.relative_to(pa)
        return True
    except ValueError:
        return False


def invocation_ctx(env: dict | None = None) -> dict:
    env = os.environ if env is None else env
    raw = env.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}"
    try:
        parsed = json.loads(raw)
        data = parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        data = {}
    out = {k: data[k] for k in CTX_KEYS if data.get(k)}
    if not out.get("workspace_id") and env.get("HERDR_WORKSPACE_ID"):
        out["workspace_id"] = env["HERDR_WORKSPACE_ID"]
    pane_id = out.get("focused_pane_id") or env.get("HERDR_PANE_ID")
    if pane_id:
        out["focused_pane_id"] = pane_id
    if not out.get("focused_pane_cwd"):
        cwd = out.get("workspace_cwd") or env.get("PWD")
        if cwd:
            out["focused_pane_cwd"] = cwd
    return out


def pane_cwd(pane: dict) -> str:
    return str(pane.get("cwd") or pane.get("foreground_cwd") or "")


def is_sitting_pane(pane: dict) -> bool:
    return (pane.get("label") or "").strip() == SITTING_LABEL


def find_sitting_pane(
    panes: list[dict],
    *,
    cwd: str | None,
    workspace_id: str | None = None,
) -> dict | None:
    """Existing kettle sitting for this place, if any."""
    hits = []
    for p in panes:
        if not is_sitting_pane(p):
            continue
        if workspace_id and p.get("workspace_id") and p.get("workspace_id") != workspace_id:
            continue
        if cwd and not same_place(pane_cwd(p), cwd):
            continue
        hits.append(p)
    return hits[0] if hits else None


def list_panes(workspace_id: str | None = None, env: dict | None = None) -> list[dict]:
    args = ["pane", "list"]
    if workspace_id:
        args += ["--workspace", workspace_id]
    data = herdr(*args, env=env)
    panes = data.get("panes") or []
    return panes if isinstance(panes, list) else []


def open_sitting(env: dict | None = None) -> int:
    ctx = invocation_ctx(env)
    cwd = ctx.get("focused_pane_cwd") or os.getcwd()
    ws = ctx.get("workspace_id")
    try:
        panes = list_panes(ws, env=env)
    except HerdrError as e:
        print(f"kettle-tui: {e}", file=sys.stderr)
        return 1
    existing = find_sitting_pane(panes, cwd=cwd, workspace_id=ws)
    if existing and existing.get("pane_id"):
        try:
            herdr("plugin", "pane", "focus", str(existing["pane_id"]), env=env)
        except HerdrError:
            try:
                herdr("plugin", "pane", "open", *open_args(cwd, ws), env=env)
            except HerdrError as e:
                print(f"kettle-tui: {e}", file=sys.stderr)
                return 1
        return 0
    try:
        herdr("plugin", "pane", "open", *open_args(cwd, ws), env=env)
    except HerdrError as e:
        print(f"kettle-tui: {e}", file=sys.stderr)
        return 1
    return 0


def open_args(cwd: str, workspace_id: str | None) -> list[str]:
    args = [
        "--plugin",
        PLUGIN_ID,
        "--entrypoint",
        SITTING_ENTRY,
        "--placement",
        "tab",
        "--cwd",
        cwd,
        "--focus",
    ]
    if workspace_id:
        args += ["--workspace", workspace_id]
    return args


# --- prefix+k --------------------------------------------------------------


def keys_install_text(text: str) -> str:
    lines = _strip_block(text.splitlines())
    if lines and lines[-1].strip():
        lines.append("")
    lines.extend(KEY_BLOCK)
    return "\n".join(lines) + "\n"


def keys_uninstall_text(text: str) -> str:
    lines = _strip_block(text.splitlines())
    while lines and not lines[-1].strip():
        lines.pop()
    body = "\n".join(lines)
    return body + ("\n" if body else "")


def _strip_block(lines: list[str]) -> list[str]:
    out, inside = [], False
    for ln in lines:
        s = ln.strip()
        if s == BLOCK_BEGIN:
            inside = True
            continue
        if s == BLOCK_END:
            inside = False
            continue
        if not inside:
            out.append(ln)
    # drop a trailing blank left behind the block
    while out and not out[-1].strip() and len(out) >= 2 and not out[-2].strip():
        out.pop()
    return out


def cmd_keys(action: str) -> int:
    path = HERDR_CONFIG
    if not path.is_file():
        print(f"kettle-tui: no {path}", file=sys.stderr)
        return 1
    original = path.read_text(encoding="utf-8")
    if action == "install":
        updated = keys_install_text(original)
    elif action == "uninstall":
        updated = keys_uninstall_text(original)
    else:
        print("kettle-tui: keys install|uninstall", file=sys.stderr)
        return 2
    if updated == original:
        return 0
    backup = path.with_suffix(path.suffix + ".bak")
    backup.write_text(original, encoding="utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(updated, encoding="utf-8")
    tmp.replace(path)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["keys"]:
        return cmd_keys(args[1] if len(args) > 1 else "")
    return open_sitting()


if __name__ == "__main__":
    raise SystemExit(main())
