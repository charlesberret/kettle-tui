"""Cwd → desk matching for --here (LAB-273)."""

from __future__ import annotations

from pathlib import Path

from kettle_tui.client import default_project_id, match_project_here


def _projects(tmp: Path) -> list[dict]:
    (tmp / "mono").mkdir()
    (tmp / "mono" / "nested").mkdir()
    (tmp / "other").mkdir()
    return [
        {"id": "mono", "name": "Mono", "root": str(tmp / "mono")},
        {"id": "nested", "name": "Nested", "root": str(tmp / "mono" / "nested")},
        {"id": "other", "name": "Other", "root": str(tmp / "other")},
        {"id": "ghost", "name": "Ghost"},  # no root — ignored
    ]


def test_exact_root_match(tmp_path: Path):
    projects = _projects(tmp_path)
    assert match_project_here(projects, tmp_path / "other") == "other"


def test_subdirectory_matches_ancestor(tmp_path: Path):
    projects = _projects(tmp_path)
    (tmp_path / "mono" / "src").mkdir()
    assert match_project_here(projects, tmp_path / "mono" / "src") == "mono"


def test_longest_root_wins(tmp_path: Path):
    projects = _projects(tmp_path)
    (tmp_path / "mono" / "nested" / "pkg").mkdir()
    assert match_project_here(projects, tmp_path / "mono" / "nested" / "pkg") == "nested"


def test_parent_of_root_does_not_match(tmp_path: Path):
    projects = _projects(tmp_path)
    assert match_project_here(projects, tmp_path) is None


def test_tilde_expansion(tmp_path: Path, monkeypatch):
    desk = tmp_path / "home-desk"
    desk.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    projects = [{"id": "home", "root": "~/home-desk"}]
    assert match_project_here(projects, desk) == "home"


def test_require_here_does_not_fall_back(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("KETTLE_PROJECT", raising=False)
    projects = _projects(tmp_path)
    assert (
        default_project_id(projects, here=tmp_path, require_here=True) is None
    )
    # without --here, unknown cwd still falls back to first
    assert default_project_id(projects, here=tmp_path) == "mono"


def test_preferred_beats_here(tmp_path: Path):
    projects = _projects(tmp_path)
    assert (
        default_project_id(
            projects, "other", here=tmp_path / "mono", require_here=True
        )
        == "other"
    )


def test_env_beats_here(tmp_path: Path, monkeypatch):
    projects = _projects(tmp_path)
    monkeypatch.setenv("KETTLE_PROJECT", "other")
    assert (
        default_project_id(
            projects, here=tmp_path / "mono", require_here=True
        )
        == "other"
    )


def test_here_match_used_when_present(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("KETTLE_PROJECT", raising=False)
    projects = _projects(tmp_path)
    assert (
        default_project_id(projects, here=tmp_path / "other") == "other"
    )
