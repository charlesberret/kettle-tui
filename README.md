# kettle-tui

Plugin-shaped terminal sitting for the Kettle review desk (**Shape B**):

```text
Queue  ·  Editor  ·  Desk (brief → Approve · Comment · Sweep → stream → composer)
```

Product structure: [`kettle-core/docs/UI-STRUCTURE.md`](../kettle-core/docs/UI-STRUCTURE.md)  
Research notes: [`.local/`](.local/)

## Run

Core server should be up (or use demo mode). With both this package and
`kettle-core` installed, bare `kettle` execs the TUI; `kettle-web` is the HTTP host.

```sh
cd ~/cloud/git-projects/kettle-tui
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/kettle-tui --here             # desk for this directory
.venv/bin/kettle-tui --project armada   # live, named desk
.venv/bin/kettle-tui --demo             # offline
.venv/bin/kettle-tui --check --here     # no TUI; exit 1 if this dir has no desk
```

`--here` binds to the registered desk whose `root` is this directory (or an ancestor). It does not fall back to some other project.

## Herdr

A sitting belongs in a **tab** on the directory you are already working in — not a cowbell harness (those raise agents).

```sh
herdr plugin link ~/cloud/git-projects/kettle-tui
herdr plugin action invoke kettle-tui.install-keys   # prefix+k
herdr server reload-config
```

`prefix+k` (or the action **kettle: sitting for this directory**) opens a tab running `kettle-tui --here` at the focused pane's cwd. A second press focuses the existing sitting for that place.

## Keys

| Key | Action |
|-----|--------|
| `j` / `k` | Move queue |
| `Enter` | Open entry in editor |
| `a` | Approve (bookmark only) |
| `c` | Comment for sweep |
| `s` | Sweep |
| `v` | Toggle diff / file |
| `Ctrl+E` | Open in `$VISUAL` / `$EDITOR` |
| `i` | Founding interview |
| `d` | Draft proposal |
| `Ctrl+S` | Save |
| `p` | Project |
| `n` | Register a folder |
| `r` | Refresh |
| `Ctrl+K` | Palette (compact / clear session / refound / …) |
| `Tab` | Cycle focus |
| `q` / `Ctrl+C` | Quit |

Composer: interview while founding, chat once founded. Drag `│` to resize columns.

```sh
.venv/bin/kettle-tui --demo --project new-desk   # founding
.venv/bin/kettle-tui --demo --project demo-desk
.venv/bin/kettle-tui --project armada            # live; may spawn core
```

Details: [`.local/spike-notes.md`](.local/spike-notes.md)
