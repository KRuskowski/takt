"""Workspaces panel widget."""

from textual.app import ComposeResult
from textual.widgets import DataTable, Static
from textual.containers import Vertical
from textual import work


class WorkspacesPanel(Vertical):
  """Panel showing all workspaces."""

  DEFAULT_CSS = """
  WorkspacesPanel {
    border: solid $accent;
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

  def on_data_table_row_selected(
    self, event: DataTable.RowSelected
  ) -> None:
    """Show workspace detail when a row is selected."""
    name = str(event.row_key.value)
    self._load_detail(name)

  @work(thread=True)
  def _load_detail(self, name: str) -> None:
    """Load workspace detail in a worker thread."""
    from lib.workspace_ops import get_workspace_status
    try:
      statuses = get_workspace_status(name)
    except FileNotFoundError:
      self.app.call_from_thread(
        self.app.update_detail,
        f"Workspace '{name}' not found.",
      )
      return

    lines = [f"## Workspace: {name}\n"]
    for s in statuses:
      lines.append(
        f"  {s['repo']:<25} {s['branch']:<20} {s['status']}"
      )

    # Try to read session state from CLAUDE.md.
    from lib.config import WORKSPACES_DIR
    claude_md = WORKSPACES_DIR / name / "CLAUDE.md"
    if claude_md.exists():
      text = claude_md.read_text()
      # Extract session state section.
      marker = "## Session State"
      idx = text.find(marker)
      if idx >= 0:
        lines.append(f"\n{text[idx:]}")

    self.app.call_from_thread(
      self.app.update_detail, "\n".join(lines)
    )
