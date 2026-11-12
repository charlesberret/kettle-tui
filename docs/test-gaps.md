# Test gap analysis (kettle-tui)

Sprint pass: map coverage by module, name the riskiest untested behaviour, and add
tests for the worst gaps without changing product code.

## Coverage command

From the repo root, with dev dependencies installed:

```sh
python3 -m pip install -e '.[dev]' pytest-cov
python3 -m pytest --cov=kettle_tui --cov-report=term-missing -q
```

Last run on this branch (2026-11-12, Linux, Python 3.12):

```
Name                         Stmts   Miss  Cover   Missing
----------------------------------------------------------
src/kettle_tui/__init__.py       1      0   100%
src/kettle_tui/__main__.py      71     15    79%   …
src/kettle_tui/app.py         1783    644    64%   …
src/kettle_tui/client.py       607    145    76%   …
src/kettle_tui/demo.py          22      0   100%
src/kettle_tui/uicopy.py        52     11    79%   …
----------------------------------------------------------
TOTAL                         2536    815    68%
121 passed, 1 skipped in ~28s
```

The lone skip is `tests/test_plain_copy.py::test_fallback_literals_equal_core_catalog`
when no `kettle-core` catalog is on disk (`KETTLE_UI_COPY` unset).

### Per-module summary

| Module | Role | Stmt cover | Test files that exercise it |
|--------|------|------------|-----------------------------|
| `kettle_tui/__init__.py` | version export | 100% | implicit import |
| `kettle_tui/__main__.py` | CLI (`--demo`, `--check`, `--here`) | 79% | `tests/test_gap_fill.py` |
| `kettle_tui/app.py` | Textual UI, modals, stream handlers | 64% | `tests/test_plain_copy.py`, `tests/test_tui_pilot.py` |
| `kettle_tui/client.py` | discovery, HTTP client, queue/phase helpers | 76% | `tests/test_client_logic.py`, `tests/test_client_reachable.py`, `tests/test_demo_client.py`, `tests/test_here.py`, `tests/test_plain_copy.py`, `tests/test_gap_fill.py` |
| `kettle_tui/demo.py` | offline sample desk | 100% | `tests/test_demo_client.py`, `tests/test_gap_fill.py` |
| `kettle_tui/uicopy.py` | copy catalog resolution | 79% | `tests/test_plain_copy.py` |

Adjacent (not in the `kettle_tui` package but covered in CI):

| Area | Test file |
|------|-----------|
| Herdr editor plugin (`herdr/`) | `tests/test_herdr_open.py` |
| Plugin manifest | `tests/test_plugin_manifest.py` |

## Ten riskiest untested behaviours

Ordered by user impact if the behaviour regressed silently.

1. **Live HTTP error surfaces (`client._request_raw` / `_post_ndjson`)** — Non-JSON bodies, auth retry exhaustion, and NDJSON parse fallbacks are only partially covered; a wrong retry could loop or drop stream chunks.
2. **`KettleClient.sweep_health` dual path** — Reads `.kettle/runtime/sweep-health.json` then legacy `.setsweep/…`; untested live fallback and corrupt JSON short-circuit.
3. **`ensure_reachable` spawn failure** — `subprocess.Popen` `OSError` returns False without a test; user sees a dead TUI with no auto-launch.
4. **`app.py` stream / founding state machine** — Large branches for interview → propose → found → sweep NDJSON handling; pilot tests cover happy paths only, not cancel/error mid-stream.
5. **`app.py` project picker / register desk validation** — Errors from `add_project` and missing-folder recovery are thinly exercised compared to the modal code volume.
6. **`client.interview_status` live mode** — Demo mapping is well tested; live `GET /api/interview` field combinations (legacy cores) are not.
7. **`client.file_text` / `save_file` live round-trip** — Missing `content` in JSON and 404 vs hard errors are untested outside demo stubs.
8. **`uicopy.in_process_payload`** — Import failures and missing `kettle` package paths fall back silently; only the “returns None” monkeypatch path is tested.
9. **`__main__._check` against a live core** — Demo `--check` is now tested; reachable spawn + queue line for real HTTP is not (would need the hermetic server fixture extended).
10. **`_process_alive` permission / invalid pid edge cases** — Stale sweep uses a monkeypatched False; PermissionError → True and invalid pid → None are untested.

## Gap-filling tests added (this pass)

`tests/test_gap_fill.py` (14 tests) targets gaps **2’s demo cousin**, **8’s demo paths**, **discovery**, **entry/proposal helpers**, **add_project validation**, and **CLI `--check`**:

- `load_discovery` — flag, discovery file, and default fallback
- `entry_status` / `is_proposal_entry` / `is_set_aside` / `resolve_project_root`
- `demo.file_content` basename and unknown-path stub
- `add_project` requires `root`; demo slug deduplication (`my_app` / `my_app_2`)
- `main(["--demo", "--check"])` and `--here` miss exit code; `build_parser` `--here` const

Run the new file alone:

```sh
python3 -m pytest tests/test_gap_fill.py -q
```

## Suggested next tests (not in this PR)

- Extend the in-process HTTP fixture to cover `sweep_health` path order and invalid JSON.
- One pilot test that aborts a sweep stream mid-NDJSON and asserts UI unlock.
- Hermetic `__main__._check` with the existing ping handler + projects list endpoint.
