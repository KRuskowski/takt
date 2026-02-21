"""Stages panel widget for all pipeline stages."""

import time

from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import DataTable, Static
from textual.containers import Vertical
from textual import work

from tui.widgets.style_utils import age_label, age_style, ws_bucket


class StagesPanel(Vertical):
  """Panel showing all pipeline stages."""

  DEFAULT_CSS = """
  StagesPanel {
    padding: 0 1;
  }
  """

  def compose(self) -> ComposeResult:
    yield Static("Stages", classes="panel-title")
    yield DataTable(
      id="stages-table",
      cursor_foreground_priority="renderable",
    )

  def on_mount(self) -> None:
    table = self.query_one("#stages-table", DataTable)
    table.cursor_type = "row"
    table.add_columns(
      "Workspace", "Role", "Repos", "Branch", "Status",
    )

  @work(thread=True)
  def refresh_data(self) -> None:
    """Load stage data in a worker thread."""
    from lib.workspace_ops import list_stages
    stages = list_stages()
    self.app.call_from_thread(self._update_table, stages)

  def _update_table(self, stages) -> None:
    """Update the table with fresh data."""
    table = self.query_one("#stages-table", DataTable)
    table.clear()
    for s in stages:
      repos_str = ", ".join(s["repos"][:3])
      if len(s["repos"]) > 3:
        repos_str += f" +{len(s['repos']) - 3}"

      last = s.get("last_active", 0.0)
      if last > 0:
        age_min = (time.time() - last) / 60
      else:
        age_min = float("inf")
      bucket = ws_bucket(age_min)
      style = age_style(bucket)
      label = age_label(age_min) if last > 0 else "unknown"

      table.add_row(
        Text(s["workspace"], style=style),
        Text(s["role"], style=style),
        Text(repos_str, style=style),
        Text(s["branch"], style=style),
        Text(label, style=style),
        key=f"{s['workspace']}:{s['role']}",
      )
