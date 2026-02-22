"""Agents tab — list of all agents with output viewer.

Receives agent state via service events (agent.update)
and replays/streams output via service commands and
agent.output.<id> subscriptions.
"""

import asyncio
import logging

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, RichLog, Static

from tui.widgets.agent_output import render_output_line

log = logging.getLogger("takt.agents_tab")


class AgentsTab(Static):
  """Agent list + output viewer in a vertical split."""

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
    max-height: 12;
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
      yield RichLog(
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
    table.add_columns(
      "Agent", "Model", "State", "Turns", "Cost",
    )
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

  async def _poll_agents_service(self) -> None:
    """Fetch agent list from service."""
    try:
      reply = await self.app.service.send_cmd(
        "list_agents"
      )
      if reply.get("status") != "ok":
        return
      agents = reply["data"]["agents"]
      table = self.query_one(
        "#agents-list-table", DataTable
      )
      existing_keys = set()
      for i in range(table.row_count):
        coord = table.coordinate_to_cell_key((i, 0))
        existing_keys.add(str(coord[0].value))
      current_ids = {a["agent_id"] for a in agents}
      for agent in agents:
        aid = agent["agent_id"]
        self._agent_data[aid] = agent
        state = agent.get("state", "?")
        turns = str(agent.get("num_turns", 0))
        cost_val = agent.get("total_cost_usd", 0)
        cost = (
          f"${cost_val:.4f}" if cost_val > 0 else "-"
        )
        model = agent.get("model", "?")
        if aid in existing_keys:
          row_idx = self._find_row_index(table, aid)
          if row_idx is not None:
            table.update_cell_at((row_idx, 2), state)
            table.update_cell_at((row_idx, 3), turns)
            table.update_cell_at((row_idx, 4), cost)
        else:
          table.add_row(
            aid, model, state, turns, cost, key=aid,
          )
      for key in existing_keys - current_ids:
        table.remove_row(key)
    except Exception:
      log.debug(
        "poll agents service failed", exc_info=True
      )

  def _poll_registry(self) -> None:
    """Refresh agent list from local registry."""
    from lib import agent_registry
    runners = agent_registry.list_all()
    table = self.query_one(
      "#agents-list-table", DataTable
    )
    existing_keys = set()
    for i in range(table.row_count):
      coord = table.coordinate_to_cell_key((i, 0))
      existing_keys.add(str(coord[0].value))
    current_ids = {r.info.agent_id for r in runners}
    for runner in runners:
      info = runner.info
      aid = info.agent_id
      state = info.state.value
      turns = str(info.num_turns)
      cost = (
        f"${info.total_cost_usd:.4f}"
        if info.total_cost_usd > 0 else "-"
      )
      if aid in existing_keys:
        row_idx = self._find_row_index(table, aid)
        if row_idx is not None:
          table.update_cell_at((row_idx, 2), state)
          table.update_cell_at((row_idx, 3), turns)
          table.update_cell_at((row_idx, 4), cost)
      else:
        table.add_row(
          aid, info.model, state, turns, cost,
          key=aid,
        )
    for key in existing_keys - current_ids:
      table.remove_row(key)

  def _find_row_index(self, table, key_value):
    """Find row index for a given key value.

    Args:
      table: DataTable instance.
      key_value: String key to find.

    Returns:
      Row index or None.
    """
    for i in range(table.row_count):
      coord = table.coordinate_to_cell_key((i, 0))
      if str(coord[0].value) == key_value:
        return i
    return None

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
    self._show_agent_output(agent_id)

  def on_data_table_row_highlighted(
    self, event: DataTable.RowHighlighted
  ) -> None:
    """Preview output when cursor moves."""
    if event.row_key is not None:
      agent_id = str(event.row_key.value)
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
      "#agent-viewer-log", RichLog
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
      status.update(f"[{state}]  {agent_id}")
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
        "#agent-viewer-log", RichLog
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
          "#agent-viewer-log", RichLog
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
      "#agent-viewer-log", RichLog
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
      "#agent-viewer-log", RichLog
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
