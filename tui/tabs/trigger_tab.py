"""Trigger tab — manual workflow actions.

Reads pipeline definitions and run history from SQLite.
Sends trigger_run commands to takt-service when connected.
"""

import asyncio
import logging

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Static
from textual import work

log = logging.getLogger("takt.trigger_tab")


class TriggerTab(Static):
  """Workflow action buttons and pipeline/run overview."""

  DEFAULT_CSS = """
  TriggerTab {
    height: 1fr;
    padding: 1 2;
  }

  TriggerTab #trigger-buttons {
    height: auto;
    margin: 0 0 1 0;
  }

  TriggerTab #trigger-buttons Button {
    margin: 0 1 0 0;
  }

  TriggerTab .trigger-section {
    margin: 1 0;
  }

  TriggerTab .trigger-label {
    text-style: bold;
    color: #cccccc;
    margin: 0 0 1 0;
  }

  TriggerTab DataTable {
    height: auto;
    max-height: 15;
    background: #101010;
  }
  """

  def compose(self) -> ComposeResult:
    with Horizontal(id="trigger-buttons"):
      yield Button(
        "Trigger Run", variant="primary",
        id="btn-trigger-run",
      )
      yield Button(
        "Push to GitHub", variant="warning",
        id="btn-push-github",
      )
      yield Button(
        "New Workspace", variant="default",
        id="btn-new-ws",
      )
      yield Button(
        "Setup Pipeline", variant="default",
        id="btn-setup-pipeline",
      )
    with Vertical(classes="trigger-section"):
      yield Static("Pipelines", classes="trigger-label")
      yield DataTable(id="trigger-pipelines-table")
    with Vertical(classes="trigger-section"):
      yield Static("Recent Runs", classes="trigger-label")
      yield DataTable(id="trigger-runs-table")

  def on_mount(self) -> None:
    """Set up tables and load data."""
    pipelines_t = self.query_one(
      "#trigger-pipelines-table", DataTable
    )
    pipelines_t.cursor_type = "row"
    pipelines_t.add_columns(
      "Workspace", "Steps", "Type"
    )

    runs_t = self.query_one(
      "#trigger-runs-table", DataTable
    )
    runs_t.cursor_type = "row"
    runs_t.add_columns(
      "ID", "Workspace", "Status", "Trigger", "Created"
    )
    self.refresh_data()

  @work(thread=True)
  def refresh_data(self) -> None:
    """Load pipelines and runs from SQLite."""
    from lib import db
    from lib.workspace_ops import list_workspaces
    workspaces = list_workspaces()
    pipelines = []
    for ws in workspaces:
      steps = db.get_pipeline(ws["name"])
      if steps:
        pipelines.append({
          "workspace": ws["name"],
          "steps": ", ".join(s["name"] for s in steps),
          "types": ", ".join(
            s["step_type"] for s in steps
          ),
        })
    runs = db.list_runs(limit=20)
    self.app.call_from_thread(
      self._populate, pipelines, runs
    )

  def _populate(self, pipelines, runs) -> None:
    """Populate tables."""
    pipelines_t = self.query_one(
      "#trigger-pipelines-table", DataTable
    )
    pipelines_t.clear()
    for p in pipelines:
      pipelines_t.add_row(
        p["workspace"],
        p["steps"],
        p["types"],
      )

    runs_t = self.query_one(
      "#trigger-runs-table", DataTable
    )
    runs_t.clear()
    for r in runs:
      created = r.get("created_at", "")[:19]
      runs_t.add_row(
        str(r.get("id", "")),
        r.get("workspace", ""),
        r.get("status", "?"),
        r.get("trigger", ""),
        created,
      )

  def on_button_pressed(
    self, event: Button.Pressed
  ) -> None:
    """Handle action button presses."""
    if event.button.id == "btn-trigger-run":
      from tui.screens import TriggerRunScreen
      self.app.push_screen(
        TriggerRunScreen(),
        callback=self._on_run_triggered,
      )
    elif event.button.id == "btn-push-github":
      from tui.screens import PushGithubScreen
      self.app.push_screen(PushGithubScreen())
    elif event.button.id == "btn-new-ws":
      from tui.screens import CreateWorkspaceScreen
      self.app.push_screen(CreateWorkspaceScreen())
    elif event.button.id == "btn-setup-pipeline":
      from tui.screens import PipelineSetupScreen
      self.app.push_screen(
        PipelineSetupScreen(),
        callback=self._on_pipeline_saved,
      )

  def _on_pipeline_saved(self, result) -> None:
    """Refresh data after pipeline setup."""
    if result:
      self.refresh_data()

  def _on_run_triggered(self, result) -> None:
    """Handle trigger run callback.

    Sends trigger_run command to the service if
    connected.
    """
    if not result:
      return
    self.refresh_data()
    ws = result.get("workspace")
    if not ws:
      return
    client = getattr(self.app, 'service', None)
    if client:
      asyncio.ensure_future(
        self._trigger_via_service(ws)
      )

  async def _trigger_via_service(self, ws):
    """Send trigger_run command to service.

    Args:
      ws: Workspace name.
    """
    try:
      reply = await self.app.service.send_cmd(
        "trigger_run", workspace=ws,
      )
      if reply.get("status") == "ok":
        self.app.notify(f"Triggered run for {ws}")
      else:
        self.app.notify(
          reply.get("message", "Trigger failed"),
          severity="error",
        )
    except Exception as e:
      log.error(
        "trigger_run failed: %s", e, exc_info=True
      )
      self.app.notify(
        f"Trigger failed: {e}", severity="error"
      )
