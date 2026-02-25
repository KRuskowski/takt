"""Workspaces panel widget."""

import time
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.widgets import DataTable, Static, TabbedContent
from textual.containers import Vertical
from textual import work

from tui.widgets.style_utils import (
  age_label, age_style, ws_bucket,
)

# Pipeline status display.
_RUN_STYLE = {
  "queued": "#ffb74d",
  "running": "#42a5f5",
  "passed": "#4caf50",
  "failed": "#ef5350",
  "cancelled": "#9e9e9e",
}


class WorkspacesPanel(Vertical):
  """Panel showing all workspaces with pipeline status."""

  BINDINGS = [
    Binding("t", "trigger_workspace", "Trigger"),
    Binding("p", "setup_pipeline", "Pipeline"),
  ]

  DEFAULT_CSS = """
  WorkspacesPanel {
    padding: 0 1;
  }
  """

  def compose(self) -> ComposeResult:
    yield Static("Workspaces", classes="panel-title")
    yield DataTable(
      id="workspaces-table",
      cursor_foreground_priority="renderable",
    )

  def on_mount(self) -> None:
    table = self.query_one(
      "#workspaces-table", DataTable
    )
    table.cursor_type = "row"
    table.add_columns(
      "Name", "Branch", "Git", "Pipeline", "Activity",
    )

  @work(thread=True)
  def refresh_data(self) -> None:
    """Load workspace data in a worker thread."""
    from lib import db
    from lib.git_status import workspace_git_summary
    from lib.workspace_ops import list_workspaces

    workspaces = list_workspaces()
    for ws in workspaces:
      ws["git"] = workspace_git_summary(
        Path(ws["path"]), ws["repos"],
      )
      # Latest pipeline run status.
      runs = db.list_runs(ws["name"], limit=1)
      if runs:
        ws["run_status"] = runs[0]["status"]
      else:
        pipeline = db.get_pipeline(ws["name"])
        ws["run_status"] = "—" if pipeline else ""
    self.app.call_from_thread(
      self._update_table, workspaces
    )

  def _update_table(self, workspaces) -> None:
    """Update the table with fresh data."""
    table = self.query_one(
      "#workspaces-table", DataTable
    )
    table.clear()
    for ws in workspaces:
      last = ws.get("last_active", 0.0)
      if last > 0:
        age_min = (time.time() - last) / 60
      else:
        age_min = float("inf")
      bucket = ws_bucket(age_min)
      style = age_style(bucket)
      activity = (
        age_label(age_min) if last > 0 else "unknown"
      )

      git_label = ws.get("git", "ok")
      git_style = (
        "#ffb74d" if git_label != "ok" else style
      )

      run_status = ws.get("run_status", "")
      run_style = _RUN_STYLE.get(run_status, style)

      table.add_row(
        Text(ws["name"], style=style),
        Text(ws.get("branch", "?"), style=style),
        Text(git_label, style=git_style),
        Text(run_status, style=run_style),
        Text(activity, style=style),
        key=ws["name"],
      )

  def action_trigger_workspace(self) -> None:
    """Switch to Trigger tab and preselect workspace."""
    ws_name = self._get_selected_workspace()
    if not ws_name:
      return
    try:
      tabs = self.app.query_one(
        "#tabs", TabbedContent
      )
      tabs.active = "tab-trigger"
      from tui.tabs.trigger_tab import TriggerTab
      trigger = self.app.query_one(
        "#trigger-tab", TriggerTab
      )
      trigger.trigger_for_workspace(ws_name)
    except NoMatches:
      pass

  def _get_selected_workspace(self) -> str | None:
    """Return the name of the currently selected row."""
    table = self.query_one(
      "#workspaces-table", DataTable
    )
    if table.row_count == 0:
      return None
    try:
      row_idx = table.cursor_row
      coord = table.coordinate_to_cell_key(
        (row_idx, 0)
      )
      return str(coord[0].value)
    except Exception:
      return None

  def action_setup_pipeline(self) -> None:
    """Switch to Pipeline tab and preselect workspace."""
    ws_name = self._get_selected_workspace()
    if not ws_name:
      return
    try:
      tabs = self.app.query_one(
        "#tabs", TabbedContent
      )
      tabs.active = "tab-pipeline"
      from tui.tabs.pipeline_tab import PipelineTab
      pipeline = self.app.query_one(
        "#pipeline-tab", PipelineTab
      )
      pipeline.select_workspace(ws_name)
    except NoMatches:
      pass
