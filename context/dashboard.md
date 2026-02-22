# TUI Dashboard

## Overview

`bin/dashboard.py` launches a Textual TUI (`TaktApp`)
with tabbed layout: Dashboard, Trigger, Settings, plus dynamic
agent tabs for inline Claude agents.

## Layout

```
+------------------------------------------------------------+
| Header: takt                  [clock]        |
+------------------------------------------------------------+
| [Dashboard] [Agents] [Trigger] [Settings] [ws/test ✓]     |
+------------------------------------------------------------+
|   (active tab content fills remaining space)                |
+------------------------------------------------------------+
| Footer: [r]efresh [n]ew ws [c]laim [x]release [^w]close   |
+------------------------------------------------------------+
```

### Dashboard tab

Existing 6-panel grid (agents, workspaces, pipeline grid,
pipeline events, targets, PRs). Extracted into
`tui/tabs/dashboard_tab.py`.

### Agents tab

Static tab with all inline agents. Top half: DataTable
listing agent ID, model, state, turns, cost. Bottom half:
RichLog showing selected agent's streaming output. Polls
registry every 2s. Selecting a row switches the viewer.
Output is buffered on each runner so switching agents
replays full history. Implemented in
`tui/tabs/agents_tab.py`.

### Trigger tab

Workflow action buttons + stages table + run history.
Buttons: Trigger Stage, Push to GitHub, New Workspace,
Add Stage. Each opens a modal screen.

### Settings tab

Read-only config display: default model select, repos
table, targets table, poll interval. Model selection
persists to `config/tui_settings.yaml`.

### Agent tabs

Dynamic tabs created when pipeline markers trigger or
manual trigger. Each tab streams Claude agent output
via `claude-code-sdk`. Status bar shows state, model,
cost, turns. Tab title gets icon on completion (✓/✗).

## File Structure

```
bin/dashboard.py              Entry point
tui/
  app.py                      TaktApp — TabbedContent
  dashboard.tcss              Global CSS
  screens.py                  Modal screens
  tabs/
    __init__.py
    dashboard_tab.py           Grid of 6 monitoring panels
    agents_tab.py              Agent list + output viewer
    trigger_tab.py             Workflow action buttons
    settings_tab.py            Config display
    agent_tab.py               Streaming agent output
  widgets/
    agents.py                  AgentsPanel
    workspaces.py              WorkspacesPanel
    pipeline_grid.py           PipelineGridPanel (arrow flow)
    pipeline.py                PipelinePanel (events + inline dispatch)
    targets.py                 TargetsPanel
    prs.py                     PrsPanel
    agent_output.py            SDK message renderer
    style_utils.py             Shared age-bucket styling
lib/
  agent_runner.py              SDK wrapper (AgentRunner)
  agent_registry.py            Global agent tracker
config/
  tui_settings.yaml            Persisted TUI settings
```

## Agent Execution

`lib/agent_runner.py`: AgentRunner wraps `claude-code-sdk`
`query()`. Each agent is one async query call with
`bypassPermissions`. AgentInfo tracks state, cost, turns.

`lib/agent_registry.py`: Module-level dict `{id: AgentRunner}`.
Functions: register/unregister/get/is_running/list_active.

`tui/widgets/agent_output.py`: Converts SDK messages
(TextBlock, ToolUseBlock, ToolResultBlock, ThinkingBlock,
ResultMessage) to Rich Text for RichLog display.

## Pipeline Integration

`tui/widgets/pipeline.py` scans for `.pipeline-push` and
`.upstream-sync` markers and launches agents inline via
`app.launch_agent()` instead of kitty tabs.

`bin/pipeline_watch.py` is unchanged — still works standalone
with kitty as fallback when TUI is not running.

## Polling Architecture

All polling uses `@work(thread=True)` workers since git and
file operations are blocking. Workers post results back to
the main thread via `app.call_from_thread()`.

Agent streaming uses `@work(thread=False)` (async, SDK
iterators). Multiple agents run in parallel via separate
OS subprocesses.

| Panel      | Interval | Data source                        |
|------------|----------|------------------------------------|
| Agents     | 5s       | `session_parser.discover_sessions` |
| Workspaces | 10s      | `workspace_ops.list_workspaces`    |
| Stages     | 10s      | `workspace_ops.list_stages`        |
| Targets    | 10s      | `target_ops.get_all_targets`       |
| Pipeline   | 10s      | marker scanning + agent registry   |
| PRs        | 60s      | `pr_ops.list_all_prs`              |

## Keybindings

| Key      | Action                              |
|----------|-------------------------------------|
| `n`      | Open create workspace modal         |
| `c`      | Claim selected target (opens modal) |
| `x`      | Release selected target (confirm)   |
| `r`      | Refresh all panels                  |
| `ctrl+w` | Close active agent tab              |
| `q`      | Quit                                |

## Modal Screens

- **CreateWorkspaceScreen**: Input for name + SelectionList
  of repos from repos.yaml.
- **ClaimTargetScreen**: Input for workspace name.
- **ConfirmScreen**: Reusable yes/no dialog.
- **TriggerStageScreen**: Select workspace + role, writes
  `.pipeline-push` markers, launches inline agent.
- **PushGithubScreen**: Select workspace, runs
  push_to_github.py.
- **AddStageScreen**: Select workspace + role, calls
  create_stage().

## Dependencies

- `textual>=1.0.0` (includes Rich)
- `claude-code-sdk>=0.0.25`
- PyYAML (already in requirements)
