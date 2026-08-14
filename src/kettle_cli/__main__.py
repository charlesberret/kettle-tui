"""CLI entry: kettle-tui."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .app import KettleTui
from .client import ClientError, KettleClient


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kettle-tui",
        description="Textual TUI spike for the Kettle review desk",
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
        "--check",
        action="store_true",
        help="ping + list projects, then exit (no TUI)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    client = KettleClient(demo_mode=args.demo, url=args.url, token=args.token)

    if args.check:
        return _check(client, args.project)

    app = KettleTui(client, project_id=args.project)
    app.run()
    return 0


def _check(client: KettleClient, project: str | None) -> int:
    try:
        if not client.demo_mode and not client.ensure_reachable(timeout_s=8.0):
            print("error: cannot reach kettle core", file=sys.stderr)
            return 1
        ping = client.ping()
        projects = client.projects()
    except ClientError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"ok  app={ping.get('app')} version={ping.get('version')}")
    print(f"    source={client.discovery.source} url={client.discovery.url}")
    print(f"    projects={len(projects)}")
    for p in projects[:12]:
        mark = " *" if project and p.get("id") == project else ""
        print(f"      - {p.get('id')}  ({p.get('name')}){mark}")
    if len(projects) > 12:
        print(f"      … +{len(projects) - 12} more")

    if project or projects:
        pid = project or projects[0]["id"]
        try:
            q = client.queue(str(pid), mutate=False)
            n = len(q.get("entries") or [])
            print(f"    queue[{pid}]={n} entries")
        except ClientError as e:
            print(f"    queue error: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
