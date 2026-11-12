"""Tests for the highest-risk gaps from docs/test-gaps.md (no source changes)."""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path

import pytest

from kettle_tui import client as client_mod
from kettle_tui import demo
from kettle_tui.__main__ import build_parser, main
from kettle_tui.client import (
    ClientError,
    KettleClient,
    entry_status,
    is_proposal_entry,
    is_set_aside,
    load_discovery,
    resolve_project_root,
)


# ── load_discovery ──────────────────────────────────────────────────────


def test_load_discovery_explicit_url_uses_flag_source(tmp_path, monkeypatch):
    disc = tmp_path / "server.json"
    disc.write_text(
        json.dumps({"url": "http://127.0.0.1:9999", "token": "from-file"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(client_mod, "DISCOVERY_PATH", disc)
    d = load_discovery("http://127.0.0.1:5555/")
    assert d.source == "flag"
    assert d.url == "http://127.0.0.1:5555"
    assert d.token == "from-file"


def test_load_discovery_reads_server_json(tmp_path, monkeypatch):
    disc = tmp_path / "server.json"
    disc.write_text(
        json.dumps(
            {
                "url": "http://127.0.0.1:4242",
                "token": "secret",
                "port": 4242,
                "projects": "/tmp/reg",
                "pid": 42,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(client_mod, "DISCOVERY_PATH", disc)
    d = load_discovery()
    assert d.source == "discovery"
    assert d.token == "secret"
    assert d.projects_path == "/tmp/reg"
    assert d.pid == 42


def test_load_discovery_defaults_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(client_mod, "DISCOVERY_PATH", tmp_path / "missing.json")
    d = load_discovery()
    assert d.source == "default"
    assert d.url == client_mod.DEFAULT_URL
    assert d.port == 4242


# ── entry classification helpers ────────────────────────────────────────


def test_entry_status_defaults_to_pending():
    assert entry_status(None) == "pending"
    assert entry_status({}) == "pending"
    assert entry_status({"status": "Approved"}) == "approved"


def test_is_proposal_entry_by_id_or_phase():
    assert is_proposal_entry({"id": "desk-proposal"})
    assert is_proposal_entry({"id": "x", "founding_phase": "proposal"})
    assert not is_proposal_entry({"id": "readme"})
    assert not is_proposal_entry(None)


def test_is_set_aside_only_for_set_aside_status():
    assert is_set_aside({"status": "set-aside"})
    assert not is_set_aside({"status": "rework"})
    assert not is_set_aside(None)


def test_resolve_project_root_rejects_empty_and_tilde(tmp_path, monkeypatch):
    assert resolve_project_root(None) is None
    assert resolve_project_root("") is None
    desk = tmp_path / "desk"
    desk.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    got = resolve_project_root("~/desk")
    assert got == desk.resolve()


# ── demo file_content path resolution ───────────────────────────────────


def test_demo_file_content_unknown_path_has_stub_body():
    body = demo.file_content("nowhere/unknown.md")
    assert "(demo file)" in body
    assert "unknown.md" in body


def test_demo_file_content_matches_by_basename():
    body = demo.file_content("deep/nested/README.md")
    assert body.startswith("# Demo Project")


# ── add_project validation and slug ids ─────────────────────────────────


def test_add_project_requires_root():
    c = KettleClient(demo_mode=True)
    with pytest.raises(ClientError) as ei:
        c.add_project("Name", "", "A valid founding sentence here.")
    assert ei.value.status == 400
    assert "folder" in ei.value.message.lower()


def test_add_project_slug_dedupes_when_name_collides():
    c = KettleClient(demo_mode=True)
    first = c.add_project("My App", "/tmp/a", "First project founding note.")
    second = c.add_project("My App", "/tmp/b", "Second project founding note.")
    assert first["id"] == "my_app"
    assert second["id"] == "my_app_2"


# ── CLI --check (demo, no TUI) ──────────────────────────────────────────


def test_cli_check_demo_lists_projects(capsys):
    rc = main(["--demo", "--check"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ok  app=kettle version=demo" in out
    assert "source=demo" in out
    assert "demo-desk" in out


def test_cli_check_here_no_match_exits_one(capsys, tmp_path):
    rc = main(["--demo", "--check", "--here", str(tmp_path / "nowhere")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no project" in err.lower()


def test_build_parser_here_const_means_cwd():
    p = build_parser()
    args = p.parse_args(["--here"])
    assert args.here == ""
