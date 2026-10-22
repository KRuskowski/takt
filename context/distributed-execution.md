# Distributed Execution — Design

Status: **proposal, not implemented.** Written 2026-10-22. Decides how takt stops being limited to one machine's compute — LAN now, cloud later.

## The problem

takt is single-host by construction. One `takt-service` owns the database, the root repos, the worktrees and the executor, and every pipeline step — agent and script alike — is a local process of that service. `max_agents` caps concurrent runs, which throttles contention rather than adding capacity.

The consequence is structural, not situational: **available compute is whatever the box running the service happens to have.** That holds regardless of which workstation that is or what else is running at the time. Builds, test suites, VM verification and agents all compete for one set of cores, one memory budget and one IO path, and the only control is to do less at once.

For scale, a representative workstation: 24 cores and 62 GB RAM against 230 workspaces and 23 root repos totalling 6.3 GB, OTC.Relay alone being 4.0 GB. Disk capacity is not usually the binding constraint — contention for cores, memory and IO is.

### What distribution does not fix

Claude rate and usage limits attach to the **account**, not the machine. Running agents on ten workers does not raise how much model work can happen concurrently. More machines help CPU-bound work — compiles, test suites, VM verification — and nothing else.

Worth fixing independently: `config/takt.yaml` models multiple accounts with a per-account `claude_home`, but **the pipeline executor never reads it**. Only `bin/takt_agent.py` and the usage/accounts endpoints do. `AgentInfo` carries no account field and `AgentRunner` sets no `CLAUDE_CONFIG_DIR`, so pipeline agents run under whatever ambient `~/.claude` the service inherited. If agent throughput becomes the ceiling, per-run account assignment is the lever — and it is a single-host change.

## Three roles currently collapsed into one machine

This is the core of the redesign. takt conflates three things that have no reason to be the same host:

1. **Where you work** — the interactive session driving takt through MCP.
2. **Where takt schedules** — the durable owner of the database, the queue and the root repos.
3. **Where work executes** — the compute.

Separating them is what makes compute poolable, and it also means moving between workstations stops implying moving takt. The scheduler becomes a machine chosen for durability and disk; workstations become clients; compute becomes a pool that any of them can draw on.

Role 1 is **already decoupled and unused**. `bin/takt_mcp.py` reads `TAKT_API_URL` (default `http://127.0.0.1:7433`), so a Claude session on any machine can already drive a remote takt. Two things block it:

- `lib/service.py:209` binds the HTTP API to `127.0.0.1`, deliberately, so nothing off-box can reach it.
- There is no authentication. The API assumes a trusted local caller, and every endpoint is unauthenticated — including the ones that create workspaces and trigger runs.

So exposing the scheduler needs a bind address, a token or mTLS, and a decision about what a client is allowed to do. That is a prerequisite for this design rather than part of it, and it is small.

Also note `STATE_DIR = PROJECT_DIR / ".state"` — the database lives inside the checkout. For a durable scheduler node that should be configurable, so the state is not tied to where the repo happens to be cloned.

## Hard constraints

Properties of the current design, not preferences. Each rules out an otherwise obvious approach.

1. **Run worktrees are bound to the root repo's filesystem.** `create_run_worktrees()` calls `git -C <root_repo> worktree add`, and a worktree's `.git` is a *file* pointing into the parent repo's git directory. A remote machine cannot host a worktree of the scheduler's mirror. Any worker that materialises source needs its own clone locally.
2. **SQLite is not a network database.** WAL-mode SQLite does not lock correctly over NFS/SMB. The database stays on the scheduler; workers reach it only through the API.
3. **Target locks are local files.** `.locks/<name>.lock` is visible only to processes on that host, so target claiming is not safe across machines as it stands.
4. **Workspace clones use `~/dev/root/<repo>` as origin.** A worker has no path to that, so "push to origin" means something different on a worker.
5. **Agents need an authenticated Claude Code**, installed per worker with its own `claude_home`.

## Requirements

- LAN machines first, cloud later, without a redesign in between.
- Cloud instances have no route back into the network, so **workers dial out**. No inbound SSH, no VPN assumption, no port forwarding.
- Workers are ephemeral and untrusted with state: any of them can vanish at any time.
- The "agents never push to GitHub; the operator gates what reaches GitHub" invariant survives.
- Heterogeneous machines: Debian and Windows, varying cores, varying toolchains.

