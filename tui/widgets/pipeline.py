"""Pipeline panel widget for monitoring events.

Shows recent pipeline events from the SQLite event log.
Receives live events from takt-service via PUB/SUB when
connected.
"""

from collections import deque
from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static
from textual import work

MAX_EVENTS = 50


class PipelinePanel(Vertical):
  """Panel showing pipeline events."""

  DEFAULT_CSS = """
  PipelinePanel {
    padding: 0 1;
  }
  """

  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    self._events = deque(maxlen=MAX_EVENTS)

  def compose(self) -> ComposeResult:
    yield Static(
      "Pipeline (watching)", classes="panel-title",
      id="pipeline-title",
    )
    yield DataTable(id="pipeline-table")

  def on_mount(self) -> None:
    """Set up table and seed from SQLite event log."""
    table = self.query_one("#pipeline-table", DataTable)
    table.cursor_type = "row"
    table.add_columns("Time", "Entity", "Transition")
    self._seed_events()

  @work(thread=True)
  def _seed_events(self) -> None:
    """Load recent events from SQLite."""
    from lib import db
    events = db.get_events(limit=MAX_EVENTS)
    display = []
    for ev in events:
      ts = ev.get("ts", "")[:19]
      entity = f"{ev['entity']} {ev['entity_id']}"
      old = ev.get("old_status", "")
      new = ev.get("new_status", "")
      transition = f"{old} -> {new}" if old else new
      reason = ev.get("reason", "")
      if reason:
        transition += f" ({reason[:30]})"
      display.append({
        "time": ts,
        "entity": entity,
        "transition": transition,
      })
    self.app.call_from_thread(
      self._set_events, display
    )

  def _set_events(self, events) -> None:
    """Set initial events."""
    self._events.clear()
    for ev in events:
      self._events.append(ev)
    self._update_table()

  def on_service_event(self, data) -> None:
    """Handle a pipeline.event from the service.

    Args:
      data: Event dict with time, workspace, event.
    """
    if "time" not in data:
      data["time"] = datetime.now().strftime(
        "%Y-%m-%dT%H:%M:%S"
      )
    self._events.appendleft({
      "time": data.get("time", ""),
      "entity": data.get("workspace", ""),
      "transition": data.get("event", ""),
    })
    self._update_table()

  def refresh_data(self) -> None:
    """Reload events from SQLite."""
    self._seed_events()

  def _update_table(self) -> None:
    """Render event log to the table."""
    table = self.query_one("#pipeline-table", DataTable)
    table.clear()
    for ev in self._events:
      table.add_row(
        ev.get("time", ""),
        ev.get("entity", ""),
        ev.get("transition", ""),
      )
