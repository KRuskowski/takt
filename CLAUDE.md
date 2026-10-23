# General
- you have full sudo access on this machine
- sign you commits
- use google style guide, 80 character limit, 2 space indends
- if you write scripts use python
- keep CLAUDE.md concise — put details in `context/` files and reference them by filepath from here
- NEVER run interactive auth (`gh auth login`, `ssh-add`, etc). If credentials are missing, report the blocker and stop. The operator handles authentication.

# takt Tools

## Workspace Management (`bin/workspace.py`)

Create isolated workspaces with local clones for multi-repo tasks. Workspace name = branch name across all repos.

```bash
# Create a workspace
bin/workspace.py create feature-auth Combatant Conveyor config

# Create from a specific base branch
bin/workspace.py create feature-auth Combatant --base release/v11.x

# List all workspaces
bin/workspace.py list

# Show repo status in a workspace
bin/workspace.py status feature-auth

# Delete a workspace (-f to skip confirmation)
bin/workspace.py delete feature-auth

# Define pipeline steps (roles or scripts)
bin/workspace.py pipeline-set feature-auth test push_to_github

# Show configured pipeline
bin/workspace.py pipeline-show feature-auth

# Show pipeline run history
bin/workspace.py runs feature-auth
```

## takt-service (`bin/takt_service.py`)

Persistent background service for pipeline watching and agent execution. All state in SQLite (`.state/takt.db`). Uses ZMQ for IPC (ROUTER/DEALER for commands, PUB/SUB for broadcasts). REST API on port 7433.

```bash
# Start/stop/logs
systemctl --user start takt-service
systemctl --user status takt-service
journalctl --user -u takt-service -f
```

## MCP Server (`bin/takt_mcp.py`)

FastMCP server wrapping the takt REST API. Installed at user scope via `claude mcp add -s user takt <venv-python> bin/takt_mcp.py` (stored in `~/.claude.json`) — available to all Claude sessions in every project, no per-project approval. Exposes workspace, target, pipeline, run, template, context, repo, agent, and account tools. This is the primary interface to takt.

## Context Engine (`lib/context_store.py`)

Retrieval over takt's knowledge: the markdown in `context/` and `templates/`, plus facts agents record as they work. SQLite FTS5 in `.state/takt.db`, BM25-ranked, returning **sections** (with heading path and line range) rather than whole files. Docs are scoped `global`/`repo`/`workspace`/`target`; facts carry the run and workspace that produced them. Details: `context/context-engine.md`

MCP tools: `search`, `record_fact`, `list_facts`, `supersede_fact`, `context_stats`, `reindex_context`.

The index self-maintains — reindexed at service startup and on every edit through the API. `reindex_context` is only needed after editing `context/` or `templates/` directly on disk.

## Pipeline Watcher (`bin/pipeline_watch.py`)

Thin CLI for branch change detection using `lib/pipeline.py` and `lib/db.py`.

```bash
# Single poll cycle
bin/pipeline_watch.py --once

# Clear stored refs
bin/pipeline_watch.py --reset
```

## Target Management (`bin/target.py`)

Manage build/test targets (VMs and hardware) with exclusive locking. Templates (deb-01, win-01) are read-only — use `bin/clone_vm.py` to create clones.

```bash
# List targets (shows [template] tag)
bin/target.py list

# Claim/release for a workspace
bin/target.py claim deb-02 feature-auth
bin/target.py release deb-02

# VM lifecycle (stubs if virsh not installed)
bin/target.py up deb-02
bin/target.py down deb-02

# Run command via SSH
bin/target.py run deb-02 "cmake --build ."

# Show target details + connectivity
bin/target.py status deb-02
```

## VM Cloning (`bin/clone_vm.py`)

Create/delete qcow2-backed clones from templates. Requires sudo. Details: `context/vm-templates.md`

```bash
# Create a clone
sudo python3 bin/clone_vm.py create deb-01 deb-02 \
  --ip 10.101.0.100

# Delete a clone
sudo python3 bin/clone_vm.py delete deb-02
```

## Unified CLI (`bin/takt.py`)

Argparse CLI over `lib/commands.py`: `takt ws|target|pipeline|push|service`. Sourcing `config/takt.zsh` from `~/.zshrc` provides the `takt` shell function with completions.

takt has no graphical or terminal UI. Interaction is via the MCP server, the REST API on port 7433, and this CLI.

## Reclaim Build Output (`bin/reclaim.py`)

Workspace clones are hardlinked and nearly free; the build trees inside them dominate storage and nothing else reclaims them. Dry run by default.

```bash
bin/reclaim.py                              # survey only
bin/reclaim.py --older-than 30              # idle 30+ days
bin/reclaim.py --older-than 30 --apply      # delete
```

Only removes directories carrying a marker that proves they are regenerable (`CMakeCache.txt`, `CACHEDIR.TAG`, `node_modules`); never descends into `.git`; re-checks the marker before deleting; spares workspaces with a run in flight.

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

- **Root repos**: `~/dev/root/<repo>` — local mirrors of GitHub.
- **Workspaces**: `~/dev/workspaces/<name>/` — clones of root repos for isolated work. Origin = root repo.
- **Pipeline**: Ordered sequence of steps (agents or scripts) defined in SQLite per workspace. Steps run sequentially in temporary worktrees from root repos.
- **Runs**: `~/dev/runs/<ws>-<run_id>/<repo>` — git worktrees created for each pipeline run.
- **Workspace name = branch name** across all repos.
- **Agents never push to GitHub.** takt handles all pipeline orchestration, pushes, and PR creation.
- **State**: All pipeline state in `.state/takt.db` (SQLite, WAL mode).
- **Context engine**: Scoped, searchable knowledge base in SQLite FTS5. Serves sections of the checked-in docs *and* facts agents record during runs, so knowledge accumulates instead of dying with each session. See `context/context-engine.md`.
- **Issue ledger**: Tracks which issue is assigned to which workspace. Agents claim issues atomically before starting work, so parallel planning agents don't duplicate effort. Status lifecycle: `open` → `assigned` → `in_progress` → `review` → `merged` → `closed`. Available via REST API (`/api/issues/*`) and MCP tools (`assign_issue`, `unassign_issue`, `update_issue_status`, `list_issues`).

# Compute

takt is single-host: one service owns the state, the worktrees and the executor, and all steps run locally on it. `max_agents` throttles contention, it does not add capacity. Targets (`bin/target.py run`) are the only offload path today and are deliberately not wired into the pipeline executor.

Design for distributing across machines (scheduler node + pull-based workers + thin clients): `context/distributed-execution.md`. Not implemented.

# Config Files

- `config/repos.yaml` — managed repo registry with push order
- `config/targets.yaml` — target inventory (VMs + hardware)
- `templates/` — CLAUDE.md templates and role snippets
- `context/` — architecture and decision docs

# Building Repos

Build instructions: `context/building-repos.md`

# Setup

New workstation setup: `context/workstation-setup.md`
