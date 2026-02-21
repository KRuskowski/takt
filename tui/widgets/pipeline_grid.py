"""Pipeline grid widget — 2D view of workspaces x stages."""

import time

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static
from textual import work

from tui.widgets.style_utils import age_label, age_style, ws_bucket


def _parse_stage_session(cwd, stages_dir):
  """Parse a session cwd into (workspace, role) if under stages.

  Args:
    cwd: Absolute path from session.cwd.
    stages_dir: Absolute path to the stages directory.

  Returns:
    Tuple of (workspace, role) or None if not a stage path.
  """
  prefix = str(stages_dir) + "/"
  if not cwd.startswith(prefix):
    return None
  rest = cwd[len(prefix):]
  parts = rest.split("/", 2)
  if len(parts) < 2:
    return None
  return (parts[0], parts[1])


def _build_columns(workspaces, get_pipeline_fn):
  """Build ordered column list from all workspace pipelines.

  Columns are the union of all pipeline stages, preserving
  first-seen order.

  Args:
    workspaces: List of workspace dicts from list_workspaces().
    get_pipeline_fn: Callable taking workspace name, returning
      pipeline dict with 'stages' key.

  Returns:
    List of role slugs in column order.
  """
  columns = []
  seen = set()
  for ws in workspaces:
    try:
      pipeline = get_pipeline_fn(ws["name"])
    except FileNotFoundError:
      continue
    for role in pipeline.get("stages", []):
      if role not in seen:
        columns.append(role)
        seen.add(role)
  return columns


def _build_active_map(sessions, stages_dir):
  """Map active sessions to (workspace, role) pairs.

  Args:
    sessions: List of SessionInfo from discover_sessions().
    stages_dir: Absolute path to the stages directory.

  Returns:
    Set of (workspace, role) tuples with active agents.
  """
  active = set()
  for s in sessions:
    if not s.is_active or not s.cwd:
      continue
    parsed = _parse_stage_session(s.cwd, stages_dir)
    if parsed:
      active.add(parsed)
  return active


class PipelineGridPanel(Vertical):
  """2D grid: workspaces on Y, pipeline stages on X."""

  DEFAULT_CSS = """
  PipelineGridPanel {
    padding: 0 1;
  }
  """

  def compose(self) -> ComposeResult:
    yield Static("Pipeline Grid", classes="panel-title")
    yield DataTable(
      id="pipeline-grid-table",
      cursor_foreground_priority="renderable",
    )

  def on_mount(self) -> None:
    table = self.query_one(
      "#pipeline-grid-table", DataTable
    )
    table.cursor_type = "row"

  @work(thread=True)
  def refresh_data(self) -> None:
    """Load grid data in a worker thread."""
    from lib.config import STAGES_DIR
    from lib.session_parser import discover_sessions
    from lib.workspace_ops import (
      get_pipeline,
      list_stages,
      list_workspaces,
    )

    workspaces = list_workspaces()
    stages = list_stages()
    sessions = discover_sessions()
    columns = _build_columns(workspaces, get_pipeline)
    active_map = _build_active_map(
      sessions, str(STAGES_DIR)
    )

    # Index stages by (workspace, role).
    stage_index = {}
    for s in stages:
      stage_index[(s["workspace"], s["role"])] = s

    # Build row data.
    rows = []
    for ws in workspaces:
      cells = []
      for role in columns:
        key = (ws["name"], role)
        if key in active_map:
          cells.append(("active", "#66bb6a"))
        elif key in stage_index:
          st = stage_index[key]
          last = st.get("last_active", 0.0)
          if last > 0:
            age_min = (time.time() - last) / 60
            bucket = ws_bucket(age_min)
            style = age_style(bucket)
            label = age_label(age_min)
          else:
            style = "#666666"
            label = "idle"
          cells.append((label, style))
        else:
          cells.append(("-", "#444444"))
      rows.append((ws["name"], cells))

    self.app.call_from_thread(
      self._update_table, columns, rows
    )

  def _update_table(self, columns, rows) -> None:
    """Rebuild the table with dynamic columns."""
    table = self.query_one(
      "#pipeline-grid-table", DataTable
    )
    table.clear(columns=True)
    table.add_column("Workspace", key="workspace")
    for role in columns:
      table.add_column(role, key=role)

    for ws_name, cells in rows:
      row_cells = [Text(ws_name, style="#cccccc")]
      for label, style in cells:
        row_cells.append(Text(label, style=style))
      table.add_row(*row_cells, key=ws_name)

    # Update title with count.
    title = self.query_one(".panel-title", Static)
    title.update(f"Pipeline Grid ({len(rows)} workspaces)")
