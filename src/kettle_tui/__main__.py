"""CLI entry: kettle-tui."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .app import KettleTui
from .client import ClientError, KettleClient, default_project_id


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kettle-tui",
        description="Terminal viewport for Kettle",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument(
        "--demo",
        action="store_true",
        help="offline sample data (no server)",
    )
    p.add_argument(
        "--url",
        default=None,
        help="core base URL (default: discovery or http://127.0.0.1:4242)",
    )
    p.add_argument(
        "--token",
        default=None,
        help="X-Kettle-Token (default: from ~/.cache/kettle/server.json)",
    )
    p.add_argument(
        "--project",
        default=None,
        help="initial project id",
    )
    p.add_argument(
        "--here",
        nargs="?",
        const="",
        metavar="DIR",
        default=None,
        help="open the project registered for DIR (default: cwd). "
        "Does not fall back to another project.",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="ping + list projects, then exit (no TUI)",
    )
    return p


def _here_from_args(args: argparse.Namespace) -> tuple[str | None, bool]:
    if args.here is None:
        return None, False
    raw = (args.here or "").strip()
    return (raw or None), True


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    here, require_here = _here_from_args(args)

    client = KettleClient(demo_mode=args.demo, url=args.url, token=args.token)

    if args.check:
        return _check(client, args.project, here=here, require_here=require_here)

    app = KettleTui(
        client,
        project_id=args.project,
        here=here,
        require_here=require_here,
    )
    app.run()
    return 0


def _check(
    client: KettleClient,
    project: str | None,
    *,
    here: str | None = None,
    require_here: bool = False,
) -> int:
    try:
        if not client.demo_mode and not client.ensure_reachable(timeout_s=8.0):
            print("error: cannot reach kettle core", file=sys.stderr)
            return 1
        ping = client.ping()
        projects = client.projects()
    except ClientError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        settings = client.settings()
    except ClientError:
        settings = {}
    pid = default_project_id(
        projects,
        project,
        here=here,
        require_here=require_here,
        last=settings.get("lastProject"),
    )
    here_display = here or (os.getcwd() if require_here else None)

    print(f"ok  app={ping.get('app')} version={ping.get('version')}")
    print(f"    source={client.discovery.source} url={client.discovery.url}")
    print(f"    projects={len(projects)}")
    for p in projects[:12]:
        mark = " *" if pid and p.get("id") == pid else ""
        print(f"      - {p.get('id')}  ({p.get('name')}){mark}")
    if len(projects) > 12:
        print(f"      … +{len(projects) - 12} more")

    if require_here and not pid:
        loc = here_display or os.getcwd()
        try:
            loc = str(Path(loc).expanduser().resolve())
        except (OSError, RuntimeError):
            pass
        print(f"    no project for {loc}", file=sys.stderr)
        return 1

    if pid:
        try:
            q = client.queue(str(pid), mutate=False)
            n = len(q.get("entries") or [])
            print(f"    queue[{pid}]={n} files")
        except ClientError as e:
            print(f"    queue error: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
