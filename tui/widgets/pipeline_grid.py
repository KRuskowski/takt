"""Pipeline grid widget — step flow diagrams per workspace.

Reads pipeline definitions and run status from SQLite.
Each workspace shows its pipeline steps as a flow diagram
with state-colored boxes and arrows.
"""

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static
from textual import work

# Display state constants.
STATE_STALE = "stale"
STATE_MISSING = "missing"
STATE_PASSED = "passed"
STATE_FAILED = "failed"
STATE_RUNNING = "running"
STATE_TRIGGERED = "triggered"
STATE_PAUSED = "paused"
STATE_SKIPPED = "skipped"

# Icons per state.
_STATE_ICON = {
  STATE_STALE: "·",
  STATE_MISSING: "╌",
  STATE_PASSED: "✓",
  STATE_FAILED: "✗",
  STATE_RUNNING: "●",
  STATE_TRIGGERED: "▸",
  STATE_PAUSED: "║",
  STATE_SKIPPED: "—",
}

# Colors per state.
_STATE_COLOR = {
  STATE_STALE: "#666666",
  STATE_MISSING: "#444444",
  STATE_PASSED: "#4caf50",
  STATE_FAILED: "#ef5350",
  STATE_RUNNING: "#42a5f5",
  STATE_TRIGGERED: "#ffb74d",
  STATE_PAUSED: "#ff9800",
  STATE_SKIPPED: "#9e9e9e",
}

# Map SQLite step status to display state.
_STEP_STATUS_MAP = {
  "pending": STATE_STALE,
  "queued": STATE_TRIGGERED,
  "running": STATE_RUNNING,
  "completed": STATE_PASSED,
  "failed": STATE_FAILED,
  "paused": STATE_PAUSED,
  "skipped": STATE_SKIPPED,
  "cancelled": STATE_FAILED,
}

# Border characters per state.
_BORDER_CHARS = {
  "tl": {"solid": "╭", "dashed": "╭"},
  "tr": {"solid": "╮", "dashed": "╮"},
  "bl": {"solid": "╰", "dashed": "╰"},
  "br": {"solid": "╯", "dashed": "╯"},
  "h_top": {"solid": "─", "dashed": "╌"},
  "h_bot": {"solid": "─", "dashed": "╌"},
  "v": {"solid": "│", "dashed": "┊"},
}


def _build_pipelines(workspaces, get_pipeline_fn):
  """Build per-workspace step name lists.

  Args:
    workspaces: List of workspace dicts with 'name' key.
    get_pipeline_fn: Callable taking workspace name,
      returning list of step dicts with 'name' key.

  Returns:
    Dict mapping workspace name to list of step names
    in pipeline order.
  """
  pipelines = {}
  for ws in workspaces:
    steps = get_pipeline_fn(ws["name"])
    pipelines[ws["name"]] = [s["name"] for s in steps]
  return pipelines


def _node_state(step_name, step_map, has_pipeline):
  """Determine display state for a pipeline step node.

  Args:
    step_name: Step name (column).
    step_map: Dict mapping step name to status string
      from the latest run, or empty dict if no run.
    has_pipeline: Whether this workspace has a pipeline.

  Returns:
    One of the STATE_* constants.
  """
  if not has_pipeline:
    return STATE_MISSING
  if step_name not in step_map:
    return STATE_MISSING
  status = step_map[step_name]
  return _STEP_STATUS_MAP.get(status, STATE_STALE)


def _border_style(state):
  """Return border type for a node state.

  Args:
    state: One of the STATE_* constants.

  Returns:
    'solid' or 'dashed'.
  """
  if state == STATE_MISSING:
    return "dashed"
  return "solid"


def _build_flow(workspaces, pipelines, step_maps):
  """Build a Rich Text flow diagram for all workspaces.

  Each workspace renders as 3 lines with the name inline
  on the middle row beside its own pipeline step boxes.

  Args:
    workspaces: List of workspace dicts with 'name' key.
    pipelines: Dict mapping ws_name to list of step names
      in pipeline order.
    step_maps: Dict mapping ws_name to step status dict.

  Returns:
    Rich Text object with the full flow diagram.
  """
  if not workspaces:
    return Text("")
  max_name = max(len(ws["name"]) for ws in workspaces)
  name_col = max_name + 2
  result = Text()
  for i, ws in enumerate(workspaces):
    if i > 0:
      result.append("\n")
    ws_steps = pipelines.get(ws["name"], [])
    sm = step_maps.get(ws["name"], {})
    _append_workspace_flow(
      result, ws["name"], ws_steps, sm, name_col,
    )
  return result


