"""Pipeline panel widget for monitoring stage triggers.

Receives pipeline events from takt-service via PUB/SUB
when connected. Falls back to polling markers directly
when running without the service.
"""

from collections import deque
from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from bin.pipeline_watch import load_events

MAX_EVENTS = 50


class PipelinePanel(Vertical):
  """Panel showing pipeline trigger events."""

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
    """Set up table and seed from persisted event log."""
    table = self.query_one("#pipeline-table", DataTable)
    table.cursor_type = "row"
    table.add_columns("Time", "Stage", "Repos", "Event")
    for ev in load_events()[:MAX_EVENTS]:
      self._events.append(ev)
    if self._events:
      self._update_table()

  def on_service_event(self, data) -> None:
    """Handle a pipeline.event from the service.

    Args:
      data: Event dict with time, stage, repos, event.
    """
    if "time" not in data:
      data["time"] = datetime.now().strftime("%H:%M:%S")
    self._events.appendleft(data)
    self._update_table()

  def refresh_data(self) -> None:
    """Reload events from disk (no-service fallback)."""
    events = load_events()[:MAX_EVENTS]
    self._events.clear()
    for ev in events:
      self._events.append(ev)
    self._update_table()

  def _update_table(self) -> None:
    """Render event log to the table."""
    table = self.query_one("#pipeline-table", DataTable)
    table.clear()
    for ev in self._events:
      table.add_row(
        ev.get("time", ""),
        ev.get("stage", ""),
        ev.get("repos", ""),
        ev.get("event", ""),
      )
