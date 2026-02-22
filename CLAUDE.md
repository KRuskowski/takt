# General
- you have full sudo access on this machine
- sign you commits
- use google style guide, 80 character limit, 2 space indends
- if you write scripts use python
- keep CLAUDE.md concise — put details in `context/` files
  and reference them by filepath from here

# takt Tools

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

# Delete workspace + all pipeline stages
bin/workspace.py delete feature-auth --cascade

# Add a pipeline stage (role from pipeline_roles.md)
bin/workspace.py stage-add feature-auth test
bin/workspace.py stage-add feature-auth review

# Remove a stage (-f to skip confirmation)
bin/workspace.py stage-remove feature-auth test

# List all stages (or filter by workspace)
bin/workspace.py stage-list
bin/workspace.py stage-list feature-auth

# Refresh stage CLAUDE.md and hooks
bin/workspace.py stage-refresh feature-auth test
bin/workspace.py stage-refresh --all

# Show pipeline chain for a workspace
bin/workspace.py pipeline feature-auth

# Show pipeline run history
bin/workspace.py runs feature-auth
```

## takt-service (`bin/takt_service.py`)

Persistent background service for pipeline watching and agent
execution. Agents survive TUI disconnects; output is persisted
and replayed on reconnect. Uses ZMQ for IPC (ROUTER/DEALER
for commands, PUB/SUB for broadcasts).
Details: `context/pipeline-service.md`

```bash
# Start the service
systemctl --user start takt-service

# View logs
journalctl --user -u takt-service -f

# Single poll cycle (no service needed)
bin/pipeline_watch.py --once

# Clear stored state
bin/pipeline_watch.py --reset
```

## Pipeline Watcher (`bin/pipeline_watch.py`)

Reusable functions for branch change detection, marker
scanning, and prompt building. Used by takt-service for
polling. The `--once` flag runs a direct poll cycle.

```bash
# Single poll cycle
bin/pipeline_watch.py --once

# Clear stored state
bin/pipeline_watch.py --reset
```

## Target Management (`bin/target.py`)

Manage build/test targets (VMs and hardware) with exclusive
locking. Templates (deb-01, win-01) are read-only — use
`bin/clone_vm.py` to create clones.

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

Create/delete qcow2-backed clones from templates. Requires
sudo. Details: `context/vm-templates.md`

```bash
# Create a clone
sudo python3 bin/clone_vm.py create deb-01 deb-02 \
  --ip 10.101.0.100

# Delete a clone
sudo python3 bin/clone_vm.py delete deb-02
```

## Dashboard (`bin/takt.py`)

Tabbed TUI: Dashboard (monitoring panels), Trigger
(workflow actions), Settings, plus dynamic agent tabs.
Connects to takt-service for agent execution and pipeline
events. Falls back to local execution without service.
Details: `context/dashboard.md`

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
- **Workspaces**: `~/dev/workspaces/<name>/` — clones of root
  repos for isolated work. Origin = root repo.
- **Stages**: `~/dev/stages/<workspace>/<role>/` — pipeline
  stages. Any role from `templates/pipeline_roles.md` can be
  a stage. Remote chain: workspace -> stage1 -> stage2 -> root.
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

# Setup

New workstation setup: `context/workstation-setup.md`
