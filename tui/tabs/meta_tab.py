"""Meta agents tab — inline editor for meta agents.

Meta agents operate on takt itself rather than user
workspaces. This tab lists agents with an inline editor
below (no modal dialogs). Selecting a row auto-loads
its fields; New clears the form for a fresh entry.
"""

import asyncio
import logging

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.widgets import (
  DataTable,
  Input,
  Label,
  Select,
  Static,
  TextArea,
)
from textual import work

from tui.mixins import TabBase
from tui.widgets.agent_output import render_output_line
from tui.widgets.selectable_log import SelectableLog

log = logging.getLogger("takt.meta_tab")

_STATE_COLORS = {
  "queued": "#ffb74d",
  "running": "#42a5f5",
  "completed": "#4caf50",
  "failed": "#ef5350",
  "cancelled": "#666666",
}

_STATE_ICONS = {
  "queued": "○",
  "running": "●",
  "completed": "✓",
  "failed": "✗",
  "cancelled": "·",
}


class MetaTab(TabBase, Static):
  """Meta agents list with inline editor, run history,
  and output viewer."""

  _status_id = "meta-status"

  BINDINGS = [
    Binding("n", "new_agent", "New"),
    Binding("d", "delete_agent", "Delete"),
    Binding("r", "run_agent", "Run"),
    Binding("c", "cancel_run", "Cancel Run"),
    Binding(
      "ctrl+s", "save_agent", "Save", show=True
    ),
  ]

  DEFAULT_CSS = """
  MetaTab {
    height: 1fr;
  }

  MetaTab #meta-top-row {
    height: auto;
    max-height: 40%;
    border-bottom: solid #2a2a2a;
  }

  MetaTab #meta-agents-section {
    width: 1fr;
    height: auto;
    border-right: solid #2a2a2a;
  }

  MetaTab #meta-agents-table {
    height: auto;
    max-height: 12;
    background: #101010;
  }

  MetaTab #meta-runs-section {
    width: 1fr;
    height: auto;
  }

  MetaTab #meta-runs-table {
    height: auto;
    max-height: 12;
    background: #101010;
  }

  MetaTab #meta-editor-section {
    height: auto;
    max-height: 40%;
    padding: 0 1;
    border-bottom: solid #2a2a2a;
  }

  MetaTab #meta-editor-section Label {
    margin: 0 1 0 0;
    padding: 1 0 0 0;
  }

  MetaTab #meta-editor-row {
    height: auto;
    margin: 0 0 1 0;
  }

  MetaTab #meta-editor-row Input {
    width: 1fr;
  }

  MetaTab #meta-editor-row2 {
    height: auto;
    margin: 0 0 1 0;
  }

  MetaTab #meta-editor-row2 Select {
    width: 20;
  }

  MetaTab #meta-editor-row2 Input {
    width: 15;
  }

  MetaTab #meta-prompt-area {
    height: 8;
    margin: 0 0 1 0;
  }

  MetaTab #meta-status {
    height: auto;
    color: $warning;
  }

  MetaTab #meta-output-section {
    height: 1fr;
  }

  MetaTab #meta-output-status {
    height: 1;
    background: #1a1a1a;
    color: #cccccc;
    padding: 0 1;
  }

  MetaTab #meta-output-log {
    height: 1fr;
    background: #101010;
    padding: 0 1;
  }
  """

  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    self._agents = []
    self._editing_id = None
    self._selected_agent_id = None
    self._selected_run_id = None
    self._runs = []

  def compose(self) -> ComposeResult:
    with Horizontal(id="meta-top-row"):
      with Vertical(id="meta-agents-section"):
        yield Static("Meta Agents", classes="panel-title")
        yield DataTable(id="meta-agents-table")
      with Vertical(id="meta-runs-section"):
        yield Static(
          "Run History", classes="panel-title"
        )
        yield DataTable(id="meta-runs-table")
    with Vertical(id="meta-editor-section"):
      with Horizontal(id="meta-editor-row"):
        yield Label("Name:")
        yield Input(
          placeholder="agent-name",
          id="meta-name-input",
        )
        yield Label("Desc:")
        yield Input(
          placeholder="Short description",
          id="meta-desc-input",
        )
      with Horizontal(id="meta-editor-row2"):
        yield Label("Model:")
        yield Select(
          [("sonnet", "sonnet"), ("opus", "opus"),
           ("haiku", "haiku")],
          id="meta-model-select",
          value="sonnet",
        )
        yield Label("Timeout:")
        yield Input(
          value="1800",
          placeholder="1800",
          id="meta-timeout-input",
        )
      yield Static("Prompt:", id="meta-prompt-label")
      yield TextArea("", id="meta-prompt-area")
      yield Static("", id="meta-status")
    with Vertical(id="meta-output-section"):
      yield Static(
        "Select an agent to view runs",
        id="meta-output-status",
      )
      yield SelectableLog(
        id="meta-output-log",
        highlight=True,
        markup=True,
        wrap=True,
      )

  def on_mount(self) -> None:
    """Set up table columns and schedule data load."""
    agents_table = self.query_one(
      "#meta-agents-table", DataTable
    )
    agents_table.cursor_type = "row"
    agents_table.add_columns(
      "Name", "Model", "Description"
    )
    runs_table = self.query_one(
      "#meta-runs-table", DataTable
    )
    runs_table.cursor_type = "row"
    runs_table.add_columns(
      "#", "Status", "Cost", "Turns", "Time"
    )
    self.set_timer(0.1, self._initial_load)
    self.set_interval(5, self._poll_refresh)

  def _initial_load(self) -> None:
    """Deferred initial load after app mount."""
    self._load_agents()

  def _poll_refresh(self) -> None:
    """Periodic refresh of run statuses."""
    if self._selected_agent_id is not None:
      self._load_runs(self._selected_agent_id)

  def refresh_data(self) -> None:
    """Reload agents list."""
    self._load_agents()

  # -- Agent data loading --

  @work(thread=True)
  def _load_agents(self) -> None:
    """Load meta agents from DB."""
    from lib import db
    agents = db.list_meta_agents()
    self.app.call_from_thread(
      self._apply_agents, agents
    )

  def _apply_agents(self, agents) -> None:
    """Populate the agents table.

    Args:
      agents: List of meta agent dicts.
    """
    self._agents = agents
    table = self.query_one(
      "#meta-agents-table", DataTable
    )
    table.clear()
    for agent in agents:
      table.add_row(
        agent["name"],
        agent["model"],
        agent["description"],
        key=str(agent["id"]),
      )

  # -- Inline editor: auto-load on row highlight --

  def on_data_table_row_highlighted(
    self, event: DataTable.RowHighlighted
  ) -> None:
    """Auto-populate editor when agent row changes."""
    if event.data_table.id == "meta-agents-table":
      if event.row_key is None:
        return
      agent_id = int(event.row_key.value)
      if agent_id != self._selected_agent_id:
        self._selected_agent_id = agent_id
        self._selected_run_id = None
        self._clear_output()
        self._load_runs(agent_id)
      self._load_into_editor(agent_id)
    elif event.data_table.id == "meta-runs-table":
      if event.row_key is None:
        return
      run_id = int(event.row_key.value)
      if run_id != self._selected_run_id:
        self._selected_run_id = run_id
        self._load_output(run_id)

  def on_data_table_row_selected(
    self, event: DataTable.RowSelected
  ) -> None:
    """Load output on Enter in runs table."""
    if event.data_table.id == "meta-runs-table":
      run_id = int(event.row_key.value)
      self._selected_run_id = run_id
      self._load_output(run_id)

  def _load_into_editor(self, agent_id) -> None:
    """Populate editor fields from an agent.

    Args:
      agent_id: Meta agent row ID.
    """
    agent = next(
      (a for a in self._agents if a["id"] == agent_id),
      None,
    )
    if agent is None:
      return
    self._editing_id = agent_id
    self.query_one(
      "#meta-name-input", Input
    ).value = agent["name"]
    self.query_one(
      "#meta-desc-input", Input
    ).value = agent["description"]
    model_sel = self.query_one(
      "#meta-model-select", Select
    )
    model_sel.value = agent["model"]
    self.query_one(
      "#meta-timeout-input", Input
    ).value = str(agent["timeout_secs"])
    self.query_one(
      "#meta-prompt-area", TextArea
    ).load_text(agent["prompt"])
    self._set_status("")

  def _clear_editor(self) -> None:
    """Clear editor fields for a new agent."""
    self._editing_id = None
    self.query_one(
      "#meta-name-input", Input
    ).value = ""
    self.query_one(
      "#meta-desc-input", Input
    ).value = ""
    self.query_one(
      "#meta-model-select", Select
    ).value = "sonnet"
    self.query_one(
      "#meta-timeout-input", Input
    ).value = "1800"
    self.query_one(
      "#meta-prompt-area", TextArea
    ).load_text("")
    self._set_status("New agent — fill in and Save.")

  def _read_editor(self):
    """Read current editor field values.

    Returns:
      Dict with name, description, model, timeout_secs,
      prompt. Or None if validation fails (sets status).
    """
    name = self.query_one(
      "#meta-name-input", Input
    ).value.strip()
    if not name:
      self._set_status("Name is required.")
      return None
    desc = self.query_one(
      "#meta-desc-input", Input
    ).value.strip()
    model_sel = self.query_one(
      "#meta-model-select", Select
    )
    model = (
      str(model_sel.value)
      if model_sel.value is not Select.BLANK
      else "sonnet"
    )
    timeout_str = self.query_one(
      "#meta-timeout-input", Input
    ).value.strip()
    try:
      timeout = int(timeout_str)
    except ValueError:
      self._set_status("Timeout must be a number.")
      return None
    prompt = self.query_one(
      "#meta-prompt-area", TextArea
    ).text
    return {
      "name": name,
      "description": desc,
      "model": model,
      "timeout_secs": timeout,
      "prompt": prompt,
    }

  # -- Runs + output loading --

  @work(thread=True)
  def _load_runs(self, agent_id) -> None:
    """Load run history for a meta agent.

    Args:
      agent_id: Meta agent row ID.
    """
    from lib import db
    runs = db.list_meta_agent_runs(agent_id)
    self.app.call_from_thread(
      self._apply_runs, runs
    )

  def _apply_runs(self, runs) -> None:
    """Populate the runs table.

    Args:
      runs: List of run dicts.
    """
    self._runs = runs
    table = self.query_one(
      "#meta-runs-table", DataTable
    )
    table.clear()
    for run in runs:
      status = run["status"]
      color = _STATE_COLORS.get(status, "#888888")
      icon = _STATE_ICONS.get(status, " ")
      cost = run.get("cost_usd", 0) or 0
      cost_str = f"${cost:.4f}" if cost > 0 else "-"
      turns = str(run.get("num_turns", 0) or 0)
      created = run.get("created_at", "")[:19]
      from rich.text import Text
      status_text = Text()
      status_text.append(f"{icon} ", style=color)
      status_text.append(status, style=color)
      table.add_row(
        str(run["id"]),
        status_text,
        cost_str,
        turns,
        created,
        key=str(run["id"]),
      )

  @work(thread=True)
  def _load_output(self, run_id) -> None:
    """Load output for a meta agent run.

    Args:
      run_id: Meta agent run row ID.
    """
    from lib import db
    lines = db.get_meta_output(run_id)
    run = db.get_meta_agent_run(run_id)
    self.app.call_from_thread(
      self._apply_output, run_id, lines, run
    )

  def _apply_output(self, run_id, lines, run) -> None:
    """Populate the output log.

    Args:
      run_id: Meta agent run row ID.
      lines: List of output line dicts.
      run: Run dict.
    """
    log_widget = self.query_one(
      "#meta-output-log", SelectableLog
    )
    log_widget.clear()
    for line in lines:
      rendered = render_output_line(line)
      if rendered:
        log_widget.write(rendered)
    status = self.query_one(
      "#meta-output-status", Static
    )
    if run:
      state = run["status"]
      color = _STATE_COLORS.get(state, "#888888")
      from rich.text import Text
      st = Text()
      st.append(f"[{state}]", style=color)
      st.append(f"  Run #{run_id}")
      status.update(st)

  def _clear_output(self) -> None:
    """Clear the output viewer."""
    log_widget = self.query_one(
      "#meta-output-log", SelectableLog
    )
    log_widget.clear()
    status = self.query_one(
      "#meta-output-status", Static
    )
    status.update("Select a run to view output")

  # -- Keybinding actions --

  def action_new_agent(self) -> None:
    """Clear the editor for a new agent."""
    self._clear_editor()
    self.query_one("#meta-name-input", Input).focus()

  def action_save_agent(self) -> None:
    """Save the editor contents (create or update)."""
    data = self._read_editor()
    if data is None:
      return
    if self._editing_id is not None:
      self._save_update(self._editing_id, data)
    else:
      self._save_create(data)

  @work(thread=True)
  def _save_create(self, data) -> None:
    """Create a meta agent in a worker.

    Args:
      data: Dict with name, description, prompt, model,
        timeout_secs.
    """
    from lib import db
    try:
      aid = db.create_meta_agent(
        name=data["name"],
        description=data["description"],
        prompt=data["prompt"],
        model=data["model"],
        timeout_secs=data["timeout_secs"],
      )
      self.app.call_from_thread(
        self._set_status,
        f"Created '{data['name']}'.",
      )
      self.app.call_from_thread(
        self._set_editing_id, aid
      )
      self._load_agents()
    except Exception as e:
      self.app.call_from_thread(
        self._set_status, f"Error: {e}"
      )

  def _set_editing_id(self, aid) -> None:
    """Set the editing ID after a create.

    Args:
      aid: New meta agent row ID.
    """
    self._editing_id = aid

  @work(thread=True)
  def _save_update(self, agent_id, data) -> None:
    """Update a meta agent in a worker.

    Args:
      agent_id: Meta agent row ID.
      data: Dict with fields to update.
    """
    from lib import db
    try:
      db.update_meta_agent(agent_id, **data)
      self.app.call_from_thread(
        self._set_status,
        f"Saved '{data['name']}'.",
      )
      self._load_agents()
    except Exception as e:
      self.app.call_from_thread(
        self._set_status, f"Error: {e}"
      )

  def action_delete_agent(self) -> None:
    """Delete the selected agent after confirmation."""
    agent = self._get_selected_agent()
    if not agent:
      self.app.notify(
        "No agent selected", severity="warning"
      )
      return
    self._confirm(
      f"Delete meta agent '{agent['name']}'?",
      self._on_delete_confirmed,
      str(agent["id"]),
    )

  def _on_delete_confirmed(self, result) -> None:
    """Handle delete confirmation.

    Args:
      result: Agent ID string if confirmed, else None.
    """
    if result is None:
      return
    self._do_delete(int(result))

  @work(thread=True)
  def _do_delete(self, agent_id) -> None:
    """Delete a meta agent in a worker.

    Args:
      agent_id: Meta agent row ID.
    """
    from lib import db
    try:
      db.delete_meta_agent(agent_id)
      self.app.call_from_thread(
        self._set_status, "Agent deleted."
      )
      if self._editing_id == agent_id:
        self.app.call_from_thread(self._clear_editor)
      self._load_agents()
    except Exception as e:
      self.app.call_from_thread(
        self._set_status, f"Error: {e}"
      )

  def action_run_agent(self) -> None:
    """Run the selected meta agent."""
    agent = self._get_selected_agent()
    if not agent:
      self.app.notify(
        "No agent selected", severity="warning"
      )
      return
    client = getattr(self.app, 'service', None)
    if client:
      asyncio.ensure_future(
        self._run_via_service(agent["id"])
      )
    else:
      self._run_local(agent["id"])

  async def _run_via_service(self, agent_id) -> None:
    """Send run_meta_agent command to service.

    Args:
      agent_id: Meta agent row ID.
    """
    try:
      reply = await self.app.service.send_cmd(
        "run_meta_agent", meta_agent_id=agent_id,
      )
      if reply.get("status") == "ok":
        run_id = reply["data"]["run_id"]
        self.app.notify(f"Started run #{run_id}")
        self._load_runs(agent_id)
      else:
        self.app.notify(
          reply.get("message", "Run failed"),
          severity="error",
        )
    except Exception as e:
      log.error(
        "run_meta_agent failed: %s", e, exc_info=True
      )
      self.app.notify(
        f"Run failed: {e}", severity="error"
      )

  @work(thread=True)
  def _run_local(self, agent_id) -> None:
    """Run meta agent locally via DB + executor.

    Args:
      agent_id: Meta agent row ID.
    """
    from lib import db
    from lib.meta_runner import MetaAgentExecutor
    agent = db.get_meta_agent(agent_id)
    if agent is None:
      self.app.call_from_thread(
        self.app.notify,
        "Agent not found",
        severity="error",
      )
      return
    run_id = db.create_meta_agent_run(agent_id)
    self.app.call_from_thread(
      self.app.notify, f"Started run #{run_id}"
    )
    self.app.call_from_thread(
      self._load_runs, agent_id
    )
    import asyncio as _asyncio
    loop = _asyncio.new_event_loop()
    try:
      executor = MetaAgentExecutor(run_id, agent)
      loop.run_until_complete(executor.execute())
    except Exception as e:
      log.error(
        "Local meta run failed: %s", e, exc_info=True
      )
    finally:
      loop.close()
    self.app.call_from_thread(
      self._load_runs, agent_id
    )

  def action_cancel_run(self) -> None:
    """Cancel the selected run."""
    if self._selected_run_id is None:
      self.app.notify(
        "No run selected", severity="warning"
      )
      return
    run_id = self._selected_run_id
    client = getattr(self.app, 'service', None)
    if client:
      asyncio.ensure_future(
        self._cancel_via_service(run_id)
      )
    else:
      self._cancel_local(run_id)

  async def _cancel_via_service(self, run_id) -> None:
    """Send cancel_meta_run command to service.

    Args:
      run_id: Meta agent run row ID.
    """
    try:
      reply = await self.app.service.send_cmd(
        "cancel_meta_run", run_id=run_id,
      )
      if reply.get("status") == "ok":
        self.app.notify(f"Cancelled run #{run_id}")
      else:
        self.app.notify(
          reply.get("message", "Cancel failed"),
          severity="error",
        )
    except Exception as e:
      self.app.notify(
        f"Cancel failed: {e}", severity="error"
      )

  @work(thread=True)
  def _cancel_local(self, run_id) -> None:
    """Cancel a run locally via DB.

    Args:
      run_id: Meta agent run row ID.
    """
    from lib import db
    try:
      db.advance_meta_run(run_id, "cancelled")
      self.app.call_from_thread(
        self.app.notify,
        f"Cancelled run #{run_id}",
      )
    except Exception as e:
      self.app.call_from_thread(
        self.app.notify,
        f"Cancel failed: {e}",
        severity="error",
      )
    if self._selected_agent_id:
      self.app.call_from_thread(
        self._load_runs, self._selected_agent_id
      )

  # -- Helpers --

  def _get_selected_agent(self):
    """Return the selected meta agent dict, or None.

    Returns:
      Meta agent dict, or None.
    """
    table = self.query_one(
      "#meta-agents-table", DataTable
    )
    if table.row_count == 0:
      return None
    try:
      row_idx = table.cursor_row
      coord = table.coordinate_to_cell_key(
        (row_idx, 0)
      )
      key = int(coord[0].value)
      return next(
        (a for a in self._agents if a["id"] == key),
        None,
      )
    except Exception:
      return None

  def on_meta_update(self, data) -> None:
    """Handle meta.update events from service.

    Args:
      data: Dict with run_id and status.
    """
    if self._selected_agent_id is not None:
      self._load_runs(self._selected_agent_id)
