"""Main TUI dashboard application."""

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Footer, Header

from tui.widgets.agents import AgentsPanel
from tui.widgets.detail import DetailPane
from tui.widgets.targets import TargetsPanel
from tui.widgets.usage import UsagePanel
from tui.widgets.workspaces import WorkspacesPanel


class DashboardApp(App):
  """Agent Orchestration Dashboard."""

  CSS_PATH = "dashboard.tcss"
  TITLE = "Agent Orchestration Dashboard"

  BINDINGS = [
    Binding("q", "quit", "Quit"),
    Binding("r", "refresh", "Refresh"),
    Binding("n", "new_workspace", "New WS"),
    Binding("c", "claim_target", "Claim"),
    Binding("x", "release_target", "Release"),
  ]

  def compose(self) -> ComposeResult:
    yield Header(show_clock=True)
    yield WorkspacesPanel(id="workspaces-panel")
    yield AgentsPanel(id="agents-panel")
    yield DetailPane(id="detail-pane")
    yield TargetsPanel(id="targets-panel")
    yield UsagePanel(id="usage-panel")
    yield Footer()

  def on_mount(self) -> None:
    """Initial data load and start polling."""
    self._refresh_all()
    self.set_interval(10, self._poll_workspaces)
    self.set_interval(5, self._poll_agents)
    self.set_interval(10, self._poll_targets)
    self.set_interval(60, self._poll_usage)

  def _refresh_all(self) -> None:
    """Refresh all panels."""
    self._poll_workspaces()
    self._poll_agents()
    self._poll_targets()
    self._poll_usage()

  def _poll_workspaces(self) -> None:
    panel = self.query_one("#workspaces-panel", WorkspacesPanel)
    panel.refresh_data()

  def _poll_agents(self) -> None:
    panel = self.query_one("#agents-panel", AgentsPanel)
    panel.refresh_data()

  def _poll_targets(self) -> None:
    panel = self.query_one("#targets-panel", TargetsPanel)
    panel.refresh_data()

  def _poll_usage(self) -> None:
    panel = self.query_one("#usage-panel", UsagePanel)
    panel.refresh_data()

  def action_refresh(self) -> None:
    """Manual refresh all panels."""
    self._refresh_all()

  def action_new_workspace(self) -> None:
    """Open create workspace modal."""
    from tui.screens import CreateWorkspaceScreen
    self.push_screen(CreateWorkspaceScreen())

  def action_claim_target(self) -> None:
    """Open claim target modal."""
    targets_panel = self.query_one(
      "#targets-panel", TargetsPanel
    )
    row_key = targets_panel.get_selected_target()
    if row_key:
      from tui.screens import ClaimTargetScreen
      self.push_screen(ClaimTargetScreen(row_key))

  def action_release_target(self) -> None:
    """Release the selected target."""
    targets_panel = self.query_one(
      "#targets-panel", TargetsPanel
    )
    row_key = targets_panel.get_selected_target()
    if row_key:
      from tui.screens import ConfirmScreen
      self.push_screen(
        ConfirmScreen(
          f"Release target '{row_key}'?",
          row_key,
        ),
        callback=self._on_release_confirmed,
      )

  def _on_release_confirmed(self, result) -> None:
    """Handle release confirmation."""
    if result:
      from lib.target_ops import release_lock
      release_lock(result)
      self._poll_targets()

  def update_detail(self, content: str) -> None:
    """Update the detail pane with new content."""
    pane = self.query_one("#detail-pane", DetailPane)
    pane.update_content(content)
