"""Main TUI application — tabbed layout."""

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, TabbedContent, TabPane

from tui.tabs.agents_tab import AgentsTab
from tui.tabs.dashboard_tab import DashboardTab
from tui.tabs.settings_tab import SettingsTab
from tui.tabs.trigger_tab import TriggerTab


class TaktApp(App):
  """takt — tabbed TUI."""

  CSS_PATH = "dashboard.tcss"
  TITLE = "takt"

  BINDINGS = [
    Binding("q", "quit", "Quit"),
    Binding("r", "refresh", "Refresh"),
    Binding("n", "new_workspace", "New WS"),
    Binding("c", "claim_target", "Claim"),
    Binding("x", "release_target", "Release"),
    Binding("ctrl+w", "close_tab", "Close Tab"),
  ]

  def compose(self) -> ComposeResult:
    yield Header(show_clock=True)
    with TabbedContent(id="tabs"):
      with TabPane("Dashboard", id="tab-dashboard"):
        yield DashboardTab(id="dashboard-tab")
      with TabPane("Agents", id="tab-agents"):
        yield AgentsTab(id="agents-tab")
      with TabPane("Trigger", id="tab-trigger"):
        yield TriggerTab(id="trigger-tab")
      with TabPane("Settings", id="tab-settings"):
        yield SettingsTab(id="settings-tab")
    yield Footer()

  def on_mount(self) -> None:
    """Start dashboard polling."""
    dashboard = self.query_one(
      "#dashboard-tab", DashboardTab
    )
    dashboard.refresh_all()
    dashboard.start_polling()
    # Stream new output to agents tab viewer.
    self.set_interval(
      1, self._poll_agents_viewer
    )

  def _poll_agents_viewer(self) -> None:
    """Push new output lines to agents tab viewer."""
    try:
      agents_tab = self.query_one(
        "#agents-tab", AgentsTab
      )
      agents_tab.refresh_viewer()
    except Exception:
      pass

  # -- Agent tab management --

  def launch_agent(self, agent_id, prompt, cwd,
                   model=None, workspace="", role=""):
    """Create an AgentRunner, register it, and open a tab.

    Args:
      agent_id: Unique ID like "ws/role".
      prompt: Prompt string for the agent.
      cwd: Working directory for the agent.
      model: Model name (default from settings).
      workspace: Workspace name.
      role: Pipeline role.
    """
    from lib import agent_registry
    from lib.agent_runner import AgentInfo, AgentRunner
    if agent_registry.is_running(agent_id):
      self.notify(
        f"Agent {agent_id} already running.",
        severity="warning",
      )
      return
    if model is None:
      from tui.tabs.settings_tab import load_settings
      model = load_settings().get("model", "sonnet")
    info = AgentInfo(
      agent_id=agent_id,
      workspace=workspace,
      role=role,
      cwd=str(cwd),
      model=model,
    )
    runner = AgentRunner(info)
    runner._prompt = prompt
    agent_registry.register(runner)
    self.add_agent_tab(agent_id, agent_id, runner)

  def add_agent_tab(self, agent_id, title, runner):
    """Create and switch to a new agent tab.

    Args:
      agent_id: Unique ID like "ws/role".
      title: Display title for the tab.
      runner: AgentRunner instance to stream from.
    """
    from tui.tabs.agent_tab import AgentTab
    tab_id = f"tab-agent-{agent_id.replace('/', '-')}"
    tabs = self.query_one("#tabs", TabbedContent)
    agent_tab = AgentTab(runner=runner, id=f"at-{tab_id}")
    pane = TabPane(title, agent_tab, id=tab_id)
    tabs.add_pane(pane)
    tabs.active = tab_id

  def remove_agent_tab(self, agent_id):
    """Remove an agent tab by agent_id.

    Args:
      agent_id: The agent ID used when the tab was added.
    """
    tab_id = f"tab-agent-{agent_id.replace('/', '-')}"
    tabs = self.query_one("#tabs", TabbedContent)
    try:
      tabs.remove_pane(tab_id)
    except Exception:
      pass

  def action_close_tab(self) -> None:
    """Close the active tab if it's an agent tab."""
    tabs = self.query_one("#tabs", TabbedContent)
    active = tabs.active
    if active and active.startswith("tab-agent-"):
      tabs.remove_pane(active)

  # -- Delegated actions --

  def action_refresh(self) -> None:
    """Refresh dashboard panels."""
    try:
      dashboard = self.query_one(
        "#dashboard-tab", DashboardTab
      )
      dashboard.refresh_all()
    except Exception:
      pass
    try:
      trigger = self.query_one(
        "#trigger-tab", TriggerTab
      )
      trigger.refresh_data()
    except Exception:
      pass
    try:
      agents_tab = self.query_one(
        "#agents-tab", AgentsTab
      )
      agents_tab.refresh_viewer()
    except Exception:
      pass

  def action_new_workspace(self) -> None:
    """Open create workspace modal."""
    from tui.screens import CreateWorkspaceScreen
    self.push_screen(CreateWorkspaceScreen())

  def action_claim_target(self) -> None:
    """Open claim target modal."""
    try:
      from tui.widgets.targets import TargetsPanel
      targets_panel = self.query_one(
        "#targets-panel", TargetsPanel
      )
      row_key = targets_panel.get_selected_target()
      if row_key:
        from tui.screens import ClaimTargetScreen
        self.push_screen(ClaimTargetScreen(row_key))
    except Exception:
      pass

  def action_release_target(self) -> None:
    """Release the selected target."""
    try:
      from tui.widgets.targets import TargetsPanel
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
    except Exception:
      pass

  def _on_release_confirmed(self, result) -> None:
    """Handle release confirmation."""
    if result:
      from lib.target_ops import release_lock
      release_lock(result)
      self.action_refresh()

  async def on_unmount(self) -> None:
    """Cancel all running agents on exit."""
    from lib import agent_registry
    for runner in agent_registry.list_active():
      runner.cancel()
