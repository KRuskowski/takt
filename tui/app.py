"""Main TUI dashboard application."""

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header

from tui.widgets.agents import AgentsPanel
from tui.widgets.stages import StagesPanel
from tui.widgets.targets import TargetsPanel
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
    yield AgentsPanel(id="agents-panel")
    yield WorkspacesPanel(id="workspaces-panel")
    yield StagesPanel(id="stages-panel")
    yield TargetsPanel(id="targets-panel")
    yield Footer()

  def on_mount(self) -> None:
    """Initial data load and start polling."""
    self._refresh_all()
    self.set_interval(10, self._poll_workspaces)
    self.set_interval(10, self._poll_stages)
    self.set_interval(5, self._poll_agents)
    self.set_interval(10, self._poll_targets)

  def _refresh_all(self) -> None:
    """Refresh all panels."""
    self._poll_workspaces()
    self._poll_stages()
    self._poll_agents()
    self._poll_targets()

  def _poll_workspaces(self) -> None:
    panel = self.query_one("#workspaces-panel", WorkspacesPanel)
    panel.refresh_data()

  def _poll_agents(self) -> None:
    panel = self.query_one("#agents-panel", AgentsPanel)
    panel.refresh_data()

  def _poll_stages(self) -> None:
    panel = self.query_one("#stages-panel", StagesPanel)
    panel.refresh_data()

  def _poll_targets(self) -> None:
    panel = self.query_one("#targets-panel", TargetsPanel)
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
