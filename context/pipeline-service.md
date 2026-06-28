# Pipeline Architecture: takt-service

## Overview

Pipeline agents run as a persistent background service (`takt-service`) that survives TUI disconnects. The TUI attaches/detaches freely — agent output is persisted and replayed on reconnect.

## Architecture

```
takt-service (asyncio event loop)
├── PipelineWatcher: poll_once() on timer
├── AgentExecutor: runs agents via AgentRunner
├── AgentStore: persists info + output to .state/agents/
└── ZMQ sockets
    ├── ROUTER (ipc://.state/takt-cmd.sock) — commands
    └── PUB    (ipc://.state/takt-pub.sock) — broadcasts

TUI client (attach/detach at will)
├── DEALER (connects to ROUTER) — commands
└── SUB    (connects to PUB) — events
```

## Service Lifecycle

```bash
# Install and start
systemctl --user enable takt-service
systemctl --user start takt-service

# View logs
journalctl --user -u takt-service -f

# Restart
systemctl --user restart takt-service

# Run a single poll cycle (no service needed)
bin/pipeline_watch.py --once
```

The TUI settings tab has Start/Stop/Restart buttons.

## IPC Protocol (ZMQ / pyzmq)

Two socket pairs:

1. **ROUTER/DEALER** — request/reply commands. Service binds ROUTER on `ipc://.state/takt-cmd.sock`. TUI connects DEALER.

2. **PUB/SUB** — broadcast events. Service binds PUB on `ipc://.state/takt-pub.sock`. TUI connects SUB with topic filtering.

### Commands (DEALER -> ROUTER -> DEALER)

```json
{"cmd": "ping"}
{"cmd": "list_agents"}
{"cmd": "replay_output", "agent_id": "ws/role",
 "from_line": 0}
{"cmd": "launch_agent", "agent_id": "ws/role",
 "prompt": "...", "cwd": "/path", "model": "sonnet",
 "workspace": "ws", "role": "role"}
{"cmd": "cancel_agent", "agent_id": "ws/role"}
{"cmd": "trigger_stage", "workspace": "ws", "role": "test"}
{"cmd": "poll_now"}
```

Replies: `{"status": "ok", "data": {...}}` or `{"status": "error", "message": "..."}`.

### PUB Topics

```
agent.update       {"agent_id", "state", "cost", ...}
agent.output.<id>  {"agent_id", "line_no", "kind",
                     "content", "meta"}
pipeline.event     {"time", "stage", "repos", "event"}
```

## Per-Step Model Selection

Each agent step stores its model in `config_json`:
```json
{"model": "sonnet"}
```

Valid values: `sonnet`, `opus`, `haiku`. Default: `sonnet`.

Data flow:
1. Pipeline tab writes model to `pipeline_steps.config_json`
2. `db.create_run` copies `config_json` to `steps` table
3. `PipelineExecutor.run_agent_step` reads `config.model`, passes to `AgentInfo(model=...)`
4. `AgentRunner` passes model to `ClaudeCodeOptions`
5. `list_agents` maps short names to full IDs for display (sonnet → claude-sonnet-4-6, etc.)

## Output Persistence

Agent output persisted to `.state/agents/<id>/output.jsonl` (append-only JSONL). Agent metadata in `.state/agents/<id>/info.json`. On TUI connect, `replay_output` returns stored lines; live output publishes to `agent.output.<id>`.

Output line format:
```json
{"line_no": 0, "kind": "text", "content": "...", "meta": {}}
```

Kinds: `text`, `tool_use`, `tool_result`, `thinking`, `result`, `system`, `error`.

## Stage Triggers

When a stage receives a push, the watcher:
1. Reads `.pipeline-push` marker files
2. Builds a trigger prompt with commit log
3. Launches an agent via AgentExecutor
4. Deletes marker files after launching

## Upstream Sync

When a root repo's default branch advances, the watcher writes `.upstream-sync` markers in every active workspace that uses the repo. On the next poll, a sync agent merges upstream into the workspace branch.

Marker location: `~/dev/workspaces/<ws>/<repo>/.upstream-sync`

## Stage Repo Lockdown

Stage repos have origin configured as push-only:
- Fetch URL: `/dev/null` (blocks `git fetch/pull origin`)
- Push URL: next stage or root repo (normal push works)
- Incoming changes arrive via post-receive hook (`receive.denyCurrentBranch=updateInstead`)

## Key Files

- `lib/service.py` — TaktService orchestrator
- `lib/service_client.py` — TUI-side ZMQ client
- `lib/protocol.py` — SDK message serialization
- `lib/agent_store.py` — output/info persistence
- `bin/takt_service.py` — service entry point
- `bin/pipeline_watch.py` — reusable poll functions
- `config/takt-service.service` — systemd unit
