"""Workspaces panel widget."""

from textual.app import ComposeResult
from textual.widgets import DataTable, Static
from textual.containers import Vertical
from textual import work


class WorkspacesPanel(Vertical):
  """Panel showing all workspaces."""

  DEFAULT_CSS = """
  WorkspacesPanel {
    padding: 0 1;
  }
  """

  def compose(self) -> ComposeResult:
    yield Static("Workspaces", classes="panel-title")
    yield DataTable(id="workspaces-table")

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
      table.add_row(
        ws["name"],
        repos_str,
        ws["branch"],
        "active",
        key=ws["name"],
      )

