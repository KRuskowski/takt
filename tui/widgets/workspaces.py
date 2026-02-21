"""Workspaces panel widget."""

import time

from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import DataTable, Static
from textual.containers import Vertical
from textual import work

from tui.widgets.style_utils import age_label, age_style, ws_bucket


class WorkspacesPanel(Vertical):
  """Panel showing all workspaces."""

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
    table = self.query_one("#workspaces-table", DataTable)
    table.cursor_type = "row"
    table.add_columns("Name", "Repos", "Branch", "Status")

  @work(thread=True)
  def refresh_data(self) -> None:
    """Load workspace data in a worker thread."""
    from lib.workspace_ops import list_workspaces
    workspaces = list_workspaces()
    self.app.call_from_thread(self._update_table, workspaces)

  def _update_table(self, workspaces) -> None:
    """Update the table with fresh data."""
    table = self.query_one("#workspaces-table", DataTable)
    table.clear()
    for ws in workspaces:
      repos_str = ", ".join(ws["repos"][:3])
      if len(ws["repos"]) > 3:
        repos_str += f" +{len(ws['repos']) - 3}"

      last = ws.get("last_active", 0.0)
      if last > 0:
        age_min = (time.time() - last) / 60
      else:
        age_min = float("inf")
      bucket = ws_bucket(age_min)
      style = age_style(bucket)
      label = age_label(age_min) if last > 0 else "unknown"

      table.add_row(
        Text(ws["name"], style=style),
        Text(repos_str, style=style),
        Text(ws["branch"], style=style),
        Text(label, style=style),
        key=ws["name"],
      )
