"""Herdr plugin packaging checks (LAB-273)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_pane_uses_plugin_root_not_relative_script():
    text = (ROOT / "herdr-plugin.toml").read_text(encoding="utf-8")
    assert "HERDR_PLUGIN_ROOT" in text
    assert 'exec \\"$HERDR_PLUGIN_ROOT/herdr/run-sitting.sh\\"' in text
    # Relative herdr/run-sitting.sh as the pane argv would look in the desk cwd.
    assert 'command = ["bash", "herdr/run-sitting.sh"]' not in text


def test_open_and_run_scripts_exist_and_are_executable():
    for rel in ("herdr/open.sh", "herdr/run-sitting.sh"):
        path = ROOT / rel
        assert path.is_file(), rel
        assert path.stat().st_mode & 0o111, f"{rel} must be executable"
