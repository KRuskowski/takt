<p align="center"> <img src="graphics/takt.plain.darkmode.svg" width="280" alt="takt" /> </p>

Multi-agent pipeline orchestration for running Claude Code agents across multi-repo projects.

## What it does

- Creates isolated **workspaces** (local clones + branches) for parallel agent work across multiple repos
- Defines **pipelines** per workspace — ordered sequences of agent steps (with per-step model selection) and script steps (push, PR creation, upstream merge)
- Runs pipelines via a **background service** that watches for branch changes and executes steps in temporary worktrees
- Manages **deployment targets** (VMs and hardware) with exclusive locking and qcow2-backed VM cloning
- Runs **meta agents** for cross-cutting tasks (CLAUDE.md generation, pipeline setup, role template improvement)
- Accumulates a **searchable knowledge base** — scoped, attributed facts agents record as they work, plus section-level retrieval over the project docs
- Exposes everything through an **MCP server**, a **REST/SSE API**, and a **CLI** — no GUI

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

1. **Pull**: GitHub -> root repos (fetch via service or manual `git pull`)
2. **Clone**: root repos -> workspace clones (`workspace.py create`)
3. **Work**: agent modifies workspace clones, pushes to root repo (its origin)
4. **Watch**: takt-service detects branch changes in root repos, creates pipeline runs
5. **Execute**: pipeline steps run sequentially in temporary worktrees — agents via Claude Code SDK, scripts via Python functions
6. **Push**: operator reviews and pushes from root repos to GitHub (`push_to_github.py`)

### Design principles

- **Workspace name = branch name** across all repos. One identifier ties together repos, tools, and git history.
- **No direct GitHub push.** Agents push to origin (root repo) only. Human operator gates what reaches GitHub.
- **All state in SQLite.** Pipeline definitions, runs, steps, agent output, and branch refs live in `.state/takt.db` (WAL mode).
- **Per-step model selection.** Each agent step can run with a different Claude model (sonnet/opus/haiku).
- **Progressive context disclosure.** CLAUDE.md files are lean and point to context packets. Agents fetch what they need rather than loading everything upfront.
- **Knowledge outlives the session.** What an agent learns during a run is recorded with its scope and provenance, so the next agent searches it instead of rediscovering it. See `context/context-engine.md`.
- **Pooled targets.** Deployment targets (VMs, hardware) are shared resources with exclusive locking. Agents claim, use, and release.

## Interfaces

takt is headless. It is driven by agents through MCP, by scripts through the REST API, and by hand through the CLI.

### MCP Server

FastMCP server wrapping the takt REST API, and the primary way takt is driven. Installed at user scope so it's available in every Claude session on the machine — no per-project approval needed. 37 tools covering workspaces, targets, pipelines, runs, templates, repos, accounts, the issue ledger, and the context engine.

```bash
claude mcp add -s user takt .venv/bin/python3 bin/takt_mcp.py
```

### REST/SSE API

The background service exposes a REST API on port 7433 with SSE for real-time events.

```bash
curl http://localhost:7433/api/workspaces
curl http://localhost:7433/api/runs?workspace=my-ws
curl http://localhost:7433/api/events?topics=step.update
```

### CLI

```bash
bin/takt.py ws list
bin/takt.py target claim deb-02 feature-auth
bin/takt.py pipeline show feature-auth
```

Source `config/takt.zsh` from `~/.zshrc` for a `takt` shell function with completions.

## Tools

| Tool | Purpose |
|------|---------|
| `bin/workspace.py` | Create/delete workspaces, define pipelines |
| `bin/takt_service.py` | Background service (REST API + pipeline execution) |
| `bin/takt.py` | Unified CLI (workspaces, targets, pipelines, push) |
| `bin/takt_agent.py` | Conversational REPL for managing takt |
| `bin/takt_mcp.py` | MCP server for Claude sessions |
| `bin/pipeline_watch.py` | Standalone poll for branch changes |
| `bin/target.py` | Claim/release targets, VM lifecycle, SSH |
| `bin/clone_vm.py` | Create/delete qcow2-backed VM clones |
| `bin/provision_vm.py` | Provision Debian VM (build tools, shell config) |
| `bin/push_to_github.py` | Push branches from root repos to GitHub |
| `lib/context_store.py` | Context engine (FTS5 search + recorded facts) |
| `bin/setup_takt.py` | First-time setup (systemd unit, venv, MCP) |
| `bin/setup_win_vm.py` | Create Windows 11 VM (unattended) |
| `bin/provision_win_vm.py` | Provision Windows VM (VS2022, Git, Samba) |
| `bin/setup_libvirt.py` | Install libvirt/QEMU and configure networking |

## Quick start

```bash
# First-time setup (venv, systemd unit, MCP server)
python3 bin/setup_takt.py

# Create a workspace across multiple repos
bin/workspace.py create feature-auth RepoA RepoB

# Define a pipeline
bin/workspace.py pipeline-set feature-auth test push_to_github

# Start the background service
systemctl --user start takt-service

# Trigger a run
bin/workspace.py trigger feature-auth

# Push to GitHub when ready
bin/push_to_github.py feature-auth
```

## Pipeline

Pipelines are ordered sequences of steps defined per workspace. Steps are either **agent** (Claude Code via SDK) or **script** (built-in Python functions).

