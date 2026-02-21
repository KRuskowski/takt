"""Main TUI dashboard application."""

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header

from tui.widgets.agents import AgentsPanel
from tui.widgets.pipeline import PipelinePanel
from tui.widgets.pipeline_grid import PipelineGridPanel
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
    Binding("w", "toggle_watcher", "Watch"),
  ]

  def compose(self) -> ComposeResult:
    yield Header(show_clock=True)
    yield AgentsPanel(id="agents-panel")
    yield WorkspacesPanel(id="workspaces-panel")
    yield PipelineGridPanel(id="stages-panel")
    yield PipelinePanel(id="pipeline-panel")
    yield TargetsPanel(id="targets-panel")
    yield Footer()

  def on_mount(self) -> None:
    """Initial data load and start polling."""
    self._refresh_all()
    self.set_interval(10, self._poll_workspaces)
    self.set_interval(10, self._poll_pipeline_grid)
    self.set_interval(5, self._poll_agents)
    self.set_interval(10, self._poll_targets)
    self.set_interval(10, self._poll_pipeline)

  def _refresh_all(self) -> None:
    """Refresh all panels."""
    self._poll_workspaces()
    self._poll_pipeline_grid()
    self._poll_agents()
    self._poll_targets()
    self._poll_pipeline()

  def _poll_workspaces(self) -> None:
    panel = self.query_one("#workspaces-panel", WorkspacesPanel)
    panel.refresh_data()

  def _poll_agents(self) -> None:
    panel = self.query_one("#agents-panel", AgentsPanel)
    panel.refresh_data()

  def _poll_pipeline_grid(self) -> None:
    panel = self.query_one(
      "#stages-panel", PipelineGridPanel
    )
    panel.refresh_data()

  def _poll_targets(self) -> None:
    panel = self.query_one("#targets-panel", TargetsPanel)
    panel.refresh_data()

  def _poll_pipeline(self) -> None:
    panel = self.query_one("#pipeline-panel", PipelinePanel)
    panel.refresh_data()

  def action_refresh(self) -> None:
    """Manual refresh all panels."""
    self._refresh_all()

  def action_toggle_watcher(self) -> None:
    """Toggle pipeline watcher on/off."""
    panel = self.query_one("#pipeline-panel", PipelinePanel)
    panel.toggle_watching()

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
