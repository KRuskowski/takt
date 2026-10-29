<p align="center"> <img src="graphics/takt.plain.darkmode.svg" width="280" alt="takt" /> </p>

Multi-agent pipeline orchestration for running Claude Code agents across multi-repo projects, on one machine or many.

## What it does

- Creates isolated **workspaces** (local clones + branches) for parallel agent work across multiple repos
- Defines **pipelines** per workspace — ordered sequences of agent steps (per-step model) and script steps (test, QA, push, PR creation, upstream merge)
- Runs pipelines via a **background service** that watches for branch changes and executes steps in temporary worktrees
- **Distributes runs across machines** — a scheduler owns the state and queue; workers dial out, lease runs, and execute them on their own hardware, including cloud VMs behind NAT
- Manages **deployment targets** (VMs and hardware) with exclusive, atomic locking and qcow2-backed VM cloning
- Runs **meta agents** for cross-cutting tasks (CLAUDE.md generation, pipeline setup, role template improvement)
- Accumulates a **searchable knowledge base** — scoped, attributed facts agents record as they work, plus section-level retrieval over the project docs
- Exposes everything through an **MCP server**, a **REST/SSE API**, and one **CLI** — no GUI

## Architecture

```
GitHub (upstream)
  |                                    ← operator gates what goes up
  v
~/dev/root/<repo>           Local mirrors, on the scheduler
  |
  +--> ~/dev/workspaces/<name>/    Isolated clones, one per task
  |                                 (where implementation happens)
  v
~/dev/runs/<ws>-<id>/<repo> Temporary worktrees per run
```

A run executes either on the scheduler itself or on a worker:

```
        scheduler                         worker (LAN or cloud)
  ┌───────────────────────┐         ┌──────────────────────────┐
  │ SQLite state + queue  │◀────────│ lease / heartbeat        │
  │ root repos            │  HTTP   │ own git mirrors          │
  │ renders agent prompts │────────▶│ worktrees at exact SHAs  │
  │ unbundles results     │◀────────│ runs steps, returns      │
  │ pushes to GitHub      │ bundle  │ commits as a git bundle  │
  └───────────────────────┘         └──────────────────────────┘
          workers dial out — no inbound access needed
```

### Data flow

1. **Pull**: GitHub -> root repos (fetch via service or manual `git pull`)
2. **Clone**: root repos -> workspace clones (`takt ws create`)
3. **Work**: agent writes and commits the implementation in the workspace clones, pushes to the root repo (its origin). All building happens here.
4. **Watch**: takt-service detects branch changes in root repos and creates a pipeline run
5. **Execute**: steps run sequentially in temporary worktrees, on the scheduler or on a worker that leased the run — agent steps via `claude-agent-sdk` in-process, or the `claude` CLI on a worker; script steps via Python functions
6. **Deliver**: the run pushes to GitHub and opens a PR; the operator reviews. Work produced on a worker returns to the scheduler as a git bundle first.

### Design principles

- **Workspace name = branch name** across all repos. One identifier ties together repos, tools, and git history.
- **No direct GitHub push.** Agents push to origin (the root repo) only, and workers only ever *fetch* — the scheduler is the single thing that talks to GitHub, so the operator gates what reaches it.
- **All state in SQLite.** Pipeline definitions, runs, steps, agent output, branch refs, worker registry, and target claims live in `.state/takt.db` (WAL mode), on the scheduler.
- **Claims are leases, not flags.** A worker holds a run under a generation-stamped lease. One that goes dark loses the lease and is refused when it returns, so a run is never executed or reported twice.
- **Per-step model selection.** Each agent step names its own model, by alias (`opus`, `sonnet`, `fable`, `haiku`) or full ID.
- **Progressive context disclosure.** CLAUDE.md files are lean and point to context packets. Agents fetch what they need rather than loading everything upfront.
- **Knowledge outlives the session.** What an agent learns during a run is recorded with its scope and provenance, so the next agent searches it instead of rediscovering it. See `context/context-engine.md`.
- **Pooled targets.** Deployment targets (VMs, hardware) are shared resources. Claiming one is a single atomic statement, so two machines cannot both believe they hold it.

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

One executable covers everything — workspaces, targets, pipelines, pushes, the service, workers, and reclamation:

```bash
takt ws list
takt target claim deb-02 feature-auth
takt pipeline show feature-auth
takt worker --scheduler https://takt:7433 --id build-01
takt reclaim --older-than 30
```