def _append_workspace_flow(text, ws_name, step_names,
                           step_map, name_col):
  """Append a single workspace's flow diagram to Text.

  Args:
    text: Rich Text to append to.
    ws_name: Workspace name.
    step_names: Ordered list of step names for this
      workspace's pipeline.
    step_map: Dict mapping step name to status string.
    name_col: Width of the name column for alignment.
  """
  has_pipeline = bool(step_names)
  nodes = []
  for step_name in step_names:
    state = _node_state(
      step_name, step_map, has_pipeline,
    )
    inner_w = len(step_name) + 4
    nodes.append((step_name, state, inner_w))

  if not nodes:
    # No pipeline — single "no pipeline" line.
    text.append(ws_name.ljust(name_col), style="bold")
    text.append("(no pipeline)\n", style="#666666")
    return

  pad = " " * name_col

  # Line 1: top borders.
  text.append(pad)
  for j, (name, state, inner_w) in enumerate(nodes):
    if j > 0:
      text.append("   ")
    bstyle = _border_style(state)
    color = _STATE_COLOR[state]
    tl = _BORDER_CHARS["tl"][bstyle]
    tr = _BORDER_CHARS["tr"][bstyle]
    h = _BORDER_CHARS["h_top"][bstyle] * inner_w
    text.append(f"{tl}{h}{tr}", style=color)
  text.append("\n")

  # Line 2: name + middle row with icons and arrows.
  text.append(ws_name.ljust(name_col), style="bold")
  for j, (name, state, inner_w) in enumerate(nodes):
    if j > 0:
      text.append("──▸")
    color = _STATE_COLOR[state]
    bstyle = _border_style(state)
    icon = _STATE_ICON[state]
    v = _BORDER_CHARS["v"][bstyle]
    text.append(v, style=color)
    text.append(f" {icon} {name} ", style=color)
    text.append(v, style=color)
  text.append("──▸ root\n")

  # Line 3: bottom borders.
  text.append(pad)
  for j, (name, state, inner_w) in enumerate(nodes):
    if j > 0:
      text.append("   ")
    bstyle = _border_style(state)
    color = _STATE_COLOR[state]
    bl = _BORDER_CHARS["bl"][bstyle]
    br = _BORDER_CHARS["br"][bstyle]
    h = _BORDER_CHARS["h_bot"][bstyle] * inner_w
    text.append(f"{bl}{h}{br}", style=color)
  text.append("\n")


class PipelineGridPanel(Vertical):
  """Arrow flow pipeline diagram per workspace."""

  BINDINGS = [
    Binding("t", "trigger_run", "Trigger"),
  ]

  DEFAULT_CSS = """
  PipelineGridPanel {
    padding: 0 1;
  }
  """

  def compose(self) -> ComposeResult:
    with VerticalScroll():
      yield Static(id="flow")

  @work(thread=True)
  def refresh_data(self) -> None:
    """Load grid data from SQLite in a worker thread."""
    from lib import db
    from lib.workspace_ops import list_workspaces

    workspaces = list_workspaces()
    pipelines = _build_pipelines(
      workspaces, db.get_pipeline
    )

    step_maps = {}
    for ws in workspaces:
      runs = db.list_runs(ws["name"], limit=1)
      if runs:
        steps = db.get_run_steps(runs[0]["id"])
        step_maps[ws["name"]] = {
          s["name"]: s["status"] for s in steps
        }

    flow_text = _build_flow(
      workspaces, pipelines, step_maps,
    )
    self.app.call_from_thread(
      self._update_flow, flow_text
    )

  def _update_flow(self, flow_text) -> None:
    """Replace flow content."""
    static = self.query_one("#flow", Static)
    static.update(flow_text)

  def action_trigger_run(self) -> None:
    """Open the trigger run modal."""
    from tui.screens import TriggerRunScreen
    self.app.push_screen(TriggerRunScreen())
