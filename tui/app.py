"""Main TUI application — tabbed layout.

Connects to takt-service via ZMQ for agent execution
and pipeline monitoring. Falls back gracefully if the
service is not running.
"""

import asyncio
import logging
import subprocess

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.widgets import Footer, Header, TabbedContent, TabPane

from tui.tabs.agents_tab import AgentsTab
from tui.tabs.dashboard_tab import DashboardTab
from tui.tabs.settings_tab import SettingsTab
from tui.tabs.trigger_tab import TriggerTab

log = logging.getLogger("takt.app")


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

  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    self._service_client = None

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
    """Start dashboard polling, connect to service."""
    from lib.log_setup import setup_logging
    setup_logging()
    dashboard = self.query_one(
      "#dashboard-tab", DashboardTab
    )
    dashboard.refresh_all()
    dashboard.start_polling()
    self._connect_service()

  @property
  def service(self):
    """Return the service client, or None."""
    return self._service_client

  async def _do_connect_service(self) -> None:
    """Connect to takt-service via ZMQ."""
    from lib.service_client import ServiceClient
    client = ServiceClient()
    try:
      await client.connect()
      ok = await client.is_service_running()
      if ok:
        self._service_client = client
        client.subscribe("agent.update")
        client.subscribe("pipeline.event")
        client.on(
          "agent.update",
          self._on_agent_update,
        )
        client.on(
          "pipeline.event",
          self._on_pipeline_event,
        )
        log.info("Connected to takt-service")
      else:
        await client.disconnect()
        self._service_client = None
        self.notify(
          "takt-service not running. "
          "Start with: systemctl --user start "
          "takt-service",
          severity="warning",
        )
    except Exception as e:
      log.debug(
        "Service connect failed: %s", e,
        exc_info=True,
      )
      self._service_client = None

  def _connect_service(self) -> None:
    """Schedule service connection."""
    asyncio.ensure_future(
      self._do_connect_service()
    )

  def _on_agent_update(self, topic, data) -> None:
    """Handle agent.update events from service.

    Args:
      topic: Topic string.
      data: Dict with agent state info.
    """
    try:
      agents_tab = self.query_one(
        "#agents-tab", AgentsTab
      )
      agents_tab.on_agent_update(data)
    except NoMatches:
      pass
    except Exception:
      log.debug(
        "agent update handler failed", exc_info=True
      )

  def _on_pipeline_event(self, topic, data) -> None:
    """Handle pipeline.event events from service.

    Args:
      topic: Topic string.
      data: Dict with pipeline event info.
    """
    try:
      from tui.widgets.pipeline import PipelinePanel
      pipeline = self.query_one(
        "#pipeline-panel", PipelinePanel
      )
      pipeline.on_service_event(data)
    except NoMatches:
      pass
    except Exception:
      log.debug(
        "pipeline event handler failed", exc_info=True
      )

  # -- Agent management via service --

  def launch_agent(self, agent_id, prompt, cwd,
                   model=None, workspace="", role=""):
    """Launch an agent via the service.

    Falls back to local execution if service is not
    connected.

    Args:
      agent_id: Unique ID like "ws/role".
      prompt: Prompt string for the agent.
      cwd: Working directory for the agent.
      model: Model name (default from settings).
      workspace: Workspace name.
      role: Pipeline role.
    """
    if model is None:
      from tui.tabs.settings_tab import load_settings
      model = load_settings().get("model", "sonnet")
    if self._service_client:
      asyncio.ensure_future(
        self._launch_via_service(
          agent_id, prompt, cwd, model,
          workspace, role,
        )
      )
    else:
      self._launch_local(
        agent_id, prompt, cwd, model,
        workspace, role,
      )

  async def _launch_via_service(self, agent_id, prompt,
                                cwd, model, workspace,
                                role):
    """Send launch_agent command to service.

    Args:
      agent_id: Agent ID.
      prompt: Prompt string.
      cwd: Working directory.
      model: Model name.
      workspace: Workspace name.
      role: Pipeline role.
    """
    try:
      reply = await self._service_client.send_cmd(
        "launch_agent",
        agent_id=agent_id,
        prompt=prompt,
        cwd=str(cwd),
        model=model,
        workspace=workspace,
        role=role,
      )
      if reply.get("status") == "ok":
        self._open_agent_viewer(agent_id)
      else:
        self.notify(
          reply.get("message", "Launch failed"),
          severity="error",
        )
    except Exception as e:
      log.error(
        "launch_agent failed: %s", e, exc_info=True
      )
      self.notify(
        f"Launch failed: {e}", severity="error"
      )

  def _launch_local(self, agent_id, prompt, cwd,
                    model, workspace, role):
    """Local fallback: run agent in-process.

    Args:
      agent_id: Agent ID.
      prompt: Prompt string.
      cwd: Working directory.
      model: Model name.
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

  def _open_agent_viewer(self, agent_id):
    """Open the agents tab and select the agent.

    Args:
      agent_id: Agent ID to view.
    """
    try:
      tabs = self.query_one("#tabs", TabbedContent)
      tabs.active = "tab-agents"
      agents_tab = self.query_one(
        "#agents-tab", AgentsTab
      )
      agents_tab.select_agent(agent_id)
    except NoMatches:
      pass

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
    except NoMatches:
      pass
    except Exception:
      log.debug(
        "remove_agent_tab failed for %s",
        agent_id,
        exc_info=True,
      )

  def action_close_tab(self) -> None:
    """Close the active tab if it's an agent tab."""
    tabs = self.query_one("#tabs", TabbedContent)
    active = tabs.active
    if active and active.startswith("tab-agent-"):
      tabs.remove_pane(active)

  # -- Service lifecycle --

  def action_service_start(self) -> None:
    """Start takt-service via systemd."""
    try:
      subprocess.run(
        ["systemctl", "--user", "start",
         "takt-service"],
        check=True, capture_output=True,
      )
      self.notify("takt-service started")
      self._connect_service()
    except subprocess.CalledProcessError as e:
      self.notify(
        f"Failed to start: {e.stderr.decode().strip()}",
        severity="error",
      )

  def action_service_stop(self) -> None:
    """Stop takt-service via systemd."""
    try:
      subprocess.run(
        ["systemctl", "--user", "stop",
         "takt-service"],
        check=True, capture_output=True,
      )
      self.notify("takt-service stopped")
      if self._service_client:
        asyncio.ensure_future(
          self._service_client.disconnect()
        )
        self._service_client = None
    except subprocess.CalledProcessError as e:
      self.notify(
        f"Failed to stop: {e.stderr.decode().strip()}",
        severity="error",
      )

  def action_service_restart(self) -> None:
    """Restart takt-service via systemd."""
    try:
      subprocess.run(
        ["systemctl", "--user", "restart",
         "takt-service"],
        check=True, capture_output=True,
      )
      self.notify("takt-service restarted")
      self._connect_service()
    except subprocess.CalledProcessError as e:
      self.notify(
        f"Failed to restart: "
        f"{e.stderr.decode().strip()}",
        severity="error",
      )

  # -- Delegated actions --

  def action_refresh(self) -> None:
    """Refresh dashboard panels."""
    try:
      dashboard = self.query_one(
        "#dashboard-tab", DashboardTab
      )
      dashboard.refresh_all()
    except NoMatches:
      pass
    except Exception:
      log.debug("refresh dashboard failed", exc_info=True)
    try:
      trigger = self.query_one(
        "#trigger-tab", TriggerTab
      )
      trigger.refresh_data()
    except NoMatches:
      pass
    except Exception:
      log.debug("refresh trigger failed", exc_info=True)

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
    except NoMatches:
      pass
    except Exception:
      log.debug("claim target failed", exc_info=True)

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
    except NoMatches:
      pass
    except Exception:
      log.debug("release target failed", exc_info=True)

  def _on_release_confirmed(self, result) -> None:
    """Handle release confirmation."""
    if result:
      from lib.target_ops import release_lock
      release_lock(result)
      self.action_refresh()

  async def on_unmount(self) -> None:
    """Disconnect from service and cancel local agents."""
    if self._service_client:
      await self._service_client.disconnect()
    from lib import agent_registry
    for runner in agent_registry.list_active():
      runner.cancel()
