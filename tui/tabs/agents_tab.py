"""Agents tab — list of all agents with output viewer.

Receives agent state via service events (agent.update)
and replays/streams output via service commands and
agent.output.<id> subscriptions.
"""

import asyncio
import logging

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from tui.widgets.agent_output import render_output_line
from tui.widgets.selectable_log import SelectableLog

log = logging.getLogger("takt.agents_tab")

# Colors for agent state labels.
_STATE_COLORS = {
  "pending": "#ffb74d",
  "running": "#42a5f5",
  "completed": "#4caf50",
  "failed": "#ef5350",
  "cancelled": "#666666",
}

# Icons for agent state.
_STATE_ICONS = {
  "pending": "○",
  "running": "●",
  "completed": "✓",
  "failed": "✗",
  "cancelled": "·",
}


class AgentsTab(Static):
  """Agent list + output viewer in a vertical split."""

  BINDINGS = [
    Binding("k", "cancel_agent", "Cancel"),
    Binding("R", "retry_agent", "Retry"),
  ]

  DEFAULT_CSS = """
  AgentsTab {
    height: 1fr;
  }

  AgentsTab #agents-list-section {
    height: auto;
    max-height: 40%;
    border-bottom: solid #2a2a2a;
  }

  AgentsTab #agents-list-table {
    height: auto;
    max-height: 16;
    background: #101010;
  }

  AgentsTab #agent-output-section {
    height: 1fr;
  }

  AgentsTab #agent-viewer-status {
    height: 1;
    background: #1a1a1a;
    color: #cccccc;
    padding: 0 1;
  }

  AgentsTab #agent-viewer-log {
    height: 1fr;
    background: #101010;
    padding: 0 1;
  }
  """

  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    self._selected_id = None
    self._agent_data = {}  # agent_id -> state dict
    self._viewer_line_count = 0

  def compose(self) -> ComposeResult:
    with Vertical(id="agents-list-section"):
      yield Static("Agents", classes="panel-title")
      yield DataTable(id="agents-list-table")
    with Vertical(id="agent-output-section"):
      yield Static(
        "Select an agent to view output",
        id="agent-viewer-status",
      )
      yield SelectableLog(
        id="agent-viewer-log",
        highlight=True,
        markup=True,
        wrap=True,
      )

  def on_mount(self) -> None:
    """Set up table columns and start polling."""
    table = self.query_one(
      "#agents-list-table", DataTable
    )
    table.cursor_type = "row"
    table.add_columns("", "Role", "Model", "State",
                      "Turns", "Cost")
    self.set_interval(2, self._poll_agents)

  def _poll_agents(self) -> None:
    """Refresh agent list from service or registry."""
    client = getattr(self.app, 'service', None)
    if client:
      asyncio.ensure_future(
        self._poll_agents_service()
      )
    else:
      self._poll_registry()

  def _rebuild_table(self, rows):
    """Clear and rebuild agent table as a tree by workspace.

    Workspace header rows are bold with a summary, agent
    rows are indented underneath. Workspaces with running
    agents sort first; agents sort by activity within each
    group.

    Args:
      rows: List of dicts with keys: agent_id, workspace,
        role, model, state, num_turns, total_cost_usd.
    """
    table = self.query_one(
      "#agents-list-table", DataTable
    )
    # Preserve cursor position.
    old_key = None
    if table.row_count > 0:
      try:
        row_idx = table.cursor_row
        coord = table.coordinate_to_cell_key(
          (row_idx, 0)
        )
        old_key = str(coord[0].value)
      except Exception:
        pass
    table.clear()
    # Group by workspace.
    groups = {}
    for r in rows:
      groups.setdefault(r["workspace"], []).append(r)
    # Sort agents within each group by activity.
    state_order = {
      "running": 0, "pending": 1, "completed": 2,
      "failed": 3, "cancelled": 4,
    }
    for agents in groups.values():
      agents.sort(
        key=lambda r: state_order.get(r["state"], 9)
      )
    # Sort workspaces: any running agent first, then
    # alphabetical.
    def ws_sort_key(ws):
      has_running = any(
        a["state"] == "running"
        for a in groups[ws]
      )
      return (0 if has_running else 1, ws)
    sorted_ws = sorted(groups, key=ws_sort_key)
    restore_idx = 0
    for ws in sorted_ws:
      agents = groups[ws]
      # Workspace header row.
      running = sum(
        1 for a in agents if a["state"] == "running"
      )
      summary = Text()
      summary.append(f"  {ws}", style="bold")
      if running:
        summary.append(
          f"  ({running} running)", style="#42a5f5"
        )
      table.add_row(
        summary, "", "", "", "", "",
        key=f"__ws__{ws}",
      )
      # Agent rows indented under workspace.
      for r in agents:
        aid = r["agent_id"]
        state = r["state"]
        color = _STATE_COLORS.get(state, "#888888")
        icon = _STATE_ICONS.get(state, " ")
        role_text = Text()
        role_text.append(f"  {icon} ", style=color)
        role_text.append(r["role"])
        state_text = Text(state, style=color)
        turns = str(r.get("num_turns", 0))
        cost_val = r.get("total_cost_usd", 0)
        cost = (
          f"${cost_val:.4f}" if cost_val > 0
          else "-"
        )
        model = r.get("model", "?")
        table.add_row(
          "", role_text, model, state_text, turns,
          cost, key=aid,
        )
        if aid == old_key:
          restore_idx = table.row_count - 1
    if table.row_count > 0:
      table.move_cursor(row=restore_idx)

  async def _poll_agents_service(self) -> None:
    """Fetch agent list from service."""
    try:
      reply = await self.app.service.send_cmd(
        "list_agents"
      )
      if reply.get("status") != "ok":
        return
      agents = reply["data"]["agents"]
      rows = []
      for agent in agents:
        aid = agent["agent_id"]
        self._agent_data[aid] = agent
        ws = agent.get("workspace", "")
        role = agent.get("role", "")
        if not ws and "/" in aid:
          ws, role = aid.split("/", 1)
        rows.append({
          "agent_id": aid,
          "workspace": ws,
          "role": role,
          "model": agent.get("model", "?"),
          "state": agent.get("state", "?"),
          "num_turns": agent.get("num_turns", 0),
          "total_cost_usd": agent.get(
            "total_cost_usd", 0
          ),
        })
      self._rebuild_table(rows)
    except Exception:
      log.debug(
        "poll agents service failed", exc_info=True
      )

  def _poll_registry(self) -> None:
    """Refresh agent list from local registry."""
    from lib import agent_registry
    runners = agent_registry.list_all()
    rows = []
    for runner in runners:
      info = runner.info
      aid = info.agent_id
      ws = info.workspace
      role = info.role
      if not ws and "/" in aid:
        ws, role = aid.split("/", 1)
      rows.append({
        "agent_id": aid,
        "workspace": ws,
        "role": role,
        "model": info.model,
        "state": info.state.value,
        "num_turns": info.num_turns,
        "total_cost_usd": info.total_cost_usd,
      })
    self._rebuild_table(rows)

  def on_agent_update(self, data) -> None:
    """Handle agent.update event from service.

    Args:
      data: Dict with agent_id, state, cost, etc.
    """
    aid = data.get("agent_id")
    if aid:
      self._agent_data[aid] = data

  def on_data_table_row_selected(
    self, event: DataTable.RowSelected
  ) -> None:
    """Show output for the selected agent."""
    agent_id = str(event.row_key.value)
    if agent_id.startswith("__ws__"):
      return
    self._show_agent_output(agent_id)

  def on_data_table_row_highlighted(
    self, event: DataTable.RowHighlighted
  ) -> None:
    """Preview output when cursor moves."""
    if event.row_key is not None:
      agent_id = str(event.row_key.value)
      if agent_id.startswith("__ws__"):
        return
      self._show_agent_output(agent_id)

  def select_agent(self, agent_id):
    """Programmatically select an agent for viewing.

    Args:
      agent_id: The agent ID to view.
    """
    self._show_agent_output(agent_id)

  def _show_agent_output(self, agent_id) -> None:
    """Display output for an agent.

    Uses service replay_output if connected, otherwise
    falls back to local runner buffer.

    Args:
      agent_id: The agent ID to display.
    """
    if agent_id == self._selected_id:
      return
    # Unsubscribe from previous agent output.
    client = getattr(self.app, 'service', None)
    if client and self._selected_id:
      client.unsubscribe(
        f"agent.output.{self._selected_id}"
      )
      client.off(f"agent.output.")
    self._selected_id = agent_id
    self._viewer_line_count = 0
    log_widget = self.query_one(
      "#agent-viewer-log", SelectableLog
    )
    log_widget.clear()
    status = self.query_one(
      "#agent-viewer-status", Static
    )
    if client:
      # Subscribe to live output.
      client.subscribe(f"agent.output.{agent_id}")
      client.on(
        f"agent.output.",
        self._on_output_line,
      )
      # Replay stored output.
      asyncio.ensure_future(
        self._replay_output(agent_id)
      )
      # Update status from cached data.
      data = self._agent_data.get(agent_id, {})
      state = data.get("state", "?")
      color = _STATE_COLORS.get(state, "#888888")
      st = Text()
      st.append(f"[{state}]", style=color)
      st.append(f"  {agent_id}")
      status.update(st)
    else:
      self._show_local_output(agent_id)

  async def _replay_output(self, agent_id) -> None:
    """Replay stored output from service.

    Args:
      agent_id: Agent ID to replay.
    """
    client = self.app.service
    if not client:
      return
    try:
      reply = await client.send_cmd(
        "replay_output",
        agent_id=agent_id,
        from_line=0,
      )
      if reply.get("status") != "ok":
        return
      lines = reply["data"]["lines"]
      if agent_id != self._selected_id:
        return
      log_widget = self.query_one(
        "#agent-viewer-log", SelectableLog
      )
      for line in lines:
        rendered = render_output_line(line)
        if rendered:
          log_widget.write(rendered)
      self._viewer_line_count = len(lines)
    except Exception:
      log.debug(
        "replay_output failed", exc_info=True
      )

  def _on_output_line(self, topic, data) -> None:
    """Handle live agent output from PUB socket.

    Args:
      topic: Topic string (agent.output.<id>).
      data: Output line dict.
    """
    # Extract agent_id from topic.
    agent_id = topic.replace("agent.output.", "", 1)
    if agent_id != self._selected_id:
      return
    rendered = render_output_line(data)
    if rendered:
      try:
        log_widget = self.query_one(
          "#agent-viewer-log", SelectableLog
        )
        log_widget.write(rendered)
        self._viewer_line_count += 1
      except Exception:
        pass

  def _show_local_output(self, agent_id) -> None:
    """Show output from local agent runner buffer.

    Args:
      agent_id: Agent ID to display.
    """
    from lib import agent_registry
    runner = agent_registry.get(agent_id)
    status = self.query_one(
      "#agent-viewer-status", Static
    )
    log_widget = self.query_one(
      "#agent-viewer-log", SelectableLog
    )
    if runner is None:
      status.update(f"{agent_id} (not found)")
      return
    info = runner.info
    parts = [
      f"[{info.state.value}]",
      agent_id,
      f"model:{info.model}",
    ]
    if info.total_cost_usd > 0:
      parts.append(f"${info.total_cost_usd:.4f}")
    if info.num_turns > 0:
      parts.append(f"{info.num_turns} turns")
    status.update("  ".join(parts))
    buf = getattr(runner, '_output_buffer', [])
    for item in buf:
      log_widget.write(item)
    runner._viewer_offset = len(buf)

  def refresh_viewer(self) -> None:
    """Called periodically to stream new local output."""
    if not self._selected_id:
      return
    client = getattr(self.app, 'service', None)
    if client:
      return  # Live output via SUB socket.
    self._append_local_lines(self._selected_id)

  def _append_local_lines(self, agent_id) -> None:
    """Append new lines from local runner buffer.

    Args:
      agent_id: Agent ID to check.
    """
    from lib import agent_registry
    runner = agent_registry.get(agent_id)
    if runner is None:
      return
    buf = getattr(runner, '_output_buffer', [])
    offset = getattr(runner, '_viewer_offset', 0)
    if offset >= len(buf):
      return
    log_widget = self.query_one(
      "#agent-viewer-log", SelectableLog
    )
    for item in buf[offset:]:
      log_widget.write(item)
    runner._viewer_offset = len(buf)
    info = runner.info
    parts = [
      f"[{info.state.value}]",
      agent_id,
      f"model:{info.model}",
    ]
    if info.total_cost_usd > 0:
      parts.append(f"${info.total_cost_usd:.4f}")
    if info.num_turns > 0:
      parts.append(f"{info.num_turns} turns")
    status = self.query_one(
      "#agent-viewer-status", Static
    )
    status.update("  ".join(parts))

  def _get_selected_agent_id(self):
    """Return the agent_id of the selected row, or None.

    Skips workspace header rows (keys starting with
    '__ws__').

    Returns:
      Agent ID string, or None if no valid selection.
    """
    table = self.query_one(
      "#agents-list-table", DataTable
    )
    if table.row_count == 0:
      return None
    try:
      row_idx = table.cursor_row
      coord = table.coordinate_to_cell_key(
        (row_idx, 0)
      )
      key = str(coord[0].value)
      if key.startswith("__ws__"):
        return None
      return key
    except Exception:
      return None

  def action_cancel_agent(self) -> None:
    """Cancel the selected running/pending agent."""
    aid = self._get_selected_agent_id()
    if not aid:
      self.app.notify(
        "No agent selected", severity="warning"
      )
      return
    data = self._agent_data.get(aid, {})
    state = data.get("state", "")
    if state not in ("running", "pending"):
      self.app.notify(
        f"Agent is {state}, not cancellable",
        severity="warning",
      )
      return
    from tui.screens import ConfirmScreen
    self.app.push_screen(
      ConfirmScreen(
        f"Cancel agent '{aid}'?", aid
      ),
      callback=self._on_cancel_confirmed,
    )

  def _on_cancel_confirmed(self, result) -> None:
    """Handle cancel confirmation.

    Args:
      result: Agent ID string if confirmed, None otherwise.
    """
    if not result:
      return
    client = getattr(self.app, 'service', None)
    if client:
      asyncio.ensure_future(
        self._cancel_via_service(result)
      )
    else:
      self._cancel_local(result)

  async def _cancel_via_service(self, agent_id):
    """Send cancel_agent command to service.

    Args:
      agent_id: Agent ID to cancel.
    """
    try:
      reply = await self.app.service.send_cmd(
        "cancel_agent", agent_id=agent_id,
      )
      if reply.get("status") == "ok":
        self.app.notify(f"Cancelled {agent_id}")
      else:
        self.app.notify(
          reply.get("message", "Cancel failed"),
          severity="error",
        )
    except Exception as e:
      log.error(
        "cancel_agent failed: %s", e, exc_info=True
      )
      self.app.notify(
        f"Cancel failed: {e}", severity="error"
      )

  def _cancel_local(self, agent_id):
    """Cancel a local agent runner.

    Args:
      agent_id: Agent ID to cancel.
    """
    from lib import agent_registry
    runner = agent_registry.get(agent_id)
    if runner:
      runner.cancel()
      self.app.notify(f"Cancelled {agent_id}")
    else:
      self.app.notify(
        f"Agent {agent_id} not found",
        severity="warning",
      )

  def action_retry_agent(self) -> None:
    """Retry the selected failed/cancelled agent."""
    aid = self._get_selected_agent_id()
    if not aid:
      self.app.notify(
        "No agent selected", severity="warning"
      )
      return
    data = self._agent_data.get(aid, {})
    state = data.get("state", "")
    if state not in ("failed", "cancelled"):
      self.app.notify(
        f"Agent is {state}, not retryable",
        severity="warning",
      )
      return
    from tui.screens import ConfirmScreen
    self.app.push_screen(
      ConfirmScreen(
        f"Retry agent '{aid}'?", aid
      ),
      callback=self._on_retry_confirmed,
    )

  def _on_retry_confirmed(self, result) -> None:
    """Handle retry confirmation.

    Args:
      result: Agent ID string if confirmed, None otherwise.
    """
    if not result:
      return
    data = self._agent_data.get(result, {})
    ws = data.get("workspace", "")
    role = data.get("role", "")
    client = getattr(self.app, 'service', None)
    if client:
      asyncio.ensure_future(
        self._retry_via_service(ws, role)
      )
    else:
      self._retry_local(ws, role)

  async def _retry_via_service(self, ws, role):
    """Send trigger_stage command to service to retry.

    Args:
      ws: Workspace name.
      role: Stage role.
    """
    try:
      reply = await self.app.service.send_cmd(
        "trigger_stage", workspace=ws, role=role,
      )
      if reply.get("status") == "ok":
        self.app.notify(f"Retrying {ws}/{role}")
      else:
        self.app.notify(
          reply.get("message", "Retry failed"),
          severity="error",
        )
    except Exception as e:
      log.error(
        "retry agent failed: %s", e, exc_info=True
      )
      self.app.notify(
        f"Retry failed: {e}", severity="error"
      )

  def _retry_local(self, ws, role):
    """Local fallback: retrigger via markers.

    Args:
      ws: Workspace name.
      role: Stage role.
    """
    from bin.pipeline_watch import (
      build_trigger_prompt,
      scan_markers,
    )
    from lib.config import STAGES_DIR
    agent_id = f"{ws}/{role}"
    stage_dir = STAGES_DIR / ws / role
    markers = scan_markers()
    repo_markers = markers.get((ws, role), [])
    if repo_markers:
      prompt = build_trigger_prompt(
        ws, role, repo_markers
      )
      self.app.launch_agent(
        agent_id, prompt, str(stage_dir),
        workspace=ws, role=role,
      )
      for repo, _ in repo_markers:
        marker = (
          stage_dir / repo / ".pipeline-push"
        )
        marker.unlink(missing_ok=True)
    else:
      self.app.notify(
        f"No markers found for {ws}/{role}. "
        f"Use Trigger to re-trigger.",
        severity="warning",
      )
