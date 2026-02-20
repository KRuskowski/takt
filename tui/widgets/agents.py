"""Agents panel widget."""

import time

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static
from textual import work


def _status_label(session):
  """Return a status label based on activity recency."""
  if session.is_active:
    return "active"
  if session.last_active:
    from datetime import datetime
    try:
      ts = datetime.fromisoformat(
        session.last_active.replace("Z", "+00:00")
      )
      age_min = (
        time.time() - ts.timestamp()
      ) / 60
      if age_min < 10:
        return "recent"
    except (ValueError, TypeError):
      pass
  return "idle"


def _format_tokens(n):
  """Format a token count to a human-readable string."""
  if n >= 1_000_000:
    return f"{n / 1_000_000:.1f}M"
  if n >= 1_000:
    return f"{n / 1_000:.0f}K"
  return str(n)


class AgentsPanel(Vertical):
  """Panel showing active Claude agent sessions."""

  DEFAULT_CSS = """
  AgentsPanel {
    border: solid $accent;
    padding: 0 1;
  }
  """

  def compose(self) -> ComposeResult:
    yield Static("Agents", classes="panel-title")
    yield DataTable(id="agents-table")

  def on_mount(self) -> None:
    table = self.query_one("#agents-table", DataTable)
    table.cursor_type = "row"
    table.add_columns(
      "Slug", "Project", "Branch", "Model", "Status",
      "Tokens",
    )

  @work(thread=True)
  def refresh_data(self) -> None:
    """Discover sessions in a worker thread."""
    from lib.session_parser import discover_sessions
    sessions = discover_sessions()
    self.app.call_from_thread(self._update_table, sessions)

  def _update_table(self, sessions) -> None:
    """Update the table with fresh session data."""
    table = self.query_one("#agents-table", DataTable)
    table.clear()
    for s in sessions:
      # Extract short project name from path.
      project = s.cwd.rstrip("/").rsplit("/", 1)[-1] if s.cwd else "?"
      # Short model name.
      model = s.model.split("-")[1] if "-" in s.model else s.model
      model = model[:6]
      status = _status_label(s)
      total_tok = s.total_input_tokens + s.total_output_tokens
      table.add_row(
        s.slug[:20] if s.slug else s.session_id[:8],
        project[:15],
        s.git_branch[:15] if s.git_branch else "-",
        model,
        status,
        _format_tokens(total_tok),
        key=s.session_id,
      )
    # Store sessions for detail lookup.
    self._sessions = {s.session_id: s for s in sessions}

  def on_data_table_row_selected(
    self, event: DataTable.RowSelected
  ) -> None:
    """Show session detail when a row is selected."""
    sid = str(event.row_key.value)
    session = getattr(self, "_sessions", {}).get(sid)
    if not session:
      return

    lines = [
      f"## Agent: {session.slug or session.session_id}",
      f"",
      f"  Session:  {session.session_id}",
      f"  CWD:      {session.cwd}",
      f"  Branch:   {session.git_branch}",
      f"  Model:    {session.model}",
      f"  Status:   {'ACTIVE' if session.is_active else 'idle'}",
      f"  Started:  {session.started_at}",
      f"  Last:     {session.last_active}",
      f"  Messages: {session.message_count}",
      f"",
      f"  Token Breakdown:",
      f"    Input:        {session.total_input_tokens:>12,}",
      f"    Output:       {session.total_output_tokens:>12,}",
      f"    Cache read:   {session.total_cache_read:>12,}",
      f"    Cache create: {session.total_cache_create:>12,}",
      f"",
      f"  Est. Cost: ${session.estimated_cost_usd:.4f}",
    ]
    self.app.update_detail("\n".join(lines))
