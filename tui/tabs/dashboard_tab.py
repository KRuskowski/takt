"""Dashboard tab — existing monitoring panels in a grid."""

from textual.app import ComposeResult
from textual.widgets import Static

from tui.widgets.agents import AgentsPanel
from tui.widgets.pipeline import PipelinePanel
from tui.widgets.pipeline_grid import PipelineGridPanel
from tui.widgets.prs import PrsPanel
from tui.widgets.targets import TargetsPanel
from tui.widgets.workspaces import WorkspacesPanel


class DashboardTab(Static):
  """Grid layout with all monitoring panels."""

  DEFAULT_CSS = """
  DashboardTab {
    layout: grid;
    grid-size: 2 4;
    grid-columns: 1fr 1fr;
    grid-rows: 1fr 3fr 1fr 1fr;
    grid-gutter: 1;
    height: 1fr;
  }

  DashboardTab #agents-panel {
    column-span: 1;
    row-span: 1;
    background: #101010;
    border: solid #2a2a2a;
  }

  DashboardTab #workspaces-panel {
    column-span: 1;
    row-span: 1;
    background: #101010;
    border: solid #2a2a2a;
    overflow-x: hidden;
  }

  DashboardTab #stages-panel {
    column-span: 2;
    row-span: 1;
    background: #101010;
  }

  DashboardTab #pipeline-panel {
    column-span: 1;
    row-span: 1;
    background: #101010;
    border: solid #2a2a2a;
  }

  DashboardTab #targets-panel {
    column-span: 1;
    row-span: 1;
    background: #101010;
    border: solid #2a2a2a;
  }

  DashboardTab #prs-panel {
    column-span: 2;
    row-span: 1;
    background: #101010;
    border: solid #2a2a2a;
  }
  """

  def compose(self) -> ComposeResult:
    yield AgentsPanel(id="agents-panel")
    yield WorkspacesPanel(id="workspaces-panel")
    yield PipelineGridPanel(id="stages-panel")
    yield PipelinePanel(id="pipeline-panel")
    yield TargetsPanel(id="targets-panel")
    yield PrsPanel(id="prs-panel")

  def refresh_all(self) -> None:
    """Refresh all panels."""
    self._poll_workspaces()
    self._poll_pipeline_grid()
    self._poll_agents()
    self._poll_targets()
    self._poll_pipeline()
    self._poll_prs()

  def start_polling(self) -> None:
    """Start periodic polling for all panels."""
    self.set_interval(10, self._poll_workspaces)
    self.set_interval(10, self._poll_pipeline_grid)
    self.set_interval(5, self._poll_agents)
    self.set_interval(10, self._poll_targets)
    self.set_interval(10, self._poll_pipeline)
    self.set_interval(60, self._poll_prs)

  def _poll_workspaces(self) -> None:
    self.query_one("#workspaces-panel", WorkspacesPanel
                   ).refresh_data()

  def _poll_agents(self) -> None:
    self.query_one("#agents-panel", AgentsPanel
                   ).refresh_data()

  def _poll_pipeline_grid(self) -> None:
    self.query_one("#stages-panel", PipelineGridPanel
                   ).refresh_data()

  def _poll_targets(self) -> None:
    self.query_one("#targets-panel", TargetsPanel
                   ).refresh_data()

  def _poll_pipeline(self) -> None:
    self.query_one("#pipeline-panel", PipelinePanel
                   ).refresh_data()

  def _poll_prs(self) -> None:
    self.query_one("#prs-panel", PrsPanel).refresh_data()
