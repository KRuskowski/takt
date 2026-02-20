"""Targets panel widget."""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static
from textual import work


class TargetsPanel(Vertical):
  """Panel showing build/test targets."""

  DEFAULT_CSS = """
  TargetsPanel {
    border: solid $accent;
    padding: 0 1;
  }
  """

  def compose(self) -> ComposeResult:
    yield Static("Targets", classes="panel-title")
    yield DataTable(id="targets-table")

  def on_mount(self) -> None:
    table = self.query_one("#targets-table", DataTable)
    table.cursor_type = "row"
    table.add_columns("Name", "Type", "Host", "Claimed By")

  @work(thread=True)
  def refresh_data(self) -> None:
    """Load target data in a worker thread."""
    from lib.target_ops import get_all_targets
    targets = get_all_targets()
    self.app.call_from_thread(self._update_table, targets)

  def _update_table(self, targets) -> None:
    """Update the table with fresh target data."""
    table = self.query_one("#targets-table", DataTable)
    table.clear()
    for t in targets:
      lock = t["lock"]
      claimed = lock["workspace"] if lock else "-"
      table.add_row(
        t["name"],
        t["type"],
        t["host"],
        claimed,
        key=t["name"],
      )
    # Store targets for detail lookup.
    self._targets = {t["name"]: t for t in targets}

  def get_selected_target(self):
    """Return the name of the currently selected target."""
    table = self.query_one("#targets-table", DataTable)
    if table.row_count == 0:
      return None
    try:
      row_key, _ = table.coordinate_to_cell_key(
        table.cursor_coordinate
      )
      return str(row_key.value)
    except Exception:
      return None

  def on_data_table_row_selected(
    self, event: DataTable.RowSelected
  ) -> None:
    """Show target detail when a row is selected."""
    name = str(event.row_key.value)
    target = getattr(self, "_targets", {}).get(name)
    if not target:
      return

    lock = target["lock"]
    lines = [
      f"## Target: {name}",
      f"",
      f"  Type:        {target['type']}",
      f"  Host:        {target['host']}",
      f"  User:        {target.get('user', '-')}",
      f"  Port:        {target.get('port') or 'default'}",
      f"  Description: {target['description']}",
      f"",
    ]
    if lock:
      lines.extend([
        f"  Claimed by:  {lock['workspace']}",
        f"  Claimed at:  {lock['claimed_at']}",
      ])
    else:
      lines.append("  Claimed by:  (none)")

    self.app.update_detail("\n".join(lines))
