"""Agents panel widget."""

import os
import time

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static
from textual import work

from tui.widgets.style_utils import age_style, agent_bucket

_HOME = os.path.expanduser("~")
_DEV = os.path.join(_HOME, "dev")
_ROOT = os.path.join(_DEV, "root")
_WORKSPACES = os.path.join(_DEV, "workspaces")


def _status_label(session):
  """Return a human-friendly status label and age in minutes.

  Returns:
    Tuple of (status_bucket, age_minutes, display_label).
    status_bucket is one of 'active', 'recent', 'stale', 'idle'.
  """
  age_min = float("inf")
  if session.last_active:
    from datetime import datetime
    try:
      ts = datetime.fromisoformat(
        session.last_active.replace("Z", "+00:00")
      )
      age_min = (time.time() - ts.timestamp()) / 60
    except (ValueError, TypeError):
      pass
  bucket = agent_bucket(session.is_active, age_min)
  if bucket == "active":
    return "active", 0, "active"
  if bucket == "idle":
    return "idle", age_min, "idle"
  return bucket, age_min, f"{int(age_min)}m ago"


def _short_project(cwd):
  """Derive a short project label from a working directory.

  Returns:
    'ws:<name>/<repo>' for workspace agents,
    '<repo>' for root repo agents,
    '~/<relative>' for other paths.
  """
  if not cwd:
    return "?"
  # Workspace: ~/dev/workspaces/<name>/<repo>/...
  if cwd.startswith(_WORKSPACES + "/"):
    rest = cwd[len(_WORKSPACES) + 1:]
    parts = rest.split("/", 2)
    if len(parts) >= 2:
      return f"ws:{parts[0]}/{parts[1]}"
    return f"ws:{parts[0]}"
  # Root repo: ~/dev/root/<repo>/...
  if cwd.startswith(_ROOT + "/"):
    rest = cwd[len(_ROOT) + 1:]
    return rest.split("/", 1)[0]
  # Anything else: show relative to home.
  if cwd.startswith(_HOME + "/"):
    return "~/" + cwd[len(_HOME) + 1:]
  return cwd


def _format_tokens(n):
  """Format a token count to a human-readable string."""
  if n >= 1_000_000:
    return f"{n / 1_000_000:.1f}M"
  if n >= 1_000:
    return f"{n / 1_000:.0f}K"
  return str(n)


def _format_context(session):
  """Format context window usage as 'used/limit'."""
  if session.context_limit <= 0:
    return "-"
  used = _format_tokens(session.context_tokens)
  limit = _format_tokens(session.context_limit)
  return f"{used}/{limit}"


class AgentsPanel(Vertical):
  """Panel showing active/recent Claude agent sessions."""

  DEFAULT_CSS = """
  AgentsPanel {
    padding: 0 1;
  }
  """

  def compose(self) -> ComposeResult:
    yield Static("Agents", classes="panel-title")
    yield DataTable(
      id="agents-table",
      cursor_foreground_priority="renderable",
    )

  def on_mount(self) -> None:
    table = self.query_one("#agents-table", DataTable)
    table.cursor_type = "row"
    table.add_columns(
      "Slug", "Project", "Branch", "Model", "Status",
      "Context", "Tokens",
    )

  @work(thread=True)
  def refresh_data(self) -> None:
    """Discover sessions in a worker thread."""
    from lib.session_parser import discover_sessions
    sessions = discover_sessions()
    self.app.call_from_thread(self._update_table, sessions)

  def _update_table(self, sessions) -> None:
    """Update the table, filtering out idle sessions."""
    table = self.query_one("#agents-table", DataTable)
    table.clear()

    active_count = 0
    hidden_count = 0
    for s in sessions:
      status, age_min, label = _status_label(s)
      if status == "idle":
        hidden_count += 1
        continue
      if status == "active":
        active_count += 1

      style = age_style(status)
      model = (
        s.model.split("-")[1] if "-" in s.model else s.model
      )
      model = model[:6]
      total_tok = s.total_input_tokens + s.total_output_tokens
      project = _short_project(s.cwd)
      cells = [
        s.slug[:20] if s.slug else s.session_id[:8],
        project[:25],
        s.git_branch[:15] if s.git_branch else "-",
        model,
        label,
        _format_context(s),
        _format_tokens(total_tok),
      ]
      table.add_row(
        *(Text(c, style=style) for c in cells),
        key=s.session_id,
      )

    # Update panel title with counts.
    title = self.query_one(".panel-title", Static)
    parts = [f"Agents ({active_count} active"]
    if hidden_count:
      parts[0] += f", {hidden_count} hidden"
    parts[0] += ")"
    title.update(parts[0])
