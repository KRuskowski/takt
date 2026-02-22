"""Pipeline panel widget for monitoring stage triggers."""

from collections import deque
from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static
from textual import work

from bin.pipeline_watch import (
  _prune_finished_tabs,
  _scan_and_sync,
  _scan_and_trigger,
  load_events,
)

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
    yield Static("Pipeline (watching)", classes="panel-title",
                 id="pipeline-title")
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

  @work(thread=True)
  def refresh_data(self) -> None:
    """Poll for markers and trigger agents."""
    now = datetime.now().strftime("%H:%M:%S")
    changed = False
    try:
      for title in _prune_finished_tabs():
        self._events.appendleft({
          "time": now, "stage": title,
          "repos": "", "event": "pruned",
        })
        changed = True
    except Exception as e:
      self._events.appendleft({
        "time": now, "stage": "system",
        "repos": "", "event": f"error: {e}",
      })
      changed = True
    try:
      for ev in _scan_and_trigger():
        ev["time"] = now
        self._events.appendleft(ev)
        changed = True
    except Exception as e:
      self._events.appendleft({
        "time": now, "stage": "system",
        "repos": "", "event": f"error: {e}",
      })
      changed = True
    try:
      for ev in _scan_and_sync():
        ev["time"] = now
        self._events.appendleft(ev)
        changed = True
    except Exception as e:
      self._events.appendleft({
        "time": now, "stage": "system",
        "repos": "", "event": f"error: {e}",
      })
      changed = True
    if changed:
      self.app.call_from_thread(self._update_table)

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
