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

One executable covers everything — workspaces, targets, pipelines, pushes, the
service, workers, and reclamation:

```bash
takt ws list
takt target claim deb-02 feature-auth
takt pipeline show feature-auth
takt worker --scheduler https://takt:7433 --id build-01
takt reclaim --older-than 30
```

Source `config/takt.zsh` from `~/.zshrc` for the `takt` shell function with
completions for every subcommand. The individual `bin/*.py` scripts still work
and remain thin wrappers over the same code.

## Tools

| Tool | Purpose |
|------|---------|
| `bin/workspace.py` | Create/delete workspaces, define pipelines |
| `bin/takt_service.py` | Background service (REST API + pipeline execution) |
| `bin/takt.py` | **The CLI** — ws, target, pipeline, push, service, worker, reclaim |
| `bin/takt_agent.py` | Conversational REPL for managing takt |
| `bin/takt_mcp.py` | MCP server for Claude sessions |
| `bin/pipeline_watch.py` | Standalone poll for branch changes |
| `bin/target.py` | Claim/release targets, VM lifecycle, SSH |
| `bin/clone_vm.py` | Create/delete qcow2-backed VM clones |
| `bin/provision_vm.py` | Provision Debian VM (build tools, shell config) |
| `bin/push_to_github.py` | Push branches from root repos to GitHub |
| `bin/takt_worker.py` | Worker daemon (also `takt worker`) |
| `bin/reclaim.py` | Report/reclaim build output (also `takt reclaim`) |
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

**A pipeline is how finished work gets upstream.** It runs post-implementation: the steps test, review, document and QA an implementation that is already written and committed, and then deliver it — pushing to GitHub and opening a PR. Delivery is the point of it.

A pipeline is not how code gets written. That happens in the workspace first; the run operates on temporary worktrees created from what is already committed in the root repos, so it cannot see uncommitted work and is never handed something to implement. A step may still compile (`deploy_qa` builds on a target) but as verification of existing work.

The `feature` and `refactor` roles predate this rule and contradict it. They remain defined so existing pipelines keep working, but new pipelines should not use them.

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

Runs can execute on other machines. One **scheduler** owns the SQLite state, the queue and the root repos; **workers** dial out, lease runs, execute them locally and report back over HTTP. Outbound only, so a NAT'd cloud VM needs no inbound access.

```bash
# On the scheduler: config/takt.yaml
#   api_host: 0.0.0.0
#   api_token: <secret>

# LAN worker — root repos fetched from the scheduler over SSH
takt worker --id build-01 --scheduler https://takt:7433 \
  --token <secret> --upstream scheduler --scheduler-host karl@takt

# Cloud worker — no route back, so it fetches from GitHub
takt worker --id cloud-01 --scheduler https://takt.example.com \
  --token <secret> --upstream github
```

The service refuses to bind a non-loopback address unless `api_token` is set: the API creates workspaces and triggers runs, so unauthenticated and reachable must never both be true.

Each worker keeps its own git mirrors — a worktree's `.git` points into its parent repo, so it cannot live on a different machine — and materialises worktrees at the exact commit the run recorded, not the branch tip. Commits produced on a worker return as a **git bundle** over the same outbound connection; workers only ever *fetch*, so the operator still gates what reaches GitHub, and `push_to_github`/`create_pr` are refused on a worker.

Claims are leases carrying a generation, so a worker that goes dark long enough to lose its lease is refused when it returns instead of reporting over whoever took the run over. A worker without Claude Code declares itself build-only and is never offered a run containing an agent step. `--disk-floor-mb` stops it accepting work when nearly full, and worktrees are removed at run completion.

**Agent steps run on workers too**, by invoking the `claude` CLI as a subprocess with the prompt the scheduler rendered — so role templates stay authoritative in one place and a worker needs only the CLI installed. A worker declares `agent_steps` from whether `claude` is on PATH and is never handed an agent run if it isn't. `--claude-home` sets `CLAUDE_CONFIG_DIR`, which is what actually multiplies agent throughput: limits attach to the **account**, not the machine, so workers sharing one account share one quota.

Design notes and the remaining stage (elastic cloud provisioning): `context/distributed-execution.md`

Two things that distribution does **not** solve:

- **Claude capacity is per account, not per machine.** Rate and usage limits attach to the account in `config/takt.yaml`, so running agents on more machines does not raise how much model work you can do concurrently. Extra machines help with CPU-bound work — compiles, test suites, VM-based verification — not with agent throughput.
- **State stays on the scheduler.** `.state/takt.db` is WAL-mode SQLite and does not lock correctly over NFS/SMB, so a worker never touches it — everything goes through the worker endpoints. Target claims moved out of `.locks/*.lock` into the database for the same reason: a lock file is invisible to the other machines, and the check-then-write it replaced let two claimants both think they had won the same VM.
- **Build output is the storage cost, not git.** Workspace clones are hardlinked against the root repos and so cost almost nothing; the build trees agents create inside them dominate. Nothing reclaimed them until `bin/reclaim.py`, and workspaces are deliberately durable, so reclamation has to tell the clone apart from the build output within it.

```bash
takt reclaim                        # survey; deletes nothing
takt reclaim --older-than 30        # only workspaces idle 30+ days
takt reclaim --older-than 30 --apply
```

Dry run by default. A directory is a candidate only if it carries a marker that nothing but generated output has — `CMakeCache.txt`, `CACHEDIR.TAG`, or the name `node_modules` — so a directory merely *called* `build` is left alone and nothing under `.git` is considered. `--apply` additionally requires `--older-than` or `--workspace`, re-checks each marker at deletion time, and spares any workspace with a run in flight.

`bin/takt_mcp.py` honours `TAKT_API_URL` and `TAKT_API_TOKEN`, so a Claude session on any machine can drive a remote scheduler — which decouples *where you work* from *where takt runs*.

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