Source `config/takt.zsh` from `~/.zshrc` for the `takt` shell function with completions for every subcommand. The individual `bin/*.py` scripts still work and remain thin wrappers over the same code.

## Tools

`bin/takt.py` is the entry point for day-to-day use. The scripts below it still work directly, and the ones marked "also" are reachable as `takt` subcommands.

| Tool | Purpose |
|------|---------|
| `bin/takt.py` | **The CLI** — `ws`, `target`, `pipeline`, `push`, `service`, `worker`, `reclaim` |
| `bin/takt_service.py` | Background service: REST API, branch watching, pipeline execution |
| `bin/takt_worker.py` | Worker daemon (also `takt worker`) |
| `bin/takt_mcp.py` | MCP server for Claude sessions |
| `bin/takt_agent.py` | Conversational REPL for managing takt |
| `bin/reclaim.py` | Report/reclaim build output (also `takt reclaim`) |
| `bin/workspace.py` | Create/delete workspaces, define pipelines (also `takt ws`) |
| `bin/target.py` | Claim/release targets, VM lifecycle, SSH (also `takt target`) |
| `bin/push_to_github.py` | Push branches from root repos to GitHub (also `takt push`) |
| `bin/pipeline_watch.py` | Standalone poll for branch changes |
| `bin/setup_takt.py` | First-time setup (venv, systemd unit, MCP) |
| `bin/clone_vm.py` | Create/delete qcow2-backed VM clones |
| `bin/provision_vm.py` | Provision Debian VM (build tools, shell config) |
| `bin/setup_win_vm.py` | Create Windows 11 VM (unattended) |
| `bin/provision_win_vm.py` | Provision Windows VM (VS2022, Git, Samba) |
| `bin/setup_libvirt.py` | Install libvirt/QEMU and configure networking |

## Quick start

```bash
# First-time setup (venv, systemd unit, MCP server)
python3 bin/setup_takt.py

# Start the background service
systemctl --user start takt-service

# Create a workspace across multiple repos
takt ws create feature-auth RepoA RepoB

# ... write and commit the implementation in
# ~/dev/workspaces/feature-auth/, then push to the root repo ...

# Define what happens to it: verify, then deliver
takt pipeline set feature-auth test review push_to_github create_pr

# Watch it run
takt pipeline runs feature-auth
```

A run starts when takt-service notices the branch move in the root repo. To start one by hand, use the MCP `trigger_run` tool or the API directly — there is deliberately no `takt` subcommand for it, since a run is normally a consequence of pushing rather than something you invoke:

```bash
curl -X POST http://localhost:7433/api/runs \
  -H 'Content-Type: application/json' \
  -d '{"workspace": "feature-auth"}'
```

