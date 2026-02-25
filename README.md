# takt

Multi-agent pipeline orchestration for running Claude Code
agents across multi-repo projects.

## What it does

- Creates isolated **workspaces** (local clones + branches)
  for parallel agent work across multiple repos
- Defines **pipelines** per workspace — ordered sequences
  of agent steps (with per-step model selection) and
  script steps (push, PR creation, upstream merge)
- Runs pipelines via a **background service** that watches
  for branch changes and executes steps in temporary
  worktrees
- Manages **build/test targets** (VMs and hardware) with
  exclusive locking and qcow2-backed VM cloning
- Provides a **TUI dashboard** for monitoring workspaces,
  agents, pipelines, and targets in real time

![takt dashboard](screenshots/takt.png)

## Architecture

```
GitHub (upstream)
  |
  v
~/dev/root/<repo>           Local mirrors (fetch-only)
  |
  v
~/dev/workspaces/<name>/    Isolated clones, one per task
  |
  v
~/dev/runs/<ws>-<id>/<repo> Temporary worktrees per run
```

### Data flow

1. **Pull**: GitHub -> root repos (fetch via service or
   manual `git pull`)
2. **Clone**: root repos -> workspace clones
   (`workspace.py create`)
3. **Work**: agent modifies workspace clones, pushes to
   root repo (its origin)
4. **Watch**: takt-service detects branch changes in root
   repos, creates pipeline runs
5. **Execute**: pipeline steps run sequentially in
   temporary worktrees — agents via Claude Code SDK,
   scripts via Python functions
6. **Push**: operator reviews and pushes from root repos
   to GitHub (`push_to_github.py`)

### Design principles

- **Workspace name = branch name** across all repos. One
  identifier ties together repos, tools, and git history.
- **No direct GitHub push.** Agents push to origin (root
  repo) only. Human operator gates what reaches GitHub.
- **All state in SQLite.** Pipeline definitions, runs,
  steps, agent output, and branch refs live in
  `.state/takt.db` (WAL mode).
- **Per-step model selection.** Each agent step can run
  with a different Claude model (sonnet/opus/haiku).
- **Progressive context disclosure.** CLAUDE.md files are
  lean and point to context packets. Agents fetch what
  they need rather than loading everything upfront.
- **Pooled targets.** Build/test targets (VMs, hardware)
  are shared resources with exclusive locking. Agents
  claim, use, and release.

## Tools

| Tool | Purpose |
|------|---------|
| `bin/workspace.py` | Create/delete workspaces, define pipelines |
| `bin/takt_service.py` | Background service for pipeline watching + execution |
| `bin/takt.py` | Textual TUI (connects to takt-service) |
| `bin/pipeline_watch.py` | Standalone poll for branch changes |
| `bin/target.py` | Claim/release targets, VM lifecycle, SSH |
| `bin/clone_vm.py` | Create/delete qcow2-backed VM clones |
| `bin/push_to_github.py` | Push branches from root repos to GitHub |
| `bin/setup_win_vm.py` | Create Windows 11 VM (unattended) |
| `bin/provision_win_vm.py` | Provision Windows VM (VS2022, Git, Samba) |

## Quick start

```bash
# Create a workspace across multiple repos
bin/workspace.py create feature-auth RepoA RepoB

# Define a pipeline
bin/workspace.py pipeline-set feature-auth test push_to_github

# Start the background service
systemctl --user start takt-service

# Launch the TUI
bin/takt.py

# Or trigger a run manually
bin/workspace.py trigger feature-auth

# Push to GitHub when ready
bin/push_to_github.py feature-auth
```

## Pipeline

Pipelines are ordered sequences of steps defined per
workspace. Steps are either **agent** (Claude Code via
SDK) or **script** (built-in Python functions).

Built-in scripts:
- `push_to_github` — push branch to GitHub in dependency
  order
- `create_pr` — create GitHub PRs via `gh` CLI
- `merge_upstream` — fetch and merge default branch into
  workspace branch

Agent steps run with a configurable model
(sonnet/opus/haiku) and get a role prompt from
`templates/pipeline_roles.md`. Results are written to
`.stage-result.json` in the worktree.

## Build targets

Targets are VMs and hardware registered in
`config/targets.yaml`. Template VMs are read-only base
images — agents work on clones.

Clones use qcow2 backing files (fast to create,
space-efficient — only store diffs from the template).
VMs use KVM with `-cpu host` for near-native performance
and mount `~/dev` via Samba for zero-copy source access.

```bash
# Create a clone from a template
sudo python3 bin/clone_vm.py create deb-01 deb-02 \
  --ip 10.101.0.100

# Manage targets
bin/target.py list
bin/target.py claim deb-02 feature-auth
bin/target.py run deb-02 "cmake --build ."
```

## Project layout

```
bin/                  CLI tools
lib/                  Shared library modules
  config.py           Constants, config loaders
  db.py               SQLite state layer
  pipeline.py         Pipeline executor
  service.py          Background service (ZMQ + asyncio)
  agent_runner.py     Claude Code SDK wrapper
  workspace_ops.py    Workspace operations
  git_utils.py        Git helpers
tui/                  Dashboard TUI (Textual)
  tabs/               Tab implementations
  widgets/            Reusable panel widgets
config/
  repos.yaml          Managed repo registry (gitignored)
  targets.yaml        Target inventory (gitignored)
templates/            CLAUDE.md templates, pipeline roles
context/              Architecture and decision docs
tests/                Unit tests
```

## Requirements

- Python 3.11+
- PyYAML, pyzmq, textual, claude-code-sdk
- Git
- libvirt + QEMU (for VM management, optional)
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code)

## License

MIT — see [LICENSE](LICENSE).
