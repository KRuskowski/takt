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

One executable for everything:

```bash
takt ws list|create|delete|status
takt target list|claim|release|up|down|run|status
takt pipeline set|show|runs
takt push <branch> [--dry-run] [-y] [--repos ...]
takt service start|stop|restart|status
takt worker --scheduler <url> --id <name> [...]
takt reclaim [--older-than N] [--apply]
```

Sourcing `config/takt.zsh` from `~/.zshrc` provides the `takt` shell function with completions for all of the above.

`worker` and `reclaim` own a long-running process or their own output, so they contribute flags from their own module and are invoked directly rather than through `lib/commands.py` dispatch. The standalone scripts (`bin/takt_worker.py`, `bin/reclaim.py`) remain as thin wrappers over the same functions, so neither entry point can drift from the other — `tests/test_takt_cli.py` fails if they do.

takt has no graphical or terminal UI. Interaction is via the MCP server, the REST API on port 7433, and this CLI.

## Reclaim Build Output (`bin/reclaim.py`)

Workspace clones are hardlinked and nearly free; the build trees inside them dominate storage and nothing else reclaims them. Dry run by default.

```bash
takt reclaim                              # survey only
takt reclaim --older-than 30              # idle 30+ days
takt reclaim --older-than 30 --apply      # delete
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
- **Pipeline**: Ordered sequence of steps (agents or scripts) defined in SQLite per workspace. Steps run sequentially in temporary worktrees from root repos. **Pipelines are how finished work gets upstream.** They run post-implementation: they test, review, document and QA work that is already written and committed, then push it and open a PR. A pipeline is not how code gets written — that happens in the workspace first. (A step may still compile, e.g. `deploy_qa` on a target, but as verification.) The `feature` and `refactor` roles are legacy and contradict this; don't put them in new pipelines. See `templates/pipeline_roles.md`.
- **Runs**: `~/dev/runs/<ws>-<run_id>/<repo>` — git worktrees created for each pipeline run.
- **Workspace name = branch name** across all repos.
- **Agents never push to GitHub.** takt handles all pipeline orchestration, pushes, and PR creation.
- **State**: All pipeline state in `.state/takt.db` (SQLite, WAL mode).
- **Context engine**: Scoped, searchable knowledge base in SQLite FTS5. Serves sections of the checked-in docs *and* facts agents record during runs, so knowledge accumulates instead of dying with each session. See `context/context-engine.md`.
- **Issue ledger**: Tracks which issue is assigned to which workspace. Agents claim issues atomically before starting work, so parallel planning agents don't duplicate effort. Status lifecycle: `open` → `assigned` → `in_progress` → `review` → `merged` → `closed`. Available via REST API (`/api/issues/*`) and MCP tools (`assign_issue`, `unassign_issue`, `update_issue_status`, `list_issues`).

# Distributed Compute

Runs can execute on other machines. One **scheduler** owns the database, the queue and the root repos; **workers** dial out, lease runs, execute them locally and report back. Design and rationale: `context/distributed-execution.md`

## Scheduler

```ini
# ~/.config/takt/env — mode 0600, NOT in the repo.
# config/takt.yaml is tracked in a public repo; a token
# there would be published. systemd loads this file via
# EnvironmentFile, and lib.config.load_env_file() lets the
# client tools find it without it being in every shell.
TAKT_API_HOST=0.0.0.0
TAKT_API_TOKEN=<secret>
```

```yaml
# config/takt.yaml — non-secret settings only
state_dir: /var/lib/takt # optional; default is .state/ in the checkout
```

The service refuses to bind a non-loopback address without a token set — the API creates workspaces and triggers runs, so unauthenticated and reachable must never both be true.

Clients resolve the scheduler through `lib.config.api_base_url()` and `api_token()`, which read the real environment first, then the env file, then `takt.yaml`. Once a token is set it applies to loopback too: a TLS reverse proxy terminates on loopback, so exempting it would be an unauthenticated way in. `tests/conftest.py` points `TAKT_ENV_FILE` at a non-existent path for every test, so the suite never picks up the operator's live token.

## Agent steps (`lib/agent_runner.py`)

Scheduler-side agent steps run through **`claude-agent-sdk`** (the old `claude-code-sdk` is abandoned at 0.0.25). The SDK does not talk to the API — it locates the `claude` binary and drives it over stream-json — so the CLI is the real requirement everywhere, and a worker uses it directly (`lib/worker.py`).

Two things about that SDK are load-bearing and easy to undo by accident; both have tests that fail loudly:

- **`system_prompt` must be set to the `claude_code` preset.** Leaving it unset does *not* mean "the default": this SDK sends `--system-prompt ""`, which starts the agent with no system prompt at all. The predecessor omitted the flag when falsy, so the CLI applied its own — meaning a naive port silently strips Claude Code's behaviour from every pipeline agent, with nothing failing.
- **`cli_path` is set to PATH's `claude`.** The SDK bundles its own CLI (~292 MB) and prefers it, which would have the scheduler on a different Claude Code version than workers — they invoke PATH's binary and declare `agent_steps` from `shutil.which("claude")`. `TAKT_CLAUDE_CLI` overrides; the bundled copy is the fallback for a machine with no CLI installed. The runner logs which binary it used.

The old monkey-patch over the SDK's private `message_parser` is gone: it existed for `rate_limit_event` (emitted once per run), which this SDK parses natively as `RateLimitEvent`. `serialize_sdk_message` ignores unknown types, so it costs no output line.

## Provisioning a worker (`lib/provision.py`)

```bash
takt provision laptop --scheduler http://10.0.1.33:7433   # all managed repos
takt provision laptop --repos OTC.SDK OTC.Relay           # a subset
takt provision laptop --check                             # checks only
```

Run **from the scheduler**. It installs takt on the machine (HTTPS clone of the public repo), writes `~/.config/takt/env` at mode 0600, creates `~/takt-worker{,/mirrors}` and then **pushes** the root repos across as bare mirrors.

The push direction is the point. `--upstream scheduler` has the worker *fetch*, which needs an unlocked key on the worker — the usual blocker on a laptop whose key is passphrase-protected. Provisioning uses the SSH connection the scheduler already has, so a worker can be fully stocked while holding no git credentials at all. Re-running is the refresh path: git sends only what the far side lacks. To make that usable, `ensure_mirror()` treats a failed refresh of an *existing* mirror as a warning — `materialise()` is the real gate, and it fails precisely if the needed commit is absent.

`check_channel()` runs first and on its own. Anything writing to stdout on login (an sshd `Banner`, a `~/.ssh/rc` that echoes, a shell rc greeting — for zsh that is `~/.zshenv`, which non-interactive shells also read) is spliced in front of every command's output, and git/rsync then fail with "protocol incompatibility" or "is your shell clean?", naming neither the file nor the machine. The check names them.

A loopback `--scheduler` is refused: on the worker it would mean the worker itself.

## Worker (`bin/takt_worker.py`)

```bash
# LAN worker, fetching root repos from the scheduler over SSH
takt worker --id build-01 \
  --scheduler https://takt:7433 --token <secret> \
  --upstream scheduler --scheduler-host karl@takt

# Cloud worker, no route back, fetches from GitHub
takt worker --id cloud-01 \
  --scheduler https://takt.example.com --token <secret> \
  --upstream github

# Build-only machine (no Claude Code): never handed agent steps
takt worker --id build-02 ... --no-agent-steps

# Agent steps on a worker, under their own Claude account
takt worker --id agent-01 ... \
  --claude-home ~/accounts/worker-a
```

**Agent steps** run the `claude` CLI as a subprocess (`-p --output-format stream-json --verbose`), with the prompt the *scheduler* rendered — role templates stay authoritative in one place. A worker declares `agent_steps` from whether `claude` is on PATH, and is never handed an agent run if it isn't. `--claude-home` sets `CLAUDE_CONFIG_DIR`: limits attach to the **account**, not the machine, so give each worker its own account or extra machines add no agent throughput.

Outbound only, so a NAT'd cloud VM needs no inbound access. Each worker keeps its own mirrors (a git worktree cannot live on a different machine from its repo) and materialises worktrees at the exact SHA the run recorded. Commits produced come back as a git bundle over the same connection — workers only ever *fetch*, so the operator still gates what reaches GitHub. `push_to_github` and `create_pr` are refused on a worker and reported skipped.

Claims are leases with a generation: a worker that goes dark long enough to lose its lease is refused when it returns, rather than reporting over whoever took the run. `--disk-floor-mb` stops a worker accepting work when it is nearly full, and worktrees are torn down at run completion.

Inspect with `GET /api/workers`.

# Config Files

- `config/repos.yaml` — managed repo registry with push order
- `config/targets.yaml` — target inventory (VMs + hardware)
- `templates/` — CLAUDE.md templates and role snippets
- `context/` — architecture and decision docs

# Building Repos

Build instructions: `context/building-repos.md`

# Setup

New workstation setup: `context/workstation-setup.md`
