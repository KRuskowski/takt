"""PRs panel widget."""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static
from textual import work


class PrsPanel(Vertical):
  """Panel showing open GitHub PRs across workspaces."""

  DEFAULT_CSS = """
  PrsPanel {
    padding: 0 1;
  }
  """

  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    self._gh_available = True

  def compose(self) -> ComposeResult:
    yield Static("PRs", classes="panel-title", id="prs-title")
    yield DataTable(id="prs-table")

  def on_mount(self) -> None:
    table = self.query_one("#prs-table", DataTable)
    table.cursor_type = "row"
    table.add_columns(
      "Workspace", "Repo", "PR", "Title",
      "Status", "Mergeable",
    )

  @work(thread=True)
  def refresh_data(self) -> None:
    """Load PR data in a worker thread."""
    from lib.pr_ops import list_all_prs
    rows, available = list_all_prs()
    self._gh_available = available
    self.app.call_from_thread(self._update_table, rows)

  def _update_table(self, rows) -> None:
    """Update the table and title with fresh PR data."""
    title = self.query_one("#prs-title", Static)
    if not self._gh_available:
      title.update("PRs (gh unavailable)")
    else:
      title.update(f"PRs ({len(rows)})")

    table = self.query_one("#prs-table", DataTable)
    table.clear()
    for r in rows:
      status = "Draft" if r["is_draft"] else "Open"
      mergeable = r["mergeable"]
      table.add_row(
        r["workspace"],
        r["repo"],
        f"#{r['number']}",
        r["title"],
        status,
        mergeable,
      )