To add a second machine, see [Compute](#compute).

## Pipeline

Pipelines are ordered sequences of steps defined per workspace. Steps are either **agent** (Claude Code — `claude-agent-sdk` in-process on the scheduler, or the `claude` CLI on a worker) or **script** (built-in Python functions).

**A pipeline is how finished work gets upstream.** It runs post-implementation: the steps test, review, document and QA an implementation that is already written and committed, and then deliver it — pushing to GitHub and opening a PR. Delivery is the point of it.

A pipeline is not how code gets written. That happens in the workspace first; the run operates on temporary worktrees created from what is already committed in the root repos, so it cannot see uncommitted work and is never handed something to implement. A step may still compile (`deploy_qa` builds on a target) but as verification of existing work.

The `feature` and `refactor` roles predate this rule and contradict it. They remain defined so existing pipelines keep working, but new pipelines should not use them.

Built-in scripts:
- `push_to_github` — push branch to GitHub in dependency order
- `create_pr` — create GitHub PRs via `gh` CLI
- `merge_upstream` — fetch and merge default branch into workspace branch

Agent steps name their own model (alias or full ID) and get a role prompt from `templates/pipeline_roles.md`. Per-step custom prompts are set via `set_pipeline`. The prompt is always rendered on the scheduler, so the role templates are authoritative in one place even when the step runs elsewhere.

A step reports its own verdict by writing `.stage-result.json` in the worktree. That verdict decides the step: an agent finishing cleanly but writing `{"status": "fail"}` is a failed step, not a passed one — so a failing test suite cannot come back green.

Where a run executes is decided by whoever leases it first — the scheduler itself, or any worker whose capabilities satisfy it. `max_agents` caps concurrent runs on a single machine; adding capacity means adding machines. See [Compute](#compute).

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
takt target list
takt target claim deb-02 feature-auth
takt target run deb-02 "cmake --build ."
```

Claims live in the database rather than in lock files, so they are visible to every machine and claiming is atomic — two workers cannot both believe they hold the same VM.

## Compute

Runs can execute on other machines. One **scheduler** owns the SQLite state, the queue and the root repos; **workers** dial out, lease runs, execute them locally and report back over HTTP. Outbound only, so a NAT'd cloud VM needs no inbound access.

```bash
# On the scheduler: ~/.config/takt/env, mode 0600
#   TAKT_API_HOST=0.0.0.0
#   TAKT_API_TOKEN=<secret>

# LAN worker — root repos fetched from the scheduler over SSH
takt worker --id build-01 --scheduler https://takt:7433 \
  --token <secret> --upstream scheduler --scheduler-host karl@takt

# Cloud worker — no route back, so it fetches from GitHub
takt worker --id cloud-01 --scheduler https://takt.example.com \
  --token <secret> --upstream github
```

The service refuses to bind a non-loopback address unless a token is set: the API creates workspaces and triggers runs, so unauthenticated and reachable must never both be true.

**Credentials live outside the checkout.** `config/takt.yaml` is tracked in a public repo, so a token written there would be published. takt reads `~/.config/takt/env` (`KEY=value`, mode 0600) instead — systemd loads it into the service via `EnvironmentFile`, and the client tools (MCP, CLI, `reclaim`) read the same file, so turning authentication on does not mean exporting a token in every shell. `TAKT_API_TOKEN`, `TAKT_API_URL` and `TAKT_API_HOST` in the real environment still win, which is how a worker on another machine is configured.

Note that once a token is set, **loopback is authenticated too**. That is deliberate: a TLS reverse proxy terminating in front of takt connects from loopback, and exempting it would hand every proxied request an unauthenticated path in.

### Provisioning a worker

```bash
takt provision laptop --scheduler http://10.0.1.33:7433   # all managed repos
takt provision laptop --repos OTC.SDK OTC.Relay           # a subset
takt provision laptop --check                             # checks only, changes nothing
```

Run from the scheduler. It installs takt on the machine (HTTPS clone, so no git credentials needed), writes `~/.config/takt/env` at mode 0600, creates the work and mirror directories, then **pushes** the root repos over as bare mirrors and prints what the machine reports about itself.

The push direction is deliberate. `--upstream scheduler` has the worker *fetch*, which needs an unlocked key on the worker — the usual blocker on a laptop whose key is passphrase-protected. Provisioning instead uses the SSH connection the scheduler already has, so a worker can be fully stocked while holding no git credentials of its own. Re-running it is the refresh path: git sends only what the far side lacks. Accordingly, a worker that cannot refresh an *existing* mirror logs a warning and carries on — whether the run can proceed is decided by the commit check in `materialise()`, which fails precisely if the needed commit really is missing.

`--check` runs the cheap checks alone: a clean SSH channel and the required tools. The channel check exists because that failure is so badly disguised — anything written to stdout on login (an sshd `Banner`, a `~/.ssh/rc` that echoes, a shell rc greeting, and for zsh `~/.zshenv`, which non-interactive shells also read) gets spliced in front of every command's output, and git and rsync then fail with "protocol incompatibility" or "is your shell clean?", naming neither the file nor the machine at fault.

Each worker keeps its own git mirrors — a worktree's `.git` points into its parent repo, so it cannot live on a different machine — and materialises worktrees at the exact commit the run recorded, not the branch tip. Commits produced on a worker return as a **git bundle** over the same outbound connection; workers only ever *fetch*, so the operator still gates what reaches GitHub, and `push_to_github`/`create_pr` are refused on a worker.

Claims are leases carrying a generation, so a worker that goes dark long enough to lose its lease is refused when it returns instead of reporting over whoever took the run over. A worker without Claude Code declares itself build-only and is never offered a run containing an agent step. `--disk-floor-mb` stops it accepting work when nearly full, and worktrees are removed at run completion.

**Agent steps run on workers too**, by invoking the `claude` CLI as a subprocess with the prompt the scheduler rendered — so role templates stay authoritative in one place and a worker needs only the CLI installed. A worker declares `agent_steps` from whether `claude` is on PATH and is never handed an agent run if it isn't. `--claude-home` sets `CLAUDE_CONFIG_DIR`, which is what actually multiplies agent throughput: limits attach to the **account**, not the machine, so workers sharing one account share one quota.

Design notes and the remaining stage (elastic cloud provisioning): `context/distributed-execution.md`

Three things worth knowing before adding machines:

- **Claude capacity is per account, not per machine.** Rate and usage limits attach to the account, so running agents on more machines does not by itself raise how much model work can happen at once — give each worker its own account via `--claude-home` if that is the goal. Extra machines help CPU-bound work — compiles, test suites, VM-based verification — unconditionally.
- **State stays on the scheduler.** `.state/takt.db` is WAL-mode SQLite and does not lock correctly over NFS/SMB, so a worker never touches it — everything goes through the worker endpoints. Target claims moved out of `.locks/*.lock` into the database for the same reason: a lock file is invisible to the other machines, and the check-then-write it replaced let two claimants both think they had won the same VM.
- **Build output is the storage cost, not git.** Workspace clones are hardlinked against the root repos and so cost almost nothing; the build trees agents create inside them dominate, and nothing reclaimed them before `takt reclaim`. Workspaces are deliberately durable, so reclamation has to tell the clone apart from the build output within it — and a worker reclaims its own, since nobody is logged in to notice it filling up.

```bash
takt reclaim                        # survey; deletes nothing
takt reclaim --older-than 30        # only workspaces idle 30+ days
takt reclaim --older-than 30 --apply
```

Dry run by default. A directory is a candidate only if it carries a marker that nothing but generated output has — `CMakeCache.txt`, `CACHEDIR.TAG`, or the name `node_modules` — so a directory merely *called* `build` is left alone and nothing under `.git` is considered. `--apply` additionally requires `--older-than` or `--workspace`, re-checks each marker at deletion time, and spares any workspace with a run in flight.

`bin/takt_mcp.py` honours `TAKT_API_URL` and `TAKT_API_TOKEN`, so a Claude session on any machine can drive a remote scheduler — which decouples *where you work* from *where takt runs*.

## Project layout

```
bin/
  takt.py             The CLI — every subcommand
  takt_service.py     Background service entry point
  takt_worker.py      Worker daemon (also `takt worker`)
  takt_mcp.py         MCP server
  reclaim.py          Build-output reclamation (also `takt reclaim`)
  ...                 VM provisioning, setup, push
lib/                  Shared Python library
  config.py           Constants, config loaders
  db.py               SQLite state layer (incl. leases, locks)
  commands.py         Shared CLI/API command dispatch
  pipeline.py         Pipeline executor, script steps, prompts
  checks.py           Built-in check steps
  service.py          Background service (ZMQ + asyncio)
  api.py              REST/SSE API (aiohttp), worker endpoints
  agent_runner.py     claude-agent-sdk wrapper (in-process agents)
  worker.py           Worker side: mirrors, worktrees, claude CLI
  worker_ops.py       Scheduler side: leases, reports, bundles
  workspace_ops.py    Workspace operations
  target_ops.py       Target config and claims
  git_utils.py        Git helpers
  worktree.py         Run worktree lifecycle
  meta_runner.py      Meta agent executor
  context_store.py    Context engine (FTS5 + facts)
config/
  repos.yaml          Managed repo registry (gitignored)
  targets.yaml        Target inventory (gitignored)
  takt.yaml           Service config (bind address, token, state dir,
                      agent accounts)
  takt.zsh            Shell function + completions
templates/            CLAUDE.md templates, pipeline roles
context/              Architecture and decision docs
graphics/             Logos and icons
tests/                594 tests
```

## Requirements

**Scheduler:**

- Python 3.11+ with PyYAML, pyzmq, aiohttp, claude-agent-sdk
- Git
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) for in-process agent steps
- libvirt + QEMU (for VM targets, optional)

**Worker** (anything beyond this is per-workload):

- Python 3.11+ and Git
- The `claude` CLI, only if it should run agent steps — otherwise start it with `--no-agent-steps` and it will only be offered build and test work
- Whatever toolchains the steps need (cmake, ninja, compilers, …). A worker reports what it has on registration.

## License

MIT — see [LICENSE](LICENSE).
