"""takt-service — persistent background service.

Orchestrates pipeline execution via SQLite state and
ZMQ IPC. Polls root repos for changes, creates runs,
and executes pipeline steps sequentially.

Architecture:
  PipelineExecutor: runs steps in worktrees.
  SQLite (lib.db): all state — runs, steps, events.
  ZMQ ROUTER: request/reply commands from TUI clients.
  ZMQ PUB: broadcasts step updates, output, events.
"""

import asyncio
import json
import logging
import subprocess
import time

import zmq
import zmq.asyncio
from aiohttp import web

from lib import db
from lib.api import broadcast_to_sse, build_app
from lib.config import (
  STATE_DIR,
  get_repo_path,
  load_repos_config,
  load_takt_config,
)
from lib.pipeline import (
  PipelineExecutor,
  find_changes,
  group_by_branch,
  snapshot_all_refs,
)
from lib.workspace_ops import (
  add_repo_to_workspace,
  create_workspace,
  delete_workspace,
  list_workspaces,
)

log = logging.getLogger("takt.service")

DEFAULT_CMD_ADDR = f"ipc://{STATE_DIR}/takt-cmd.sock"
DEFAULT_PUB_ADDR = f"ipc://{STATE_DIR}/takt-pub.sock"
DEFAULT_INTERVAL = 30
DEFAULT_MAX_AGENTS = 4


