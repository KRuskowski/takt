"""takt-service — persistent background service.

Runs the pipeline watcher, agent executor, and ZMQ IPC
sockets in a single asyncio event loop.

Architecture:
  PipelineWatcher: poll_once() on asyncio timer.
  AgentExecutor: runs agents via AgentRunner as tasks.
  AgentStore: persists info + output to .state/agents/.
  ZMQ ROUTER: request/reply commands from TUI clients.
  ZMQ PUB: broadcasts agent updates, output, events.
"""

import asyncio
import json
import logging
import time

import zmq
import zmq.asyncio

from lib.agent_runner import AgentInfo, AgentRunner, AgentState
from lib.agent_store import AgentStore
from lib.config import STATE_DIR, load_repos_config
from lib.protocol import serialize_sdk_message

log = logging.getLogger("takt.service")

DEFAULT_CMD_ADDR = f"ipc://{STATE_DIR}/takt-cmd.sock"
DEFAULT_PUB_ADDR = f"ipc://{STATE_DIR}/takt-pub.sock"
DEFAULT_INTERVAL = 30
DEFAULT_MAX_AGENTS = 4


class TaktService:
  """Background service orchestrating pipeline and agents.

  Attributes:
    interval: Poll interval in seconds.
    cmd_addr: ZMQ ROUTER bind address.
    pub_addr: ZMQ PUB bind address.
    max_agents: Max concurrent agent tasks.
  """

  def __init__(self, interval=DEFAULT_INTERVAL,
               cmd_addr=None, pub_addr=None,
               max_agents=DEFAULT_MAX_AGENTS,
               zmq_ctx=None, store=None):
    """Initialize the service.

    Args:
      interval: Poll interval in seconds.
      cmd_addr: ZMQ ROUTER bind address.
      pub_addr: ZMQ PUB bind address.
      max_agents: Max concurrent agent tasks.
      zmq_ctx: Optional ZMQ context (for testing).
      store: Optional AgentStore (for testing).
    """
    self.interval = interval
    self.cmd_addr = cmd_addr or DEFAULT_CMD_ADDR
    self.pub_addr = pub_addr or DEFAULT_PUB_ADDR
    self.max_agents = max_agents
    self._ctx = zmq_ctx or zmq.asyncio.Context()
    self._own_ctx = zmq_ctx is None
    self._store = store or AgentStore()
    self._semaphore = asyncio.Semaphore(max_agents)
    self._agents = {}  # agent_id -> asyncio.Task
    self._agent_infos = {}  # agent_id -> AgentInfo
    self._agent_line_counts = {}  # agent_id -> int
    self._router = None
    self._pub = None
    self._running = False
    self._poll_task = None
    self._cmd_task = None

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
    self._router = self._ctx.socket(zmq.ROUTER)
    self._router.bind(self.cmd_addr)
    self._pub = self._ctx.socket(zmq.PUB)
    self._pub.bind(self.pub_addr)
    self._running = True
    # Restore agent infos from disk.
    for info in self._store.list_agents():
      self._agent_infos[info.agent_id] = info
    if once:
      await self._poll_once()
    else:
      self._cmd_task = asyncio.create_task(
        self._cmd_loop()
      )
      self._poll_task = asyncio.create_task(
        self._poll_loop()
      )
      await asyncio.gather(
        self._cmd_task, self._poll_task,
        return_exceptions=True,
      )

  async def stop(self):
    """Stop the service gracefully."""
    log.info("Stopping takt-service")
    self._running = False
    # Cancel running agents.
    for agent_id, task in list(self._agents.items()):
      task.cancel()
    if self._agents:
      await asyncio.gather(
        *self._agents.values(),
        return_exceptions=True,
      )
    if self._poll_task:
      self._poll_task.cancel()
    if self._cmd_task:
      self._cmd_task.cancel()
    if self._router:
      self._router.close(linger=0)
    if self._pub:
      self._pub.close(linger=0)
    if self._own_ctx:
      self._ctx.term()

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
      # frames[1] is the empty delimiter.
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

  async def _handle_list_agents(self, payload):
    """Handle list_agents command."""
    agents = []
    for aid, info in self._agent_infos.items():
      agents.append({
        "agent_id": info.agent_id,
        "workspace": info.workspace,
        "role": info.role,
        "model": info.model,
        "state": info.state.value,
        "total_cost_usd": info.total_cost_usd,
        "num_turns": info.num_turns,
        "started_at": info.started_at,
        "finished_at": info.finished_at,
        "error": info.error,
      })
    return {"agents": agents}

  async def _handle_replay_output(self, payload):
    """Handle replay_output command."""
    agent_id = payload["agent_id"]
    from_line = payload.get("from_line", 0)
    lines = self._store.load_output(
      agent_id, from_line=from_line
    )
    return {"lines": lines, "agent_id": agent_id}

  async def _handle_launch_agent(self, payload):
    """Handle launch_agent command."""
    agent_id = payload["agent_id"]
    if agent_id in self._agents:
      raise ValueError(
        f"Agent {agent_id} already running"
      )
    prompt = payload["prompt"]
    cwd = payload["cwd"]
    model = payload.get("model", "sonnet")
    workspace = payload.get("workspace", "")
    role = payload.get("role", "")
    info = AgentInfo(
      agent_id=agent_id,
      workspace=workspace,
      role=role,
      cwd=cwd,
      model=model,
    )
    self._agent_infos[agent_id] = info
    self._agent_line_counts[agent_id] = 0
    self._store.save_info(info)
    task = asyncio.create_task(
      self._run_agent(info, prompt)
    )
    self._agents[agent_id] = task
    return {"agent_id": agent_id}

  async def _handle_cancel_agent(self, payload):
    """Handle cancel_agent command."""
    agent_id = payload["agent_id"]
    task = self._agents.get(agent_id)
    if task is None:
      raise ValueError(
        f"Agent {agent_id} not running"
      )
    task.cancel()
    return {"agent_id": agent_id}

  async def _handle_trigger_stage(self, payload):
    """Handle trigger_stage command."""
    from bin.pipeline_watch import (
      build_trigger_prompt,
      scan_markers,
    )
    from lib.config import STAGES_DIR
    from lib.run_log import (
      get_active_run,
      start_run,
      update_stage,
    )
    from lib.workspace_ops import get_pipeline_stages

    ws = payload["workspace"]
    role = payload["role"]
    markers = scan_markers()
    repo_markers = markers.get((ws, role), [])
    if not repo_markers:
      raise ValueError(
        f"No markers for {ws}/{role}"
      )
    repos = [r for r, _ in repo_markers]
    agent_id = f"{ws}/{role}"
    stage_dir = STAGES_DIR / ws / role
    prompt = build_trigger_prompt(
      ws, role, repo_markers
    )
    # Launch the agent.
    result = await self._handle_launch_agent({
      "cmd": "launch_agent",
      "agent_id": agent_id,
      "prompt": prompt,
      "cwd": str(stage_dir),
      "workspace": ws,
      "role": role,
    })
    # Delete markers.
    for repo, _ in repo_markers:
      marker = (
        STAGES_DIR / ws / role / repo
        / ".pipeline-push"
      )
      marker.unlink(missing_ok=True)
    # Start run if first stage.
    pipeline = get_pipeline_stages(ws)
    if pipeline and role == pipeline[0]:
      if get_active_run(ws) is None:
        start_run(ws, repos, pipeline)
    update_stage(ws, role, "running")
    await self._publish(
      "pipeline.event", {
        "time": time.strftime("%H:%M:%S"),
        "stage": agent_id,
        "repos": ", ".join(repos),
        "event": "triggered",
      }
    )
    return result

  async def _handle_poll_now(self, payload):
    """Handle poll_now command — run one poll cycle."""
    events = await self._poll_once()
    return {"events": events}

  _cmd_handlers = {
    "ping": _handle_ping,
    "list_agents": _handle_list_agents,
    "replay_output": _handle_replay_output,
    "launch_agent": _handle_launch_agent,
    "cancel_agent": _handle_cancel_agent,
    "trigger_stage": _handle_trigger_stage,
    "poll_now": _handle_poll_now,
  }

  # -- Agent execution --

  async def _run_agent(self, info, prompt):
    """Run an agent with semaphore concurrency control.

    Args:
      info: AgentInfo for the agent.
      prompt: Prompt string.
    """
    agent_id = info.agent_id
    async with self._semaphore:
      runner = AgentRunner(info)
      try:
        await runner.run(prompt, lambda msg: (
          self._on_agent_message(agent_id, msg)
        ))
      except Exception as e:
        log.error(
          "Agent %s failed: %s", agent_id, e,
        )
      finally:
        self._store.save_info(info)
        await self._publish_agent_update(info)
        self._agents.pop(agent_id, None)
        self._on_agent_finished(info)

  def _on_agent_message(self, agent_id, msg):
    """Handle a message from a running agent.

    Serializes, persists, and publishes the output.

    Args:
      agent_id: Agent ID string.
      msg: SDK message object.
    """
    line_no = self._agent_line_counts.get(agent_id, 0)
    lines = serialize_sdk_message(msg, line_no)
    if not lines:
      return
    self._agent_line_counts[agent_id] = (
      line_no + len(lines)
    )
    self._store.append_output(agent_id, lines)
    # Update info periodically.
    info = self._agent_infos.get(agent_id)
    if info:
      self._store.save_info(info)
    # Publish each line to PUB socket.
    for line in lines:
      # Fire-and-forget publish (non-async).
      topic = f"agent.output.{agent_id}"
      try:
        self._pub.send_multipart(
          [topic.encode(), json.dumps(line).encode()],
          flags=zmq.NOBLOCK,
        )
      except zmq.ZMQError:
        pass
    # Publish agent update.
    if info:
      try:
        self._pub.send_multipart(
          [
            b"agent.update",
            json.dumps({
              "agent_id": info.agent_id,
              "state": info.state.value,
              "total_cost_usd": info.total_cost_usd,
              "num_turns": info.num_turns,
            }).encode(),
          ],
          flags=zmq.NOBLOCK,
        )
      except zmq.ZMQError:
        pass

  def _on_agent_finished(self, info):
    """Handle post-completion for an agent.

    Detects stage results, updates run log, retriggers
    PR stages, sends notifications.

    Args:
      info: AgentInfo for the finished agent.
    """
    from bin.pipeline_watch import (
      _detect_stage_result,
      _maybe_finish_run,
      _retrigger_pr_stage,
    )
    from lib.run_log import update_stage
    ws = info.workspace
    role = info.role
    if role == "sync":
      _retrigger_pr_stage(ws)
      return
    stage_result = _detect_stage_result(ws, role)
    if info.state == AgentState.FAILED:
      stage_result = "failed"
    if stage_result:
      update_stage(ws, role, stage_result)
      _maybe_finish_run(ws)
    ev_name = "finished"
    if stage_result:
      ev_name = f"finished:{stage_result}"
    try:
      self._pub.send_multipart(
        [
          b"pipeline.event",
          json.dumps({
            "time": time.strftime("%H:%M:%S"),
            "stage": info.agent_id,
            "repos": "",
            "event": ev_name,
          }).encode(),
        ],
        flags=zmq.NOBLOCK,
      )
    except zmq.ZMQError:
      pass

  async def _publish_agent_update(self, info):
    """Publish an agent.update event.

    Args:
      info: AgentInfo instance.
    """
    await self._publish("agent.update", {
      "agent_id": info.agent_id,
      "state": info.state.value,
      "total_cost_usd": info.total_cost_usd,
      "num_turns": info.num_turns,
      "started_at": info.started_at,
      "finished_at": info.finished_at,
      "error": info.error,
    })

  async def _publish(self, topic, data):
    """Publish a message on the PUB socket.

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

    Scans markers, triggers agents, snapshots refs.

    Returns:
      List of event dicts.
    """
    loop = asyncio.get_event_loop()
    events = await loop.run_in_executor(
      None, self._poll_sync
    )
    for ev in events:
      await self._publish("pipeline.event", ev)
    return events

  def _poll_sync(self):
    """Synchronous poll — runs in executor.

    Returns:
      List of event dicts.
    """
    from bin.pipeline_watch import (
      build_sync_prompt,
      build_trigger_prompt,
      find_changes,
      group_by_branch,
      load_refs,
      log_events,
      save_refs,
      scan_markers,
      scan_sync_markers,
      snapshot_all_refs,
      write_sync_markers,
    )
    from lib.config import (
      STAGES_DIR,
      WORKSPACES_DIR,
      get_default_branch,
      get_repo_path,
    )
    from lib.run_log import (
      get_active_run,
      start_run,
      update_stage,
    )
    from lib.workspace_ops import get_pipeline_stages

    repos_config = load_repos_config()
    events = []
    now = time.strftime("%H:%M:%S")

    # Scan and trigger pipeline stages.
    markers = scan_markers()
    for (ws, role), repo_markers in markers.items():
      repos = [r for r, _ in repo_markers]
      agent_id = f"{ws}/{role}"
      if agent_id in self._agents:
        continue
      stage_dir = STAGES_DIR / ws / role
      prompt = build_trigger_prompt(
        ws, role, repo_markers
      )
      # Schedule agent launch on the event loop.
      asyncio.get_event_loop().call_soon_threadsafe(
        self._schedule_agent_launch,
        agent_id, prompt, str(stage_dir), ws, role,
      )
      # Delete markers.
      for repo, _ in repo_markers:
        marker = (
          STAGES_DIR / ws / role / repo
          / ".pipeline-push"
        )
        marker.unlink(missing_ok=True)
      # Start run if first stage.
      pipeline = get_pipeline_stages(ws)
      if pipeline and role == pipeline[0]:
        if get_active_run(ws) is None:
          start_run(ws, repos, pipeline)
      update_stage(ws, role, "running")
      events.append({
        "time": now,
        "stage": agent_id,
        "repos": ", ".join(repos),
        "event": "triggered",
      })

    # Scan and trigger sync agents.
    sync_markers = scan_sync_markers()
    for ws, repo_markers in sync_markers.items():
      repos = [r for r, _ in repo_markers]
      agent_id = f"{ws}/sync"
      if agent_id in self._agents:
        continue
      prompt = build_sync_prompt(ws, repo_markers)
      ws_dir = WORKSPACES_DIR / ws
      asyncio.get_event_loop().call_soon_threadsafe(
        self._schedule_agent_launch,
        agent_id, prompt, str(ws_dir), ws, "sync",
      )
      # Delete markers.
      for repo, _ in repo_markers:
        marker = (
          WORKSPACES_DIR / ws / repo
          / ".upstream-sync"
        )
        marker.unlink(missing_ok=True)
      events.append({
        "time": now,
        "stage": agent_id,
        "repos": ", ".join(repos),
        "event": "triggered",
      })

    # Snapshot refs and detect upstream changes.
    old_refs = load_refs()
    new_refs = snapshot_all_refs(repos_config)
    if not old_refs:
      log.info(
        "First run — snapshotted %d refs", len(new_refs)
      )
      save_refs(new_refs)
      log_events(events)
      return events
    changes = find_changes(old_refs, new_refs)
    if changes:
      groups = group_by_branch(changes)
      log.info(
        "Detected changes in %d branch(es)",
        len(groups),
      )
      default_changes = []
      repos_cfg = repos_config.get("repos", {})
      for branch, branch_changes in groups.items():
        for c in branch_changes:
          cfg = repos_cfg.get(c["repo"], {})
          repo_path = get_repo_path(
            cfg.get("path", c["repo"])
          )
          default_br = cfg.get(
            "default_branch",
            get_default_branch(repo_path),
          )
          if c["branch"] == default_br:
            default_changes.append(c)
      if default_changes:
        write_sync_markers(default_changes, repos_config)
    save_refs(new_refs)
    log_events(events)
    return events

  def _schedule_agent_launch(self, agent_id, prompt,
                             cwd, workspace, role):
    """Schedule an agent launch as an asyncio task.

    Called from the executor thread via
    call_soon_threadsafe.

    Args:
      agent_id: Agent ID string.
      prompt: Prompt for the agent.
      cwd: Working directory.
      workspace: Workspace name.
      role: Pipeline role.
    """
    if agent_id in self._agents:
      return
    info = AgentInfo(
      agent_id=agent_id,
      workspace=workspace,
      role=role,
      cwd=cwd,
    )
    self._agent_infos[agent_id] = info
    self._agent_line_counts[agent_id] = 0
    self._store.save_info(info)
    task = asyncio.create_task(
      self._run_agent(info, prompt)
    )
    self._agents[agent_id] = task
