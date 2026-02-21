# Multi-Agent Pipeline — Design Session Summary

## Starting Point

A workflow for running multiple Claude CLI agents in parallel across multi-repo projects.
Root repositories live in `~/dev/root`, workspaces are created in `~/dev/workspaces` as local
clones, each workspace gets its own agent. Agents read instructions from Claude.md files.

## What We Designed

### 1. Claude.md Layering

Three layers of context, each with a different purpose:

- **Root repo Claude.md** — project-level truth: architecture, conventions, tech stack,
  git rules, cross-repo dependencies. Doesn't change per task. One per repo.
- **Workspace Claude.md** — task-level context: role, task description, acceptance criteria,
  scope (which repos are in-scope vs reference-only), and a session state section at the
  bottom for tracking progress between sessions.
- **Pipeline role templates** — snippets for specialized agents (feature, test, review, docs,
  refactor, deploy/QA). Composed into workspace Claude.md files as needed.

### 2. Session State

Each workspace Claude.md has a session state section at the bottom:
status, completed work, in-progress items, decisions made, blockers, next steps.
Agents update this before ending. Next session (or next pipeline agent) reads it
to pick up context. This is the persistence mechanism between sessions.

### 3. Multi-Repo Branching

The workspace name IS the branch name. All repos in a workspace use the same branch.
This ties everything together across repos. Repos are listed in push order matching
the dependency chain (upstream first). A branch status table in session state tracks
which repos have been pushed.

The full chain:
```
GitHub (origin) → ~/dev/root/repo (root) → ~/dev/workspaces/feature-x/repo (workspace)
```
Agents never push to GitHub directly. You are the gatekeeper.

### 4. Pipeline Watcher (pipeline_watch.py)

A Python script that polls root repos for new/updated branches. When changes are detected:
- Groups changes by branch name across all repos
- Gathers diffs, commit logs, and Claude.md files from all affected repos
- Pipes everything into a Claude CLI session
- Claude analyzes the cross-repo changes and proposes a pipeline plan
- You approve, modify, or skip before anything executes
- After the session ends, refs are updated and the watcher resumes

Thin by design — all intelligence is in Claude, the script is just plumbing.

### 5. Pipeline Stages

Typical flow:
```
feature-agent → test-agent → review-agent → docs-agent → you push to GitHub
```

Each stage is a workspace with a role-specific Claude.md. Stages can be customized
per change (not every change needs all stages). The watcher suggests stages based
on what changed (source code → tests + review + docs, config only → review).

### 6. Target Management (target.py)

Unified CLI for managing both physical hardware (Raspberry Pis) and libvirt VMs.
Handles allocation (claim/release with file-based locks) and VM lifecycle
(start/stop/snapshot via virsh).

Agent workflow:
```bash
target claim win-01 feature-auth    # exclusive lock
target up win-01                    # boot VM (no-op for hardware)
target run win-01 "cmake ..."       # build via SSH
target down win-01                  # shut down VM
target release win-01               # free the lock
```

Pool-based approach (not cloned per workspace) to conserve RAM.
Hardware targets (raspis) are always on, just need claim/release.

### 7. Windows Build Performance

The main problems: QEMU without KVM is 10-50x slower, and SSH file transfer is painful.

Fixes:
- **KVM + `-cpu host`** — near-native compilation speed (the single biggest win)
- **Shared folders** — mount ~/dev into the VM via Samba (`smb=` flag in QEMU) or 9p
  passthrough (libvirt). Agents build directly from the share, no file copying at all.
  Windows VM sees it as `Z:\`, Linux VM as `/mnt/host`.
- **clang-cl + xwin** — potential cross-compilation from Linux for C++ DLLs targeting
  MSVC ABI, but complex projects may hit rough edges. Worth trying for fast iteration,
  fall back to real MSVC for final builds.

### 8. Automated UI Testing

For reducing manual testing of React UIs:

- **Playwright MCP** — gives Claude CLI browser control. The official Microsoft server
  (`@playwright/mcp`) uses accessibility tree, not screenshots. Add `.mcp.json` to
  project root and agents can navigate, click, fill forms, take screenshots.
- **Playwright Test Agents** — three built-in agents (Planner, Generator, Healer) that
  explore the app, write E2E tests, and auto-fix failures. Just markdown files, customizable.
- **Visual regression** — Chromatic (for Storybook) or BackstopJS (open source) for
  screenshot-based comparison on every push.

Fits into the pipeline as a QA stage between test-agent and review-agent.

### 9. Context Management Strategy

Based on research and our discussion:

- **Progressive disclosure** — don't front-load context. Tell agents where to find info,
  let them fetch it when needed. CLAUDE.md should be lean (<150 lines).
- **Pointers for code, prose for rationale** — point to files for anything that lives in
  code (architecture, APIs, build commands). Write prose for things that don't live anywhere
  else (decisions, gotchas, mental models, the "why").
- **Context packets** — a `context/` directory in each workspace with focused files
  (architecture.md, decisions.md, environment.md, etc.). Agents self-select which packets
  to read based on their role. Packets accumulate knowledge across sessions and agents.
- **Session state is ephemeral, context packets are persistent** — session state tracks
  what's happening now. Context packets capture institutional knowledge.
- **Don't use Claude as a linter** — use hooks and pre-commit scripts for formatting.
  Keep style rules out of Claude.md.

### 10. Ansible (mentioned, not built)

Docker container running Ansible for maintaining target infrastructure. Provisions
raspis, keeps QEMU base images updated, ensures consistent state. Separate concern
from the target CLI (Ansible = what's installed, target CLI = who's using it).
Not yet integrated into the pipeline but could be triggered by agents to self-heal.

## Files Produced

| File | Purpose |
|------|---------|
| `ROOT_REPO_CLAUDE.md` | Template for per-repo Claude.md |
| `WORKSPACE_CLAUDE.md` | Template for workspace Claude.md with session state |
| `PIPELINE_ROLES.md` | Role templates for pipeline agents (multi-repo aware) |
| `pipeline_watch.py` | Branch-aware watcher that launches Claude CLI |
| `push-to-github.sh` | Push approved branches to GitHub across all repos |
| `target.py` | Unified target management (allocation + libvirt + SSH) |
| `targets.yaml` | Target inventory (hardware + VMs) |
| `DEPLOY_QA_ROLE.md` | Deploy/QA agent role template |
| `WINDOWS_BUILD_GUIDE.md` | KVM, shared folders, cross-compilation guide |

## Key Principles

- **Workspace name = branch name** across all repos. One identifier ties everything together.
- **Agents never push to GitHub.** Root repos are staging, you are the gatekeeper.
- **Session state is in Claude.md**, not a separate file. Agent always sees task + progress in one read.
- **Pool targets, don't clone** — RAM is the constraint. Boot, build, shut down, next agent.
- **Shared folders eliminate file transfer.** Code lives on the host, VMs build from the share.
- **Progressive disclosure for context.** Small Claude.md, pointer-style references, agents fetch what they need.
- **You're ahead of most people.** Multi-repo orchestrated pipelines with hardware targets is still DIY territory.
