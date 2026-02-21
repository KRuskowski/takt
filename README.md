# Agent Orchestration

Multi-agent pipeline orchestration for running Claude CLI
agents in parallel across multi-repo projects.

## What it does

- Creates isolated **workspaces** (local clones + branches)
  for parallel agent work across multiple repos
- Chains **pipeline stages** (feature, test, review, docs)
  with automatic handoff via branch watching
- Manages **build/test targets** (VMs and hardware) with
  exclusive locking and VM cloning from templates
- Provides a **TUI dashboard** for monitoring workspaces,
  agents, targets, and usage in real time

## Architecture

```
GitHub (upstream)
  |
  v
~/dev/root/<repo>           Local mirrors (never touched by agents)
  |
  v
~/dev/workspaces/<name>/    Isolated clones, one per task
  |
  v
~/dev/stages/<ws>/<role>/   Pipeline stages (test, review, etc.)
```

### Data flow

1. **Pull**: GitHub -> root repos (manual `git pull`)
2. **Clone**: root repos -> workspace clones
   (`workspace.py create`)
3. **Work**: agent modifies workspace clones, pushes to
   root repo (its origin)
4. **Watch**: `pipeline_watch.py` detects branch changes in
   root repos, triggers next pipeline stage
5. **Push**: operator reviews and pushes from root repos to
   GitHub (`push_to_github.py`)

### Design principles

- **Workspace name = branch name** across all repos. One
  identifier ties together repos, tools, and git history.
- **No direct GitHub push.** Agents push to origin (root
  repo) only. Human operator gates what reaches GitHub.
- **Session state in CLAUDE.md.** Agent progress persists at
  the bottom of the workspace CLAUDE.md -- task + progress
  in one read, no separate state files.
- **Progressive context disclosure.** CLAUDE.md files are
  lean (<150 lines) and point to context packets. Agents
  fetch what they need rather than loading everything
  upfront.
- **Pooled targets.** Build/test targets (VMs, hardware)
  are shared resources with file-based locks for exclusive
  access. Agents claim, use, and release.
- **Shared folders eliminate file transfer.** Host exports
  `~/dev` via Samba; VMs build directly from the share.

### Agent context layering

Three layers of context, each with a different purpose:

1. **Root repo CLAUDE.md** -- project-level truth:
   architecture, conventions, build commands. One per repo,
   doesn't change per task.
2. **Workspace CLAUDE.md** -- task-level context: role, task
   description, acceptance criteria, session state. Generated
   per workspace from templates.
3. **Context packets** -- focused docs in `context/`
   directories (architecture, decisions, environment). Agents
   self-select which packets to read based on their role.

### Pipeline stages

```
workspace -> feature -> test -> review -> docs -> root
```

Each stage is a clone with a role-specific CLAUDE.md. Roles
are defined in `templates/pipeline_roles.md`. Not every
change needs all stages. The remote chain is automatically
maintained: adding or removing a stage re-links the git
remotes.

## Tools

| Tool | Purpose |
|------|---------|
| `bin/workspace.py` | Create/delete workspaces, manage pipeline stages |
| `bin/pipeline_watch.py` | Poll for branch changes, trigger pipeline stages |
| `bin/target.py` | Claim/release targets, VM lifecycle, SSH commands |
| `bin/clone_vm.py` | Create/delete qcow2-backed VM clones from templates |
| `bin/dashboard.py` | Textual TUI for monitoring |
| `bin/push_to_github.py` | Push branches from root repos to GitHub |
| `bin/setup_win_vm.py` | Create Windows 11 VM with unattended install |
| `bin/provision_win_vm.py` | Provision Windows VM (VS2022, Git, Samba) |

## Quick start

```bash
# Create a workspace across multiple repos
bin/workspace.py create feature-auth Combatant Conveyor config

# Add pipeline stages
bin/workspace.py stage-add feature-auth test
bin/workspace.py stage-add feature-auth review

# Manage build targets
bin/target.py list
bin/target.py claim deb-02 feature-auth
bin/target.py run deb-02 "cmake --build ."

# Clone a VM from a template
sudo python3 bin/clone_vm.py create deb-01 deb-02 \
  --ip 10.101.0.100

# Watch for pipeline triggers
bin/pipeline_watch.py

# Monitor everything from the dashboard
bin/dashboard.py

# Push to GitHub when ready
bin/push_to_github.py feature-auth
```

## Build targets

Targets are VMs and hardware registered in
`config/targets.yaml`. Template VMs (deb-01, win-01) are
read-only base images -- agents work on clones.

| Target | OS | Role |
|--------|----|------|
| deb-01 | Debian 12 | Template (QEMU/KVM) |
| win-01 | Windows 11 Pro | Template (QEMU/KVM, UEFI + TPM) |

Clones use qcow2 backing files (fast to create,
space-efficient -- only store diffs from the template).
Clone IPs are allocated from `10.101.0.100+`. VMs use
KVM with `-cpu host` for near-native build performance
and mount `~/dev` via Samba for zero-copy source access.

```bash
# Create a Debian clone
sudo python3 bin/clone_vm.py create deb-01 deb-02 \
  --ip 10.101.0.100

# Create a Windows clone
sudo python3 bin/clone_vm.py create win-01 win-02 \
  --ip 10.101.0.101

# Delete a clone
sudo python3 bin/clone_vm.py delete deb-02
```

## Dashboard

The TUI dashboard (`bin/dashboard.py`) monitors the system
in real time with auto-refreshing panels:

```
+------------------------------------------------------------+
| Agents (3 active, 12 hidden)                               |
| Slug       Branch       Model  Status  Context   Tokens    |
+----------------------------+-------------------------------+
| Workspaces               | Stages                         |
+----------------------------+-------------------------------+
| Targets                                                    |
+------------------------------------------------------------+
| [n]ew ws  [c]laim  [x]release  [r]efresh  [q]uit          |
+------------------------------------------------------------+
```

## Project layout

```
bin/                  CLI tools
lib/                  Shared library modules
  config.py           Constants, config loaders
  workspace_ops.py    Workspace/stage operations
  target_ops.py       Target lock management
  session_parser.py   Claude session file parser
  ssh_utils.py        SSH command execution
  git_utils.py        Git helpers
tui/                  Dashboard TUI (Textual)
config/
  repos.yaml          Managed repo registry with push order
  targets.yaml        Target inventory (VMs + hardware)
templates/            CLAUDE.md templates, pipeline role snippets
context/              Architecture and decision docs
tests/                Unit tests
```

## Requirements

- Python 3.11+
- PyYAML
- Git
- libvirt + QEMU (for VM management)
- virtinst, libguestfs-tools, qemu-utils (for VM cloning)
- [Textual](https://textual.textualize.io/) (for dashboard)
- [Claude CLI](https://docs.anthropic.com/en/docs/claude-code)
