"""Hermetic tests for KettleClient.ensure_reachable (LAB-54).

Auto-attach / spawn behaviour for live mode: on a failed ping the client should
launch a core and re-attach once discovery points at a reachable server.

These tests never touch port 4242 (the real desk) and never spawn the real
`kettle` binary — PATH is fully controlled in every case, and the "core" is an
in-process HTTP server on an ephemeral port.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from kettle_cli import client as client_mod
from kettle_cli.client import KettleClient


def _free_port() -> int:
    """A port that nothing is listening on (bind, read, release)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server contract)
        if self.path.startswith("/api/ping"):
            body = json.dumps(
                {"app": "kettle", "version": "test", "api_version": 0}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):  # noqa: A002 — silence test noise
        return


@pytest.fixture
def live_core():
    """In-process stand-in for a running kettle core; yields its base URL."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}", port
    finally:
        server.shutdown()
        server.server_close()


def _point_discovery(path: Path, url: str, port: int) -> None:
    path.write_text(json.dumps({"url": url, "port": port}), encoding="utf-8")


def test_demo_mode_is_always_reachable():
    assert KettleClient(demo_mode=True).ensure_reachable() is True


def test_no_kettle_on_path_returns_false(tmp_path, monkeypatch):
    # Discovery points at a dead port; PATH has no `kettle` → cannot spawn.
    disc = tmp_path / "server.json"
    _point_discovery(disc, f"http://127.0.0.1:{_free_port()}", _free_port())
    monkeypatch.setattr(client_mod, "DISCOVERY_PATH", disc)
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))

    client = KettleClient()
    with pytest.raises(client_mod.ClientError):
        client.ping()  # dead port, not 4242
    assert client.ensure_reachable(timeout_s=1.0) is False


def test_spawns_core_and_reattaches(tmp_path, monkeypatch, live_core):
    live_url, live_port = live_core
    disc = tmp_path / "server.json"
    # Start pointed at a dead port so the first ping fails.
    _point_discovery(disc, f"http://127.0.0.1:{_free_port()}", _free_port())
    monkeypatch.setattr(client_mod, "DISCOVERY_PATH", disc)

    # Fake `kettle`: on `--web --no-open`, repoint discovery at the live core.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "kettle"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json\n"
        "from pathlib import Path\n"
        f"Path({str(disc)!r}).write_text("
        f"json.dumps({{'url': {live_url!r}, 'port': {live_port}}}))\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    client = KettleClient()
    with pytest.raises(client_mod.ClientError):
        client.ping()  # dead port before spawn

    assert client.ensure_reachable(timeout_s=5.0) is True
    # Re-attached to the spawned core's discovered URL.
    assert client.ping().get("app") == "kettle"
    assert client._base == live_url
