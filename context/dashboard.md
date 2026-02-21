# TUI Dashboard

## Overview

`bin/dashboard.py` launches a Textual TUI that monitors the
orchestration system in real time. It polls workspaces, Claude
agent sessions, and targets, displaying everything in a grid
layout with agents prominent on top.

## Layout

```
+------------------------------------------------------------+
| Header: Agent Orchestration Dashboard        [clock]        |
+------------------------------------------------------------+
|   Agents (3 active, 12 hidden)                              |
|   Slug       Branch       Model  Status  Context   Tokens   |
|   my-task    feat-auth    opus   active  142K/200K  1.2M    |
|   ...                                                       |
+----------------------------+-------------------------------+
|   Workspaces               |   Stages                      |
|   Name  Repos  Branch  St  |   Name  Type  Repos  Branch   |
|   ...                      |   ...                         |
+----------------------------+-------------------------------+
|   Targets                                                   |
|   Name  Type  Host  Claimed By                              |
|   ...                                                       |
+------------------------------------------------------------+
| Footer: [n]ew ws  [c]laim  [x]release  [r]efresh  [q]uit   |
+------------------------------------------------------------+
```

Top row: agents panel spanning full width (most prominent).
Middle row: workspaces (left) + stages (right).
Bottom row: targets (full width).

## File Structure

```
bin/dashboard.py           Entry point (~20 lines)
tui/
  app.py                   DashboardApp — compose, bindings, polling
  dashboard.tcss           Textual CSS for grid layout
  screens.py               Modal screens (create ws, claim target, confirm)
  widgets/
    agents.py              AgentsPanel (DataTable, filters stale)
    workspaces.py          WorkspacesPanel (DataTable)
    stages.py              StagesPanel (testing + utility, DataTable)
    targets.py             TargetsPanel (DataTable)
```

## Shared Lib Modules

### `lib/session_parser.py`

Parses Claude Code JSONL session files under
`~/.claude/projects/`. Key concepts:

- **Quick mode**: For large files (>500KB), reads only the
  first 50 lines (metadata) + last 64KB (recent usage).
- **SessionInfo dataclass**: session_id, cwd, git_branch,
  slug, model, timestamps, token counts, estimated cost,
  context_tokens, context_limit.
- **Context window**: Tracks the last assistant message's
  total input tokens (input + cache_read + cache_create)
  as `context_tokens`. `context_limit` is set from a
  model lookup table (200K for all current models).
- **Cost calculation**: Maps model IDs to per-1M-token rates.

Functions:
- `discover_sessions(active_threshold_s=120)` — glob + parse
- `parse_session_file(path, quick=True)` — single file parse
- `calculate_cost(model, tokens...)` — token-to-USD
- `load_stats_cache()` — aggregated stats

### `lib/workspace_ops.py`

Extracted from `bin/workspace.py`. Pure functions, no argparse.

Functions:
- `list_workspaces()` — returns list of dicts
- `get_workspace_status(name)` — per-repo branch + status
- `create_workspace(name, repos)` — clone + branch + template
- `delete_workspace(name)` — rmtree
- `list_testing_stages()` — returns list of dicts
- `list_utility_stages()` — returns list of dicts
- `create_testing_stage(name)` — clone + branch + template
- `create_utility_stage(name)` — clone + branch + template
- `delete_testing_stage(name)` — rmtree + restore origins
- `delete_utility_stage(name)` — rmtree + restore origins

### `lib/target_ops.py`

Extracted from `bin/target.py`. Lock file management.

Functions:
- `get_lock_path(name)`, `read_lock(name)`, `write_lock()`
- `release_lock(name)` — remove lock, return old data
- `get_target(name)` — single target config
- `get_all_targets()` — all targets with lock status

## Polling Architecture

All polling uses `@work(thread=True)` workers since git and
file operations are blocking. Workers post results back to
the main thread via `app.call_from_thread()`.

| Panel      | Interval | Data source                        |
|------------|----------|------------------------------------|
| Agents     | 5s       | `session_parser.discover_sessions` |
| Workspaces | 10s      | `workspace_ops.list_workspaces`    |
| Stages     | 10s      | `workspace_ops.list_*_stages`      |
| Targets    | 10s      | `target_ops.get_all_targets`       |

## Agent Filtering

The agents panel only shows sessions that are:
- **Active**: file modified within last 2 minutes
- **Recent**: last activity timestamp within 30 minutes

Idle sessions (>30 min) are hidden. The panel title shows
counts: "Agents (3 active, 12 hidden)".

## Context Window Column

Each agent row shows context window utilization from the
last assistant message's token counts vs the model's known
limit (e.g. "142K/200K").

## Keybindings

| Key | Action                              |
|-----|-------------------------------------|
| `n` | Open create workspace modal         |
| `c` | Claim selected target (opens modal) |
| `x` | Release selected target (confirm)   |
| `r` | Refresh all panels                  |
| `q` | Quit                                |

## Modal Screens

- **CreateWorkspaceScreen**: Input for name + SelectionList
  of repos from repos.yaml. Creates workspace in worker thread.
- **ClaimTargetScreen**: Input for workspace name. Writes lock
  file in worker thread.
- **ConfirmScreen**: Reusable yes/no dialog. Used for release
  confirmation.

## Dependencies

- `textual>=1.0.0` (includes Rich)
- All other deps are stdlib + PyYAML (already in requirements)