## Design

A scheduler node owns the database, the queue and the root repos. Workers connect **outbound** and pull work. Workstations are thin clients over the API.

Pull rather than push is forced by the cloud requirement, and it reuses what exists: the service already runs a ZMQ ROUTER for commands and a PUB socket for broadcasts, and `lib/protocol.py` already serialises agent messages into line records. A worker is a DEALER client speaking a few new commands.

### The two kinds of step

The useful split is not "easy versus hard" but **whether a step mutates git history**.

- **Steps that do not** — builds, test suites, static checks, VM verification — need source in, and a pass/fail plus logs out. They are the CPU and RAM hogs, and they are trivially distributable because nothing has to come back but a result.
- **Steps that do** — agent steps — produce commits that must reach the scheduler's root repo. That is the hard part.

This split defines the staging, and it means the cheapest stage is the one that relieves the actual pressure.

### Unit of placement

**Stage 1 — step offload.** The scheduler keeps the run and its worktree. A step declaring `runs_on: <target>` ships source to that target, runs the command over SSH, streams output back, collects the result. Only non-git-mutating steps qualify. No new daemon; reuses `lib/ssh_utils.py` and the target inventory.

**Stage 2 — run lease.** A whole run is leased to one worker, which creates the worktrees locally and runs every step. The worktree is built once, which matters when a repo is 4 GB. The cost is that the worker must satisfy every requirement in the run, including Claude availability if any step is an agent.

Run-level leasing is right for stage 2: step-level leasing within a run would mean materialising a multi-gigabyte worktree on several machines to service one run.

### Getting source to a worker

A run already records exact refs. `runs.head_refs_json` is a map of `"<repo>:<branch>"` to commit SHA, produced by `snapshot_all_refs()`. The scheduler hands a worker that set, and the worker's job is to make those exact commits present locally. Deterministic, with no "whatever HEAD was" ambiguity — and it means a requeued run materialises the same source as the original attempt.

Each worker keeps its own mirrors. Where it fetches from is per-worker config, because the answer differs by location:

- **LAN worker**: fetch from the scheduler over SSH (`git clone --mirror <scheduler>:~/dev/root/<repo>`). Fast locally, and the scheduler stays the source of truth.
- **Cloud worker**: fetch from GitHub. There is no route to the scheduler.

Fetching from GitHub does **not** break the invariant. The invariant concerns *push* — agents must not push to GitHub and the operator gates what goes up. Workers only fetch. Pushing to GitHub stays a scheduler-side script step.

### Getting commits back

A worker running an agent step produces commits in its local clone, and they must reach the scheduler's root repo. On a LAN the worker could push over SSH; a cloud worker cannot be reached and must not push to GitHub.

**Return commits as git bundles over the worker's existing outbound connection.** The worker runs `git bundle create` for the range the run produced, sends it up the channel it already holds, and the scheduler unbundles into the root repo. Bundles are git's own primitive for moving commits without a reachable remote. Identical on LAN and cloud, needs no inbound connectivity, and keeps the invariant intact — the scheduler remains the only thing that talks to GitHub.

Bundle the produced range (`base..head`), never the whole history.

### Leases, heartbeats, requeue

A worker that loses power mid-run must not strand that run in `running` forever, nor have its work silently duplicated.

- Work is claimed by taking a **lease** with an expiry, not by flipping a status flag.
- The worker renews the lease with a heartbeat while working.
- The scheduler sweeps expired leases and returns those runs to `queued`.
- A requeued run starts from a fresh worktree rather than resuming mid-step. Simpler to reason about, and correct even if the original worker comes back.
- A worker reappearing after its lease expired must discover that and discard its results rather than report them. A lease generation number in the claim reply makes this checkable.

### Capability matching

Targets already carry `os`. Workers should advertise more: OS, cores, RAM, installed toolchains, and whether an authenticated Claude Code is present. Steps declare requirements; the scheduler matches. A run no online worker can satisfy stays queued with a visible reason rather than failing obscurely.

### Schema changes