class TaktService:
  """Background service orchestrating pipeline runs.

  Polls root repos for branch changes, creates pipeline
  runs in SQLite, executes them via PipelineExecutor.

  Attributes:
    interval: Poll interval in seconds.
    cmd_addr: ZMQ ROUTER bind address.
    pub_addr: ZMQ PUB bind address.
    max_agents: Max concurrent pipeline runs.
  """

  def __init__(self, interval=DEFAULT_INTERVAL,
               cmd_addr=None, pub_addr=None,
               max_agents=DEFAULT_MAX_AGENTS,
               zmq_ctx=None, db_path=None,
               api_port=None):
    """Initialize the service.

    Args:
      interval: Poll interval in seconds.
      cmd_addr: ZMQ ROUTER bind address.
      pub_addr: ZMQ PUB bind address.
      max_agents: Max concurrent pipeline runs.
      zmq_ctx: Optional ZMQ context (for testing).
      db_path: Optional DB path (for testing).
      api_port: HTTP API port (0 to disable).
    """
    self.interval = interval
    self.cmd_addr = cmd_addr or DEFAULT_CMD_ADDR
    self.pub_addr = pub_addr or DEFAULT_PUB_ADDR
    self.max_agents = max_agents
    self._ctx = zmq_ctx or zmq.asyncio.Context()
    self._own_ctx = zmq_ctx is None
    self._db_path = db_path
    self._semaphore = asyncio.Semaphore(max_agents)
    self._run_tasks = {}  # run_id -> asyncio.Task
    self._meta_run_tasks = {}  # run_id -> asyncio.Task
    self._router = None
    self._pub = None
    self._running = False
    self._poll_task = None
    self._cmd_task = None
    self._http_app = None
    self._http_runner = None
    # Resolve API port.
    if api_port is not None:
      self._api_port = api_port
    else:
      cfg = load_takt_config()
      self._api_port = cfg.get("api_port", 7433)

  async def start(self, once=False):
    """Start the service event loop.

    Args:
      once: If True, run a single poll cycle and exit.
    """
    log.info(
      "Starting takt-service (interval=%ds, "
      "max_agents=%d)",
      self.interval, self.max_agents,
    )
    # Ensure DB schema is current.
    db.migrate(db_path=self._db_path)
    self._router = self._ctx.socket(zmq.ROUTER)
    self._router.bind(self.cmd_addr)
    self._pub = self._ctx.socket(zmq.PUB)
    self._pub.bind(self.pub_addr)
    self._running = True
    # Warm GPG/SSH caches so agents don't prompt.
    await self._warm_caches()
    if once:
      await self._poll_once()
    else:
      self._cmd_task = asyncio.create_task(
        self._cmd_loop()
      )
      self._poll_task = asyncio.create_task(
        self._poll_loop()
      )
      # Start HTTP API server.
      if self._api_port:
        await self._start_http()
      await asyncio.gather(
        self._cmd_task, self._poll_task,
        return_exceptions=True,
      )

  async def stop(self):
    """Stop the service gracefully."""
    log.info("Stopping takt-service")
    self._running = False
    for run_id, task in list(self._run_tasks.items()):
      task.cancel()
    for run_id, task in list(
      self._meta_run_tasks.items()
    ):
      task.cancel()
    all_tasks = (
      list(self._run_tasks.values())
      + list(self._meta_run_tasks.values())
    )
    if all_tasks:
      await asyncio.gather(
        *all_tasks, return_exceptions=True,
      )
    if self._poll_task:
      self._poll_task.cancel()
    if self._cmd_task:
      self._cmd_task.cancel()
    if self._http_runner:
      await self._http_runner.cleanup()
    if self._router:
      self._router.close(linger=0)
    if self._pub:
      self._pub.close(linger=0)
    if self._own_ctx:
      self._ctx.term()

  async def _start_http(self):
    """Start the aiohttp REST + SSE server."""
    self._http_app = build_app(self)
    self._http_runner = web.AppRunner(self._http_app)
    await self._http_runner.setup()
    site = web.TCPSite(
      self._http_runner, "0.0.0.0", self._api_port,
    )
    await site.start()
    log.info(
      "HTTP API listening on port %d", self._api_port,
    )

  async def _warm_caches(self):
    """Warm GPG and SSH caches at startup."""
    import os
    loop = asyncio.get_running_loop()
    try:
      # Use the first secret key available.
      result = await loop.run_in_executor(
        None, lambda: subprocess.run(
          ["gpg", "--list-secret-keys", "--keyid-format",
           "long", "--with-colons"],
          capture_output=True, text=True, timeout=10,
        )
      )
      key_id = None
      for line in result.stdout.splitlines():
        if line.startswith("sec:"):
          key_id = line.split(":")[4]
          break
      if key_id:
        await loop.run_in_executor(None, lambda: (
          subprocess.run(
            ["gpg", "--sign", "--default-key",
             key_id, "-o", "/dev/null"],
            input=b"warmup",
            capture_output=True, timeout=60,
          )
        ))
      log.info("GPG cache warmed")
    except Exception as e:
      log.warning("GPG cache warmup failed: %s", e)
    sock = os.environ.get("SSH_AUTH_SOCK", "")
    if not sock:
      log.warning(
        "SSH_AUTH_SOCK not set — agents may prompt "
        "for SSH passphrases"
      )
    else:
      try:
        result = await loop.run_in_executor(
          None, lambda: subprocess.run(
            ["ssh-add", "-l"],
            capture_output=True, timeout=5,
          )
        )
        count = len(
          result.stdout.decode().strip().splitlines()
        )
        if result.returncode == 0 and count > 0:
          log.info(
            "SSH agent has %d key(s) loaded", count
          )
        else:
          log.warning(
            "SSH agent has no keys — run ssh-add"
          )
      except Exception as e:
        log.warning("SSH agent check failed: %s", e)

  # -- Command handling --

  async def _cmd_loop(self):
    """Read commands from ROUTER socket and dispatch."""
    while self._running:
      try:
        frames = await self._router.recv_multipart()
      except zmq.ZMQError:
        if not self._running:
          break
        raise
      if len(frames) < 3:
        continue
      identity = frames[0]
      try:
        payload = json.loads(frames[2])
      except (json.JSONDecodeError, IndexError):
        await self._send_reply(
          identity, "error", message="invalid JSON"
        )
        continue
      cmd = payload.get("cmd")
      handler = self._cmd_handlers.get(cmd)
      if handler is None:
        await self._send_reply(
          identity, "error",
          message=f"unknown command: {cmd}",
        )
        continue
      try:
        result = await handler(self, payload)
        await self._send_reply(
          identity, "ok", data=result
        )
      except Exception as e:
        log.error(
          "Command %s failed: %s", cmd, e,
          exc_info=True,
        )
        await self._send_reply(
          identity, "error", message=str(e)
        )

  async def _send_reply(self, identity, status, **kwargs):
    """Send a reply to a DEALER client.

    Args:
      identity: Client identity frame.
      status: "ok" or "error".
      **kwargs: Additional reply fields.
    """
    reply = {"status": status}
    reply.update(kwargs)
    await self._router.send_multipart([
      identity, b"", json.dumps(reply).encode()
    ])

  # -- Command handlers --

  async def _handle_ping(self, payload):
    """Handle ping command."""
    return {"pong": True}

  async def _handle_list_runs(self, payload):
    """Handle list_runs command."""
    workspace = payload.get("workspace")
    limit = payload.get("limit", 20)
    runs = db.list_runs(
      workspace, limit, db_path=self._db_path,
    )
    return {"runs": runs}

  async def _handle_get_run_detail(self, payload):
    """Handle get_run_detail command."""
    run_id = payload["run_id"]
    run = db.get_run(run_id, db_path=self._db_path)
    if run is None:
      raise ValueError(f"Run {run_id} not found")
    steps = db.get_run_steps(
      run_id, db_path=self._db_path,
    )
    return {"run": run, "steps": steps}

  async def _handle_get_step_detail(self, payload):
    """Handle get_step_detail command."""
    step_id = payload["step_id"]
    step = db.get_step(step_id, db_path=self._db_path)
    if step is None:
      raise ValueError(f"Step {step_id} not found")
    events = db.get_events(
      entity="step", entity_id=step_id,
      db_path=self._db_path,
    )
    return {"step": step, "events": events}

  async def _handle_replay_output(self, payload):
    """Handle replay_output command."""
    step_id = payload["step_id"]
    from_line = payload.get("from_line", 0)
    lines = db.get_output(
      step_id, from_line, db_path=self._db_path,
    )
    return {"lines": lines, "step_id": step_id}

  async def _handle_get_events(self, payload):
    """Handle get_events command."""
    entity = payload.get("entity")
    entity_id = payload.get("entity_id")
    limit = payload.get("limit", 50)
    events = db.get_events(
      entity, entity_id, limit,
      db_path=self._db_path,
    )
    return {"events": events}

  async def _handle_trigger_run(self, payload):
    """Handle trigger_run command — manual run trigger."""
    workspace = payload["workspace"]
    pipeline = db.get_pipeline(
      workspace, db_path=self._db_path,
    )
    if not pipeline:
      raise ValueError(
        f"No pipeline defined for {workspace}"
      )
    # Gather repos and refs.
    workspaces = list_workspaces()
    ws_info = next(
      (w for w in workspaces if w["name"] == workspace),
      None,
    )
    repos = ws_info["repos"] if ws_info else []
    refs = self._snapshot_workspace_refs(workspace, repos)
    run_id = db.create_run(
      workspace, "manual", repos, refs,
      db_path=self._db_path,
    )
    if run_id is None:
      raise ValueError("Duplicate trigger")
    # Launch executor.
    self._launch_run(run_id)
    return {"run_id": run_id}

  async def _handle_cancel_run(self, payload):
    """Handle cancel_run command."""
    run_id = payload["run_id"]
    task = self._run_tasks.get(run_id)
    if task:
      task.cancel()
    else:
      # Mark as cancelled in DB directly.
      run = db.get_run(run_id, db_path=self._db_path)
      if run and run["status"] in ("queued", "running"):
        with db._connect(self._db_path) as conn:
          conn.execute(
            "UPDATE runs SET status = 'cancelled', "
            "finished_at = strftime("
            "'%Y-%m-%dT%H:%M:%fZ','now') "
            "WHERE id = ?",
            (run_id,),
          )
        db.log_event(
          "run", run_id, run["status"], "cancelled",
          "operator cancelled", db_path=self._db_path,
        )
    return {"run_id": run_id}

  async def _handle_pause_step(self, payload):
    """Handle pause_step command."""
    step_id = payload["step_id"]
    db.advance_step(
      step_id, "paused", reason="operator paused",
      db_path=self._db_path,
    )
    return {"step_id": step_id}

  async def _handle_resume_step(self, payload):
    """Handle resume_step command."""
    step_id = payload["step_id"]
    db.advance_step(
      step_id, "queued", reason="operator resumed",
      db_path=self._db_path,
    )
    return {"step_id": step_id}

  async def _handle_retry_step(self, payload):
    """Handle retry_step command."""
    step_id = payload["step_id"]
    db.advance_step(
      step_id, "queued", reason="operator retry",
      db_path=self._db_path,
    )
    return {"step_id": step_id}

  async def _handle_skip_step(self, payload):
    """Handle skip_step command."""
    step_id = payload["step_id"]
    db.advance_step(
      step_id, "skipped", reason="operator skipped",
      db_path=self._db_path,
    )
    return {"step_id": step_id}

  async def _handle_list_agents(self, payload):
    """Handle list_agents — return recent agent steps."""
    limit = payload.get("limit", 50)
    rows = db.list_agent_steps(
      limit=limit, db_path=self._db_path,
    )
    model_map = {
      "sonnet": "claude-sonnet-4-6",
      "opus": "claude-opus-4-6",
      "haiku": "claude-haiku-4-5",
    }
    agents = []
    for row in rows:
      agent_id = f"run-{row['run_id']}/{row['name']}"
      config = json.loads(row.get("config_json", "{}"))
      short = config.get("model", "sonnet")
      model = model_map.get(short, short)
      agents.append({
        "agent_id": agent_id,
        "step_id": row["id"],
        "workspace": row["workspace"],
        "role": row["name"],
        "model": model,
        "state": row["status"],
        "num_turns": row.get("num_turns", 0),
        "total_cost_usd": row.get("cost_usd", 0),
        "run_id": row["run_id"],
      })
    return {"agents": agents}

  async def _handle_cancel_agent(self, payload):
    """Handle cancel_agent — cancel via parent run."""
    agent_id = payload["agent_id"]
    # Parse run_id from agent_id format "run-N/role".
    try:
      run_part = agent_id.split("/")[0]
      run_id = int(run_part.replace("run-", ""))
    except (ValueError, IndexError):
      raise ValueError(
        f"Invalid agent_id format: {agent_id}"
      )
    return await self._handle_cancel_run(
      {"run_id": run_id}
    )

  async def _handle_poll_now(self, payload):
    """Handle poll_now command — run one poll cycle."""
    events = await self._poll_once()
    return {"events": events}

  # -- Meta agent command handlers --

  async def _handle_list_meta_agents(self, payload):
    """Handle list_meta_agents command."""
    agents = db.list_meta_agents(
      db_path=self._db_path,
    )
    return {"agents": agents}

  async def _handle_get_meta_agent(self, payload):
    """Handle get_meta_agent command."""
    agent_id = payload["meta_agent_id"]
    agent = db.get_meta_agent(
      agent_id, db_path=self._db_path,
    )
    if agent is None:
      raise ValueError(
        f"Meta agent {agent_id} not found"
      )
    return {"agent": agent}

  async def _handle_create_meta_agent(self, payload):
    """Handle create_meta_agent command."""
    aid = db.create_meta_agent(
      name=payload["name"],
      description=payload.get("description", ""),
      prompt=payload.get("prompt", ""),
      model=payload.get("model", "sonnet"),
      timeout_secs=payload.get("timeout_secs", 1800),
      config=payload.get("config"),
      db_path=self._db_path,
    )
    return {"meta_agent_id": aid}

  async def _handle_update_meta_agent(self, payload):
    """Handle update_meta_agent command."""
    agent_id = payload["meta_agent_id"]
    fields = {
      k: v for k, v in payload.items()
      if k != "cmd" and k != "meta_agent_id"
    }
    db.update_meta_agent(
      agent_id, db_path=self._db_path, **fields,
    )
    return {"meta_agent_id": agent_id}

  async def _handle_delete_meta_agent(self, payload):
    """Handle delete_meta_agent command."""
    agent_id = payload["meta_agent_id"]
    db.delete_meta_agent(
      agent_id, db_path=self._db_path,
    )
    return {"meta_agent_id": agent_id}

  async def _handle_run_meta_agent(self, payload):
    """Handle run_meta_agent command."""
    agent_id = payload["meta_agent_id"]
    agent = db.get_meta_agent(
      agent_id, db_path=self._db_path,
    )
    if agent is None:
      raise ValueError(
        f"Meta agent {agent_id} not found"
      )
    run_id = db.create_meta_agent_run(
      agent_id, db_path=self._db_path,
    )
    self._launch_meta_run(run_id)
    return {"run_id": run_id}

  async def _handle_cancel_meta_run(self, payload):
    """Handle cancel_meta_run command."""
    run_id = payload["run_id"]
    task = self._meta_run_tasks.get(run_id)
    if task:
      task.cancel()
    else:
      run = db.get_meta_agent_run(
        run_id, db_path=self._db_path,
      )
      if run and run["status"] in ("queued", "running"):
        db.advance_meta_run(
          run_id, "cancelled",
          db_path=self._db_path,
        )
    return {"run_id": run_id}

  async def _handle_list_meta_runs(self, payload):
    """Handle list_meta_runs command."""
    agent_id = payload["meta_agent_id"]
    limit = payload.get("limit", 20)
    runs = db.list_meta_agent_runs(
      agent_id, limit, db_path=self._db_path,
    )
    return {"runs": runs}

  async def _handle_replay_meta_output(self, payload):
    """Handle replay_meta_output command."""
    run_id = payload["run_id"]
    from_line = payload.get("from_line", 0)
    lines = db.get_meta_output(
      run_id, from_line, db_path=self._db_path,
    )
    return {"lines": lines, "run_id": run_id}

  # -- Workspace management --

  async def _handle_create_workspace(self, payload):
    """Handle create_workspace — reply immediately,
    run in background."""
    name = payload["name"]
    repos = payload["repos"]
    chroot = payload.get("chroot", False)
    asyncio.ensure_future(
      self._bg_create_workspace(name, repos, chroot)
    )
    return {"workspace": name}

  async def _bg_create_workspace(self, name, repos,
                                 chroot):
    """Background task for workspace creation."""
    loop = asyncio.get_event_loop()
    try:
      await loop.run_in_executor(
        None,
        lambda: create_workspace(
          name, repos, chroot=chroot,
        ),
      )
      msg = f"Created workspace '{name}'."
      if chroot:
        msg += " (with chroot)"
      await self._publish(
        "workspace.event",
        {"action": "created", "name": name,
         "message": msg},
      )
    except Exception as e:
      log.error(
        "create_workspace failed: %s", e,
        exc_info=True,
      )
      await self._publish(
        "workspace.event",
        {"action": "error", "name": name,
         "message": str(e)},
      )

  async def _handle_delete_workspace(self, payload):
    """Handle delete_workspace — reply immediately,
    run in background."""
    name = payload["name"]
    asyncio.ensure_future(
      self._bg_delete_workspace(name)
    )
    return {"workspace": name}

  async def _bg_delete_workspace(self, name):
    """Background task for workspace deletion."""
    loop = asyncio.get_event_loop()
    try:
      await loop.run_in_executor(
        None, lambda: delete_workspace(name),
      )
      await self._publish(
        "workspace.event",
        {"action": "deleted", "name": name,
         "message": f"Deleted workspace '{name}'."},
      )
    except Exception as e:
      log.error(
        "delete_workspace failed: %s", e,
        exc_info=True,
      )
      await self._publish(
        "workspace.event",
        {"action": "error", "name": name,
         "message": str(e)},
      )

  async def _handle_add_repo(self, payload):
    """Handle add_repo — reply immediately,
    run in background."""
    name = payload["name"]
    repo = payload["repo"]
    asyncio.ensure_future(
      self._bg_add_repo(name, repo)
    )
    return {"workspace": name, "repo": repo}

  async def _bg_add_repo(self, name, repo):
    """Background task for adding a repo."""
    loop = asyncio.get_event_loop()
    try:
      await loop.run_in_executor(
        None,
        lambda: add_repo_to_workspace(name, repo),
      )
      await self._publish(
        "workspace.event",
        {"action": "repo_added", "name": name,
         "message": f"Added {repo} to '{name}'."},
      )
    except Exception as e:
      log.error(
        "add_repo failed: %s", e, exc_info=True,
      )
      await self._publish(
        "workspace.event",
        {"action": "error", "name": name,
         "message": str(e)},
      )

  _cmd_handlers = {
    "ping": _handle_ping,
    "list_runs": _handle_list_runs,
    "get_run_detail": _handle_get_run_detail,
    "get_step_detail": _handle_get_step_detail,
    "replay_output": _handle_replay_output,
    "get_events": _handle_get_events,
    "trigger_run": _handle_trigger_run,
    "cancel_run": _handle_cancel_run,
    "pause_step": _handle_pause_step,
    "resume_step": _handle_resume_step,
    "retry_step": _handle_retry_step,
    "skip_step": _handle_skip_step,
    "poll_now": _handle_poll_now,
    "list_agents": _handle_list_agents,
    "cancel_agent": _handle_cancel_agent,
    "list_meta_agents": _handle_list_meta_agents,
    "get_meta_agent": _handle_get_meta_agent,
    "create_meta_agent": _handle_create_meta_agent,
    "update_meta_agent": _handle_update_meta_agent,
    "delete_meta_agent": _handle_delete_meta_agent,
    "run_meta_agent": _handle_run_meta_agent,
    "cancel_meta_run": _handle_cancel_meta_run,
    "list_meta_runs": _handle_list_meta_runs,
    "replay_meta_output": _handle_replay_meta_output,
    "create_workspace": _handle_create_workspace,
    "delete_workspace": _handle_delete_workspace,
    "add_repo": _handle_add_repo,
  }

  # -- Pipeline execution --

  def _launch_run(self, run_id):
    """Launch a pipeline run as an asyncio task.

    Args:
      run_id: Run row ID.
    """
    if run_id in self._run_tasks:
      return

    async def _execute():
      async with self._semaphore:
        executor = PipelineExecutor(
          on_output=self._on_step_output,
          on_step_update=self._on_step_update,
          db_path=self._db_path,
        )
        try:
          status = await executor.execute_run(run_id)
          log.info(
            "Run %d finished: %s", run_id, status,
          )
        except Exception as e:
          log.error(
            "Run %d failed: %s", run_id, e,
            exc_info=True,
          )
        finally:
          self._run_tasks.pop(run_id, None)

    task = asyncio.create_task(_execute())
    self._run_tasks[run_id] = task

  def _on_step_output(self, step_id, lines):
    """Publish agent output lines via ZMQ and SSE.

    Args:
      step_id: Step row ID.
      lines: List of output line dicts.
    """
    for line in lines:
      topic = f"agent.output.step-{step_id}"
      try:
        self._pub.send_multipart(
          [topic.encode(), json.dumps(line).encode()],
          flags=zmq.NOBLOCK,
        )
      except zmq.ZMQError:
        pass
      if self._http_app:
        broadcast_to_sse(self._http_app, topic, line)

  def _on_step_update(self, step_id, status):
    """Publish step status change via ZMQ and SSE.

    Args:
      step_id: Step row ID.
      status: New status string.
    """
    data = {
      "step_id": step_id,
      "status": status,
      "time": time.strftime("%H:%M:%S"),
    }
    try:
      self._pub.send_multipart(
        [b"step.update", json.dumps(data).encode()],
        flags=zmq.NOBLOCK,
      )
    except zmq.ZMQError:
      pass
    if self._http_app:
      broadcast_to_sse(
        self._http_app, "step.update", data,
      )

  # -- Meta agent execution --

  def _launch_meta_run(self, run_id):
    """Launch a meta agent run as an asyncio task.

    Args:
      run_id: Meta agent run row ID.
    """
    if run_id in self._meta_run_tasks:
      return

    async def _execute():
      async with self._semaphore:
        run = db.get_meta_agent_run(
          run_id, db_path=self._db_path,
        )
        if run is None:
          return
        agent = db.get_meta_agent(
          run["meta_agent_id"],
          db_path=self._db_path,
        )
        if agent is None:
          return
        from lib.meta_runner import MetaAgentExecutor
        executor = MetaAgentExecutor(
          run_id, agent,
          on_output=self._on_meta_output,
          on_status_update=self._on_meta_status,
          db_path=self._db_path,
        )
        try:
          status = await executor.execute()
          log.info(
            "Meta run %d finished: %s",
            run_id, status,
          )
        except Exception as e:
          log.error(
            "Meta run %d failed: %s",
            run_id, e, exc_info=True,
          )
        finally:
          self._meta_run_tasks.pop(run_id, None)

    task = asyncio.create_task(_execute())
    self._meta_run_tasks[run_id] = task

  def _on_meta_output(self, run_id, lines):
    """Publish meta agent output lines via ZMQ and SSE.

    Args:
      run_id: Meta agent run row ID.
      lines: List of output line dicts.
    """
    for line in lines:
      topic = f"meta.output.run-{run_id}"
      try:
        self._pub.send_multipart(
          [topic.encode(),
           json.dumps(line).encode()],
          flags=zmq.NOBLOCK,
        )
      except zmq.ZMQError:
        pass
      if self._http_app:
        broadcast_to_sse(self._http_app, topic, line)

  def _on_meta_status(self, run_id, status):
    """Publish meta agent status change via ZMQ and SSE.

    Args:
      run_id: Meta agent run row ID.
      status: New status string.
    """
    data = {
      "run_id": run_id,
      "status": status,
      "time": time.strftime("%H:%M:%S"),
    }
    try:
      self._pub.send_multipart(
        [b"meta.update", json.dumps(data).encode()],
        flags=zmq.NOBLOCK,
      )
    except zmq.ZMQError:
      pass
    if self._http_app:
      broadcast_to_sse(
        self._http_app, "meta.update", data,
      )

  async def _publish(self, topic, data):
    """Publish a message on the PUB socket and SSE.

    Args:
      topic: Topic string.
      data: Dict to serialize as JSON.
    """
    try:
      await self._pub.send_multipart([
        topic.encode(),
        json.dumps(data).encode(),
      ])
    except zmq.ZMQError:
      log.debug(
        "Failed to publish %s", topic, exc_info=True
      )
    # Also broadcast to SSE clients.
    if self._http_app:
      broadcast_to_sse(self._http_app, topic, data)

  # -- Pipeline watcher --

  async def _poll_loop(self):
    """Poll for pipeline changes on a timer."""
    while self._running:
      try:
        await self._poll_once()
      except Exception:
        log.error(
          "Poll cycle failed", exc_info=True,
        )
      await asyncio.sleep(self.interval)

  async def _poll_once(self):
    """Run a single poll cycle.

    1. Fetch root repos from GitHub.
    2. Snapshot refs, detect changes.
    3. Create runs for workspace branch pushes.
    4. Launch queued runs.

    Returns:
      List of event dicts.
    """
    loop = asyncio.get_running_loop()
    events = await loop.run_in_executor(
      None, self._poll_sync
    )
    # Launch any queued runs.
    while True:
      queued = db.get_next_queued_run(
        db_path=self._db_path,
      )
      if queued is None:
        break
      if queued["id"] in self._run_tasks:
        break
      self._launch_run(queued["id"])
    for ev in events:
      await self._publish("pipeline.event", ev)
    return events

  def _poll_sync(self):
    """Synchronous poll — runs in executor thread.

    Fetches root repos, snapshots refs, detects changes,
    creates runs for workspace branches with pipelines.

    Returns:
      List of event dicts.
    """
    repos_config = load_repos_config()
    events = []
    now = time.strftime("%H:%M:%S")
    # Fetch all root repos from GitHub.
    self._fetch_all_root_repos(repos_config)
    # Snapshot refs.
    old_refs = db.load_refs(db_path=self._db_path)
    new_refs = snapshot_all_refs(repos_config)
    if not old_refs:
      log.info(
        "First run — snapshotted %d refs", len(new_refs)
      )
      db.save_refs(new_refs, db_path=self._db_path)
      return events
    changes = find_changes(old_refs, new_refs)
    if not changes:
      db.save_refs(new_refs, db_path=self._db_path)
      return events
    groups = group_by_branch(changes)
    log.info(
      "Detected changes in %d branch(es)", len(groups),
    )
    # Find workspace branches with pipelines.
    workspaces = list_workspaces()
    ws_names = {w["name"] for w in workspaces}
    for branch, branch_changes in groups.items():
      if branch not in ws_names:
        continue
      pipeline = db.get_pipeline(
        branch, db_path=self._db_path,
      )
      if not pipeline:
        continue
      repos = list({
        c["repo"] for c in branch_changes
        if c["type"] != "deleted"
      })
      if not repos:
        continue
      refs = {
        c["repo"]: c["new_ref"]
        for c in branch_changes
        if c["new_ref"]
      }
      run_id = db.create_run(
        branch, "push", repos, refs,
        db_path=self._db_path,
      )
      if run_id is not None:
        events.append({
          "time": now,
          "run_id": run_id,
          "workspace": branch,
          "repos": ", ".join(repos),
          "event": "run_created",
        })
    db.save_refs(new_refs, db_path=self._db_path)
    return events

  def _fetch_all_root_repos(self, repos_config):
    """Fetch all root repos from GitHub.

    Args:
      repos_config: Full repos config dict.
    """
    all_repos = repos_config.get("repos", {})
    for repo_name, cfg in all_repos.items():
      disk_path = cfg.get("path", repo_name)
      repo_path = get_repo_path(disk_path)
      if not repo_path.exists():
        bare = repo_path.parent / f"{repo_path.name}.git"
        if bare.exists():
          repo_path = bare
      if not repo_path.exists():
        continue
      try:
        subprocess.run(
          ["git", "-C", str(repo_path),
           "fetch", "--prune", "origin"],
          capture_output=True, timeout=60,
        )
      except Exception:
        log.debug(
          "Fetch %s failed", repo_name, exc_info=True,
        )

  def _snapshot_workspace_refs(self, workspace, repos):
    """Snapshot refs for workspace repos.

    Args:
      workspace: Workspace name (= branch).
      repos: List of repo names.

    Returns:
      Dict mapping repo to commit hash.
    """
    from lib.git_utils import get_branch_ref
    repos_config = load_repos_config().get("repos", {})
    refs = {}
    for repo in repos:
      cfg = repos_config.get(repo, {})
      repo_path = get_repo_path(cfg.get("path", repo))
      try:
        refs[repo] = get_branch_ref(repo_path, workspace)
      except Exception:
        pass
    return refs
