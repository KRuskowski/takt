"""Stages panel widget for all pipeline stages."""

from textual.app import ComposeResult
from textual.widgets import DataTable, Static
from textual.containers import Vertical
from textual import work


class StagesPanel(Vertical):
  """Panel showing all pipeline stages."""

  DEFAULT_CSS = """
  StagesPanel {
    padding: 0 1;
  }
  """

  def compose(self) -> ComposeResult:
    yield Static("Stages", classes="panel-title")
    yield DataTable(id="stages-table")

  def on_mount(self) -> None:
    table = self.query_one("#stages-table", DataTable)
    table.cursor_type = "row"
    table.add_columns(
      "Workspace", "Role", "Repos", "Branch",
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
      table.add_row(
        s["workspace"],
        s["role"],
        repos_str,
        s["branch"],
        key=f"{s['workspace']}:{s['role']}",
      )
