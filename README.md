# kettle-tui

A terminal viewport for [Kettle](https://github.com/charlesberret/kettle), the local-first review desk for work done by coding and writing agents. It shows the same queue as the web, VS Code and desktop viewports, in a terminal, and can open as a [herdr](https://github.com/herdrdev/herdr) tab.

```text
Queue  ·  Editor  ·  Agent (summary → Approve · Comment · Not this · Finish batch → stream → composer)
```

## Status

**Experimental.** This is version 0.0.1 and began as a spike. It works against Kettle core and is tested from source. It is planned to be folded into [`kettle-core`](https://github.com/charlesberret/kettle-core), after which this repository will be archived; until then a bare `kettle` command starts it when both packages are installed. Product structure is in [`kettle-core/docs/UI-STRUCTURE.md`](https://github.com/charlesberret/kettle-core/blob/main/docs/UI-STRUCTURE.md).

The words on screen come from kettle-core's copy catalog, `GET /api/ui-copy`. A label changed there, or relabelled in the web app's UI debug mode, shows up here too. With no core, or one too old to serve the catalog, the TUI uses the same words built in.

## Run

Needs Python 3.11 or newer. Install [kettle-core](https://github.com/charlesberret/kettle-core#try-it) first and start its server, or use demo mode, which needs no core. With both this package and `kettle-core` installed, bare `kettle` starts the TUI; `kettle-web` is the HTTP host.

```sh
git clone https://github.com/charlesberret/kettle-tui && cd kettle-tui
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/kettle-tui --here             # the project for this directory
.venv/bin/kettle-tui --project my-project   # live, named project
.venv/bin/kettle-tui --demo             # offline
.venv/bin/kettle-tui --check --here     # no TUI; exit 1 if this directory has no project
```

`--here` opens the registered project whose `root` is this directory, or an ancestor. It does not fall back to some other project.

## Herdr

Kettle belongs in a **tab** on the directory you are already working in. It is a viewport onto your review desk, not a launcher for agents. Needs herdr 0.9.0 or newer. From the `kettle-tui` clone:

```sh
herdr plugin link "$PWD"
herdr plugin action invoke kettle-tui.install-keys   # prefix+k
herdr server reload-config
```

`prefix+k`, or the action **kettle: project for this directory**, opens a tab running `kettle-tui --here` at the focused pane's cwd. A second press focuses the one already open for that place.

## Keys

| Key | Action |
|-----|--------|
| `j` / `k` | Move queue |
| `Enter` | Open file in editor |
| `a` | Approve. Marks your decision. Nothing changes until you finish the batch |
| `c` | Comment. A note the agent reads when you finish the batch |
| `x` | Not this. Set the file aside. On a file already set aside, Bring back |
| `f` | Finish batch. Apply your decisions and line up the next files |
| `v` | Toggle diff / file |
| `Ctrl+E` | Open in `$VISUAL` / `$EDITOR` |
| `i` | Start the setup interview |
| `d` | Auto-mode. Let the agent write the proposal now |
| `Ctrl+S` | Save |
| `p` | Project |
| `n` | Add project |
| `r` | Refresh |
| `Ctrl+K` | Palette: compact, clear session, set up again, … |
| `Tab` | Cycle focus |
| `q` / `Ctrl+C` | Quit |

**Not this** is one key, with no questions. The file is set aside, sinks to the bottom of the queue with the files you have already decided about, and the agent says what it took from it in the next round notes. It is off while a project is being set up and on the proposal itself. Approve reads the same while a proposal is open. Approving the proposal is what sets the project up.

The composer is the setup interview until the project is set up. After that it is chat. Drag `│` to resize columns.

```sh
.venv/bin/kettle-tui --demo --project new-desk   # a project not set up yet
.venv/bin/kettle-tui --demo --project demo-desk
.venv/bin/kettle-tui --project my-project        # live; may start core
```

## Tests

```sh
.venv/bin/pip install -e '.[dev]' && .venv/bin/python -m pytest -q
```

`tests/test_plain_copy.py` checks the built-in words against core's catalog when `KETTLE_UI_COPY=<kettle-core>/kettle/static/ui-copy.json` is set. The test is skipped otherwise.

## License

MIT. See [LICENSE](LICENSE).
