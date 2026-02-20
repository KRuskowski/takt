# General
- you have full sudo access on this machine
- sign you commits
- use google style guide, 80 character limit, 2 space indends
- if you write scripts use python

# Agent Orchestration Tools

## Workspace Management (`bin/workspace.py`)

Create isolated workspaces with local clones for multi-repo tasks.
Workspace name = branch name across all repos.

```bash
# Create a workspace (clones repos, creates branch, generates
# CLAUDE.md)
bin/workspace.py create feature-auth Combatant Conveyor config

# List all workspaces
bin/workspace.py list

# Show repo status in a workspace
bin/workspace.py status feature-auth

# Delete a workspace (-f to skip confirmation)
bin/workspace.py delete feature-auth
```

## Pipeline Watcher (`bin/pipeline_watch.py`)

Polls root repos for branch changes, gathers diffs/logs, pipes
to Claude CLI for analysis.

```bash
# Start watching (polls every 30s)
bin/pipeline_watch.py

# Single poll cycle
bin/pipeline_watch.py --once

# Custom interval
bin/pipeline_watch.py --interval 60

# Clear stored state
bin/pipeline_watch.py --reset
```

## Target Management (`bin/target.py`)

Manage build/test targets (VMs and hardware) with exclusive
locking.

```bash
# List targets
bin/target.py list

# Claim/release for a workspace
bin/target.py claim win-01 feature-auth
bin/target.py release win-01

# VM lifecycle (stubs if virsh not installed)
bin/target.py up win-01
bin/target.py down win-01

# Run command via SSH
bin/target.py run win-01 "cmake --build ."

# Show target details + connectivity
bin/target.py status win-01
```

## Dashboard (`bin/dashboard.py`)

TUI dashboard for monitoring workspaces, agents, targets,
and usage. Built with Textual.

```bash
# Launch the dashboard
bin/dashboard.py
```

**Keybindings:**
- `n` — create new workspace
- `c` — claim selected target
- `x` — release selected target
- `r` — refresh all panels
- `q` — quit

**Layout:** 3-column grid — left (workspaces + targets),
center (agents + usage), right (detail pane). Select any row
to populate the detail pane.

**Lib modules used by the dashboard:**
- `lib/session_parser.py` — JSONL parsing + cost calculation
- `lib/workspace_ops.py` — workspace CRUD operations
- `lib/target_ops.py` — target lock operations

## Push to GitHub (`bin/push_to_github.py`)

Push branches from root repos to GitHub in dependency order.

```bash
# Push a branch (prompts for confirmation)
bin/push_to_github.py feature-auth

# Dry run
bin/push_to_github.py feature-auth --dry-run

# Limit to specific repos
bin/push_to_github.py feature-auth --repos Combatant Conveyor
```

# Key Concepts

- **Root repos**: `~/dev/<repo>` — local mirrors of GitHub.
- **Workspaces**: `~/dev/workspaces/<name>/` — clones of root
  repos for isolated work. Origin = root repo.
- **Workspace name = branch name** across all repos.
- **Agents never push to GitHub.** Push to origin (root repo)
  only. Operator uses `push_to_github.py` for GitHub.
- **Session state** lives at the bottom of workspace CLAUDE.md.
  Update it before ending a session.

# Config Files

- `config/repos.yaml` — managed repo registry with push order
- `config/targets.yaml` — target inventory (VMs + hardware)
- `templates/` — CLAUDE.md templates and role snippets
- `context/` — architecture and decision docs
