"""Stages panel widget (testing + utility)."""

from textual.app import ComposeResult
from textual.widgets import DataTable, Static
from textual.containers import Vertical
from textual import work


class StagesPanel(Vertical):
  """Panel showing all testing and utility stages."""

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
    table.add_columns("Name", "Type", "Repos", "Branch")

  @work(thread=True)
  def refresh_data(self) -> None:
    """Load stage data in a worker thread."""
    from lib.workspace_ops import (
      list_testing_stages,
      list_utility_stages,
    )
    testing = list_testing_stages()
    utility = list_utility_stages()
    self.app.call_from_thread(
      self._update_table, testing, utility,
    )

  def _update_table(self, testing, utility) -> None:
    """Update the table with fresh data."""
    table = self.query_one("#stages-table", DataTable)
    table.clear()
    for s in testing:
      repos_str = ", ".join(s["repos"][:3])
      if len(s["repos"]) > 3:
        repos_str += f" +{len(s['repos']) - 3}"
      table.add_row(
        s["name"],
        "testing",
        repos_str,
        s["branch"],
        key=f"testing:{s['name']}",
      )
    for s in utility:
      repos_str = ", ".join(s["repos"][:3])
      if len(s["repos"]) > 3:
        repos_str += f" +{len(s['repos']) - 3}"
      table.add_row(
        s["name"],
        "utility",
        repos_str,
        s["branch"],
        key=f"utility:{s['name']}",
      )
