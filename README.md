# kettle-tui

Plugin-shaped terminal sitting for the Kettle review desk (**Shape B**):

```text
Queue  ·  Editor  ·  Desk (brief → Approve · Comment · Sweep → stream → composer)
```

Product structure: [`kettle-core/docs/UI-STRUCTURE.md`](../kettle-core/docs/UI-STRUCTURE.md)  
Research notes: [`.local/`](.local/)

## Run

Core server should be up (or use demo mode):

```sh
cd ~/cloud/git-projects/kettle-tui
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/kettle-tui --project armada   # live
.venv/bin/kettle-tui --demo             # offline
.venv/bin/kettle-tui --check            # no TUI
```

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
| `r` | Refresh |
| `Ctrl+K` | Palette (compact / clear session / …) |
| `Tab` | Cycle focus |
| `q` / `Ctrl+C` | Quit |

Composer: interview while founding, chat once founded. Drag `│` to resize columns.

```sh
.venv/bin/kettle-tui --demo --project new-desk   # founding
.venv/bin/kettle-tui --demo --project demo-desk
.venv/bin/kettle-tui --project armada            # live; may spawn core
```

Details: [`.local/spike-notes.md`](.local/spike-notes.md)
