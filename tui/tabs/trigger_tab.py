"""Trigger tab — manual workflow actions.

Sends trigger_stage commands to takt-service when
connected. Falls back to local marker scanning otherwise.
"""

import asyncio
import logging

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Static
from textual import work

log = logging.getLogger("takt.trigger_tab")


class TriggerTab(Static):
  """Workflow action buttons and stage/run overview."""

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
        "Trigger Stage", variant="primary",
        id="btn-trigger-stage",
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
        "Add Stage", variant="default",
        id="btn-add-stage",
      )
    with Vertical(classes="trigger-section"):
      yield Static("Stages", classes="trigger-label")
      yield DataTable(id="trigger-stages-table")
    with Vertical(classes="trigger-section"):
      yield Static("Recent Runs", classes="trigger-label")
      yield DataTable(id="trigger-runs-table")

  def on_mount(self) -> None:
    """Set up tables and load data."""
    stages_t = self.query_one(
      "#trigger-stages-table", DataTable
    )
    stages_t.cursor_type = "row"
    stages_t.add_columns(
      "Workspace", "Role", "Repos", "Status"
    )

    runs_t = self.query_one(
      "#trigger-runs-table", DataTable
    )
    runs_t.cursor_type = "row"
    runs_t.add_columns(
      "Workspace", "Status", "Stages", "Started"
    )
    self.refresh_data()

  @work(thread=True)
  def refresh_data(self) -> None:
    """Load stages and runs in worker thread."""
    from lib.workspace_ops import list_stages
    from lib.run_log import list_all_runs
    stages = list_stages()
    runs = list_all_runs(limit=20)
    self.app.call_from_thread(
      self._populate, stages, runs
    )

  def _populate(self, stages, runs) -> None:
    """Populate tables."""
    stages_t = self.query_one(
      "#trigger-stages-table", DataTable
    )
    stages_t.clear()
    for s in stages:
      repos = ", ".join(s.get("repos", [])[:3])
      stages_t.add_row(
        s.get("workspace", ""),
        s.get("role", ""),
        repos,
        s.get("status", "idle"),
      )

    runs_t = self.query_one(
      "#trigger-runs-table", DataTable
    )
    runs_t.clear()
    for r in runs:
      stage_names = ", ".join(
        r.get("stages", {}).keys()
      )
      runs_t.add_row(
        r.get("workspace", ""),
        r.get("status", "?"),
        stage_names,
        r.get("started", ""),
      )

  def on_button_pressed(
    self, event: Button.Pressed
  ) -> None:
    """Handle action button presses."""
    if event.button.id == "btn-trigger-stage":
      from tui.screens import TriggerStageScreen
      self.app.push_screen(
        TriggerStageScreen(),
        callback=self._on_stage_triggered,
      )
    elif event.button.id == "btn-push-github":
      from tui.screens import PushGithubScreen
      self.app.push_screen(PushGithubScreen())
    elif event.button.id == "btn-new-ws":
      from tui.screens import CreateWorkspaceScreen
      self.app.push_screen(CreateWorkspaceScreen())
    elif event.button.id == "btn-add-stage":
      from tui.screens import AddStageScreen
      self.app.push_screen(AddStageScreen())

  def _on_stage_triggered(self, result) -> None:
    """Handle trigger stage callback.

    Sends trigger_stage command to the service if
    connected, otherwise falls back to local triggering.
    """
    if not result:
      return
    self.refresh_data()
    ws = result.get("workspace")
    role = result.get("role")
    if not ws or not role:
      return
    client = getattr(self.app, 'service', None)
    if client:
      asyncio.ensure_future(
        self._trigger_via_service(ws, role)
      )
    else:
      self._trigger_local(ws, role)

  async def _trigger_via_service(self, ws, role):
    """Send trigger_stage command to service.

    Args:
      ws: Workspace name.
      role: Stage role.
    """
    try:
      reply = await self.app.service.send_cmd(
        "trigger_stage", workspace=ws, role=role,
      )
      if reply.get("status") == "ok":
        self.app.notify(f"Triggered {ws}/{role}")
      else:
        self.app.notify(
          reply.get("message", "Trigger failed"),
          severity="error",
        )
    except Exception as e:
      log.error(
        "trigger_stage failed: %s", e, exc_info=True
      )
      self.app.notify(
        f"Trigger failed: {e}", severity="error"
      )

  def _trigger_local(self, ws, role):
    """Local fallback: scan markers and launch agent.

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