Built-in scripts:
- `push_to_github` — push branch to GitHub in dependency order
- `create_pr` — create GitHub PRs via `gh` CLI
- `merge_upstream` — fetch and merge default branch into workspace branch

Agent steps run with a configurable model (sonnet/opus/haiku) and get a role prompt from `templates/pipeline_roles.md`. Per-step custom prompts are set via `set_pipeline`. Results are written to `.stage-result.json` in the worktree.

Steps execute on the machine running takt-service, with `max_agents` capping how many runs proceed concurrently. See [Compute](#compute) for what that implies.

## Context engine

A searchable knowledge base over takt's own context: the markdown in `context/` and `templates/`, plus facts agents record as they work. SQLite FTS5 in the same `.state/takt.db`, BM25 ranked.

Two properties that matter:

- **Retrieval returns sections, not files.** Documents are split at markdown headings, so a result is the one relevant section with its heading path and line range — not a filename to go read. Queries can be natural language; an exact-match pass runs first and widens automatically when a question uses wording the corpus doesn't.
- **Agents can write to it.** What an agent works out during a run — a required build flag, a machine quirk, an API that contradicts its docs — is recorded with a scope (`global`, or a specific repo/workspace/target) and provenance (which run, which workspace, when last verified). A repo-scoped search sees that repo plus global and nothing from other repos. Facts can be superseded without being deleted.

The index maintains itself: rebuilt at service startup (content-hashed, so near-free when nothing changed) and on every document edit through the API.

```bash
curl "http://localhost:7433/api/context/search?q=how+do+I+build+the+SDK"
curl "http://localhost:7433/api/context/search?q=ffmpeg&scope_kind=repo&scope_value=OTC.SDK"
curl http://localhost:7433/api/context/stats
```

MCP tools: `search`, `record_fact`, `list_facts`, `supersede_fact`, `context_stats`, `reindex_context`.

Design notes, including why there are no embeddings yet: `context/context-engine.md`

## Deployment targets

Targets are VMs and hardware registered in `config/targets.yaml`. Template VMs are read-only base images — agents work on clones.

Clones use qcow2 backing files (fast to create, space-efficient — only store diffs from the template). VMs use KVM with `-cpu host` for near-native performance and mount `~/dev` via Samba for zero-copy source access.

```bash
# Create a clone from a template
sudo python3 bin/clone_vm.py create deb-01 deb-02 \
  --ip 10.101.0.100

# Provision a Debian VM with build tools
python3 bin/provision_vm.py deb-02

# Manage targets
bin/target.py list
bin/target.py claim deb-02 feature-auth
bin/target.py run deb-02 "cmake --build ."
```

## Compute

takt is currently **single-host**: one `takt-service` owns the SQLite state, the worktrees and the executor, and every pipeline step — agent and script alike — runs as a local process on that machine. `max_agents` (default in `lib/service.py`) caps concurrent runs, which throttles contention rather than adding capacity.

Targets are the one way to push work off the host today, and they are deliberately not wired into the pipeline executor — `bin/target.py run <target> <cmd>` is an explicit, operator- or agent-driven SSH call. A build or test suite can be offloaded that way; the agent driving it still runs locally.

Two things to know before scaling out:

- **Claude capacity is per account, not per machine.** Rate and usage limits attach to the account in `config/takt.yaml`, so running agents on more machines does not raise how much model work you can do concurrently. Extra machines help with CPU-bound work — compiles, test suites, VM-based verification — not with agent throughput.
- **Workers cannot reach the state or the git origin.** `.state/takt.db` is SQLite with WAL (local filesystem only), `.locks/*.lock` are local files, and workspace clones use `~/dev/root/<repo>` as their origin. Distributing execution means deciding how a remote machine obtains source and reports state before anything else.
- **Nothing reclaims build output.** Workspace clones are hardlinked against the root repos and so cost almost nothing, but the build trees agents create inside them are never cleaned up and dominate storage. Workspaces are deliberately durable, so reclamation has to distinguish the clone from the build output within it.

A design for lifting this — a scheduler node, a pull-based worker pool reachable from cloud instances, and workstations as thin clients — is written up in `context/distributed-execution.md`. Not implemented.

Note that `bin/takt_mcp.py` already honours `TAKT_API_URL`, so a Claude session on another machine can drive a remote takt as soon as the service is reachable. The service currently binds to `127.0.0.1` and the API is unauthenticated, so that is not yet usable off-box.

## Project layout

```
bin/                  CLI tools and scripts
lib/                  Shared Python library
  config.py           Constants, config loaders
  db.py               SQLite state layer
  pipeline.py         Pipeline executor
  service.py          Background service (ZMQ + asyncio)
  api.py              REST/SSE API (aiohttp)
  agent_runner.py     Claude Code SDK wrapper
  workspace_ops.py    Workspace operations
  git_utils.py        Git helpers
  meta_runner.py      Meta agent executor
  context_store.py    Context engine (FTS5 + facts)
config/
  repos.yaml          Managed repo registry (gitignored)
  targets.yaml        Target inventory (gitignored)
  takt.yaml           Service config (API port, agent model)
templates/            CLAUDE.md templates, pipeline roles
context/              Architecture and decision docs
graphics/             Logos and icons
tests/                Unit tests
```

## Requirements

- Python 3.11+ with PyYAML, pyzmq, aiohttp, claude-code-sdk
- Git
- libvirt + QEMU (for VM management, optional)
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code)

## License

MIT — see [LICENSE](LICENSE).
