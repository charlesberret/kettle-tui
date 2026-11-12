"""CLI ``--check`` in offline demo mode (no TUI, no network)."""

from __future__ import annotations

from kettle_tui.__main__ import main


def test_check_demo_exits_zero(capsys):
    assert main(["--check", "--demo"]) == 0
    err = capsys.readouterr().err
    assert err == ""


def test_check_demo_prints_ping_and_project_list(capsys):
    main(["--check", "--demo"])
    out = capsys.readouterr().out
    assert "ok  app=kettle version=demo" in out
    assert "projects=3" in out
    assert "demo-desk" in out
    assert "new-desk" in out


def test_check_demo_with_project_star_marks_selection(capsys):
    main(["--check", "--demo", "--project", "demo-desk"])
    out = capsys.readouterr().out
    assert "demo-desk  (Demo Project) *" in out
    assert "queue[demo-desk]=4 files" in out


def test_check_demo_new_desk_reports_empty_queue(capsys):
    main(["--check", "--demo", "--project", "new-desk"])
    out = capsys.readouterr().out
    assert "new-desk  (New Project (setup)) *" in out
    assert "queue[new-desk]=0 files" in out
