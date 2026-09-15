"""Pure helpers for the Herdr sitting launcher (LAB-273)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "herdr" / "open_sitting.py"
_spec = importlib.util.spec_from_file_location("kettle_open_sitting", _SRC)
assert _spec and _spec.loader
open_sitting = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(open_sitting)


def test_same_place_exact(tmp_path: Path):
    a = tmp_path / "proj"
    a.mkdir()
    assert open_sitting.same_place(str(a), str(a))


def test_same_place_nested(tmp_path: Path):
    a = tmp_path / "proj"
    (a / "src").mkdir(parents=True)
    assert open_sitting.same_place(str(a / "src"), str(a))
    assert open_sitting.same_place(str(a), str(a / "src"))


def test_same_place_siblings_false(tmp_path: Path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert not open_sitting.same_place(str(tmp_path / "a"), str(tmp_path / "b"))


def test_find_sitting_by_label_and_cwd(tmp_path: Path):
    desk = tmp_path / "desk"
    desk.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    panes = [
        {
            "pane_id": "w1:p1",
            "label": "kettle-all · grok",
            "cwd": str(desk),
            "workspace_id": "w1",
        },
        {
            "pane_id": "w1:p2",
            "label": "kettle",
            "cwd": str(desk),
            "workspace_id": "w1",
        },
        {
            "pane_id": "w1:p3",
            "label": "kettle",
            "cwd": str(other),
            "workspace_id": "w1",
        },
    ]
    hit = open_sitting.find_sitting_pane(panes, cwd=str(desk / "src"), workspace_id="w1")
    assert hit is not None
    assert hit["pane_id"] == "w1:p2"


def test_find_sitting_skips_other_workspace(tmp_path: Path):
    desk = tmp_path / "desk"
    desk.mkdir()
    panes = [
        {
            "pane_id": "w2:p1",
            "label": "kettle",
            "cwd": str(desk),
            "workspace_id": "w2",
        }
    ]
    assert (
        open_sitting.find_sitting_pane(panes, cwd=str(desk), workspace_id="w1")
        is None
    )


def test_invocation_ctx_prefers_plugin_json():
    env = {
        "HERDR_PLUGIN_CONTEXT_JSON": '{"workspace_id":"wS","focused_pane_cwd":"/tmp/from-ctx"}',
        "HERDR_WORKSPACE_ID": "ignored",
        "PWD": "/tmp/pwd",
    }
    ctx = open_sitting.invocation_ctx(env)
    assert ctx["workspace_id"] == "wS"
    assert ctx["focused_pane_cwd"] == "/tmp/from-ctx"


def test_keys_install_uninstall_round_trip():
    original = "[keys]\nnew_tab = \"\"\n"
    installed = open_sitting.keys_install_text(original)
    assert 'key = "prefix+k"' in installed
    assert 'command = "kettle-tui.open"' in installed
    assert installed.count("[[keys.command]]") == 1
    again = open_sitting.keys_install_text(installed)
    assert again.count('key = "prefix+k"') == 1
    restored = open_sitting.keys_uninstall_text(installed)
    assert "prefix+k" not in restored
    assert "[keys]" in restored