```sql
CREATE TABLE workers (
  id TEXT PRIMARY KEY,              -- stable name, e.g. 'build-lan-01'
  host TEXT NOT NULL DEFAULT '',
  os TEXT NOT NULL DEFAULT '',
  cores INTEGER,
  ram_mb INTEGER,
  capabilities_json TEXT NOT NULL DEFAULT '{}',
  state TEXT NOT NULL DEFAULT 'online',  -- online|draining|offline
  last_seen TEXT,
  registered_at TEXT NOT NULL DEFAULT
    (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- Lease columns on runs, alongside the existing status fields.
ALTER TABLE runs ADD COLUMN worker_id TEXT;
ALTER TABLE runs ADD COLUMN lease_generation INTEGER NOT NULL DEFAULT 0;
ALTER TABLE runs ADD COLUMN lease_expires_at TEXT;

-- Target locks move out of .locks/*.lock so claiming works
-- across hosts. Same atomic-claim pattern as the issue ledger.
CREATE TABLE target_locks (
  target TEXT PRIMARY KEY,
  workspace TEXT,
  worker_id TEXT,
  claimed_at TEXT NOT NULL DEFAULT
    (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  expires_at TEXT
);
```

### New ZMQ commands

Added to `_cmd_handlers` in `lib/service.py`:

| Command | Purpose |
|---------|---------|
| `worker_register` | Announce identity and capabilities; upsert into `workers` |
| `worker_lease` | Request work matching capabilities; returns a run, its refs and a lease generation, or nothing |
| `worker_heartbeat` | Renew the lease; doubles as the liveness signal for `last_seen` |
| `worker_report_step` | Stream step status and output; rejected if the lease generation is stale |
| `worker_deliver` | Upload a git bundle of produced commits |
| `worker_release` | Finish or abandon a run, with a reason |

Step output already has a serialisation format and a persistence path (`agent_output`, `record_output`), so worker output reuses both rather than inventing a second channel.

## Staging

**Stage 0 — make the scheduler addressable.** Configurable bind address, an auth token on the API, and a configurable `STATE_DIR`. Small, and a prerequisite for everything else. On its own it already buys something useful: working from any workstation against one takt.

**Stage 1 — remote script steps.** `runs_on: <target>` for non-git-mutating steps, plus the target-lock migration into SQLite. Delivers the compute win without a new daemon, and does not foreclose stage 2.

A caveat on the existing dev VMs: `dev-01..03` run on the same host under KVM, so moving a build into one adds no cores. They help only with RAM isolation and blast-radius, not raw CPU. Real relief needs a second physical machine.

**Stage 2 — worker pool.** `bin/takt_worker.py`, run leases, heartbeats, requeue, capability matching, bundle delivery. Prove on LAN where a failing worker can be inspected, then extend to cloud by changing only a worker's fetch upstream.

**Stage 3 — elastic cloud.** On-demand provisioning and shutdown so instances are not paid for while idle. `bin/clone_vm.py` is libvirt-specific and will not transfer; this needs its own provider path. Out of scope until stage 2 is in use.

## Rejected alternatives

- **SQLite on a shared filesystem.** WAL locking is unreliable over NFS/SMB; invites silent corruption under concurrency.
- **Worktrees on a shared filesystem.** Sidesteps the git-worktree constraint, but git over a network filesystem is slow and lock-prone, and cannot work for cloud at all.
- **Push-only SSH to workers.** Simpler on a LAN, but needs inbound reachability that cloud instances behind NAT cannot offer. Would have to be replaced rather than extended.
- **Workers pushing results to GitHub.** Breaks the operator-gates-GitHub invariant. Bundles achieve the same transfer without it.
- **Raising `max_agents`.** Already available, and the wrong lever: it adds contention on one box rather than capacity.

## Open questions

- Must a retried run pin to the same worker, or is a fresh worktree on any eligible worker always acceptable? Decides whether retries can be opportunistic.
- Bundle size for a long agent step on a large repo is unmeasured. If it is big, delivery needs chunking rather than one message.
- If two workers authenticate as the same Claude account they share one quota — which argues for pinning agent steps to a few workers and treating the rest as build capacity only.
- Windows workers: the agent and script paths assume POSIX shell semantics in places. Needs an audit before a Windows box takes anything beyond SSH command steps.
- Does the scheduler node also serve as a worker? Convenient while there is only one spare machine, but it reintroduces the contention this design exists to remove.
