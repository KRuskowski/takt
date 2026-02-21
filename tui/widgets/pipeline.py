"""Pipeline panel widget for monitoring stage triggers."""

import time
from collections import deque
from datetime import datetime
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static
from textual import work

from bin.pipeline_watch import (
  _kitty_tab_exists,
  _prune_finished_tabs,
  build_sync_prompt,
  build_trigger_prompt,
  launch_in_kitty,
  scan_markers,
  scan_sync_markers,
)
from lib.config import STAGES_DIR, WORKSPACES_DIR

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
    self._watching = False
    self._events = deque(maxlen=MAX_EVENTS)

  def compose(self) -> ComposeResult:
    yield Static("Pipeline (paused)", classes="panel-title",
                 id="pipeline-title")
    yield DataTable(id="pipeline-table")

  def on_mount(self) -> None:
    table = self.query_one("#pipeline-table", DataTable)
    table.cursor_type = "row"
    table.add_columns("Time", "Stage", "Repos", "Event")

  @property
  def watching(self):
    """Whether the watcher is active."""
    return self._watching

  def toggle_watching(self) -> None:
    """Toggle the watcher on/off."""
    self._watching = not self._watching
    title = self.query_one("#pipeline-title", Static)
    if self._watching:
      title.update("Pipeline (watching)")
    else:
      title.update("Pipeline (paused)")

  @work(thread=True)
  def refresh_data(self) -> None:
    """Poll for markers and trigger agents."""
    if not self._watching:
      return
    try:
      pruned = _prune_finished_tabs()
      for title in pruned:
        ws, role = title.split("/", 1)
        self._events.appendleft({
          "time": datetime.now().strftime("%H:%M:%S"),
          "stage": title,
          "repos": "",
          "event": "pruned",
        })
      if pruned:
        self.app.call_from_thread(self._update_table)
    except Exception:
      pass
    try:
      markers = scan_markers()
    except Exception:
      markers = {}
    for (ws, role), repo_markers in markers.items():
      repos = [r for r, _ in repo_markers]
      stage_dir = STAGES_DIR / ws / role
      prompt = build_trigger_prompt(ws, role, repo_markers)
      # Delete markers before launching.
      for repo, _ in repo_markers:
        marker = STAGES_DIR / ws / role / repo / ".pipeline-push"
        marker.unlink(missing_ok=True)
      try:
        launch_in_kitty(ws, role, stage_dir, prompt)
        event_type = "triggered"
      except Exception:
        event_type = "error"
      self._events.appendleft({
        "time": datetime.now().strftime("%H:%M:%S"),
        "stage": f"{ws}/{role}",
        "repos": ", ".join(repos),
        "event": event_type,
      })
    # Scan for upstream sync markers.
    try:
      sync_markers = scan_sync_markers()
    except Exception:
      sync_markers = {}
    for ws, repo_markers in sync_markers.items():
      repos = [r for r, _ in repo_markers]
      title = f"{ws}/sync"
      if _kitty_tab_exists(title):
        continue
      prompt = build_sync_prompt(ws, repo_markers)
      for repo, _ in repo_markers:
        m = WORKSPACES_DIR / ws / repo / ".upstream-sync"
        m.unlink(missing_ok=True)
      ws_dir = WORKSPACES_DIR / ws
      try:
        launch_in_kitty(ws, "sync", ws_dir, prompt)
        event_type = "triggered"
      except Exception:
        event_type = "error"
      self._events.appendleft({
        "time": datetime.now().strftime("%H:%M:%S"),
        "stage": title,
        "repos": ", ".join(repos),
        "event": event_type,
      })
    if markers or sync_markers:
      self.app.call_from_thread(self._update_table)

  def _update_table(self) -> None:
    """Render event log to the table."""
    table = self.query_one("#pipeline-table", DataTable)
    table.clear()
    for ev in self._events:
      table.add_row(
        ev["time"], ev["stage"], ev["repos"], ev["event"],
      )
