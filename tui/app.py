"""Main TUI application — tabbed layout.

Connects to takt-service via ZMQ for agent execution
and pipeline monitoring. Falls back gracefully if the
service is not running.
"""

import asyncio
import logging
import subprocess
from datetime import datetime

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult, RenderResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widget import Widget
from textual.containers import Horizontal
from textual.widgets import (
  Footer, Header, Input, Label, TabbedContent, TabPane,
)
from textual.widgets._header import (
  HeaderIcon, HeaderTitle,
)

from tui.tabs.agents_tab import AgentsTab
from tui.tabs.dashboard_tab import DashboardTab
from tui.tabs.meta_tab import MetaTab
from tui.tabs.pipeline_tab import PipelineTab
from tui.tabs.settings_tab import SettingsTab
from tui.tabs.targets_tab import TargetsTab
from tui.tabs.trigger_tab import TriggerTab

log = logging.getLogger("takt.app")


class ClockStatus(Widget):
  """Clock with service status dot, replaces HeaderClock."""

  DEFAULT_CSS = """
  ClockStatus {
    dock: right;
    width: 12;
    padding: 0 1;
    background: $foreground-darken-1 5%;
    color: $foreground;
    text-opacity: 85%;
    content-align: center middle;
  }
  """

  connected = reactive(False)

  def on_mount(self) -> None:
    self.set_interval(1, self.refresh)

  def render(self) -> RenderResult:
    """Render status dot + clock."""
    now = datetime.now().strftime("%X")
    dot = "\u25cf" if self.connected else "\u25cb"
    color = "#4caf50" if self.connected else "#9e9e9e"
    result = Text()
    result.append(dot, style=color)
    result.append(f" {now}")
    return result


class TaktHeader(Header):
  """Header with service status next to the clock."""

  def compose(self) -> ComposeResult:
    yield HeaderIcon().data_bind(Header.icon)
    yield HeaderTitle()
    yield ClockStatus(id="clock-status")


class TaktApp(App):
  """takt — tabbed TUI."""

  CSS_PATH = "dashboard.tcss"
  TITLE = "takt"

  BINDINGS = [
    Binding("q", "quit", "Quit"),
    Binding("ctrl+r", "refresh", "Refresh"),
    Binding("ctrl+w", "close_tab", "Close Tab"),
    Binding(
      "colon", "show_command_bar", ":", show=False
    ),
  ]

  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    self._service_client = None
    self._prev_agent_states = {}

  def on_text_selected(self, event: events.TextSelected):
    """Auto-copy to clipboard when text is selected."""
    try:
      text = self.screen.get_selected_text()
      if text:
        self.copy_to_clipboard(text)
    except Exception:
      log.debug(
        "auto-copy failed", exc_info=True
      )

  def copy_to_clipboard(self, text):
    """Copy text to system clipboard.

    Tries system clipboard tools (wl-copy, xclip, xsel)
    first, falls back to OSC 52 for terminal support.
    Uses Popen to avoid blocking — xclip forks and stays
    alive to serve clipboard requests.

    Args:
      text: Text to copy.
    """
    self._clipboard = text
    for cmd in [
      ["wl-copy"],
      ["xclip", "-selection", "clipboard"],
      ["xsel", "--clipboard", "--input"],
    ]:
      try:
        proc = subprocess.Popen(
          cmd,
          stdin=subprocess.PIPE,
          stdout=subprocess.DEVNULL,
          stderr=subprocess.DEVNULL,
        )
        proc.stdin.write(text.encode("utf-8"))
        proc.stdin.close()
        self.notify("Copied")
        return
      except (FileNotFoundError, OSError):
        continue
    # Fall back to OSC 52.
    super().copy_to_clipboard(text)
    self.notify("Copied")

  def compose(self) -> ComposeResult:
    yield TaktHeader()
    with TabbedContent(id="tabs"):
      with TabPane("Dashboard", id="tab-dashboard"):
        yield DashboardTab(id="dashboard-tab")
      with TabPane("Pipeline", id="tab-pipeline"):
        yield PipelineTab(id="pipeline-tab")
      with TabPane("Meta", id="tab-meta"):
        yield MetaTab(id="meta-tab")
      with TabPane("Agents", id="tab-agents"):
        yield AgentsTab(id="agents-tab")
      with TabPane("Targets", id="tab-targets"):
        yield TargetsTab(id="targets-tab")
      with TabPane("Trigger", id="tab-trigger"):
        yield TriggerTab(id="trigger-tab")
      with TabPane("Settings", id="tab-settings"):
        yield SettingsTab(id="settings-tab")
    with Horizontal(id="command-bar"):
      yield Label(":")
      yield Input(
        id="command-input",
        placeholder="command (tab to help)",
      )
    yield Footer()

  # Tab name aliases for :command dispatch.
  _TAB_ALIASES = {
    "dashboard": "tab-dashboard",
    "dash": "tab-dashboard",
    "pipeline": "tab-pipeline",
    "pl": "tab-pipeline",
    "meta": "tab-meta",
    "agents": "tab-agents",
    "ag": "tab-agents",
    "targets": "tab-targets",
    "tgt": "tab-targets",
    "trigger": "tab-trigger",
    "trig": "tab-trigger",
    "settings": "tab-settings",
    "set": "tab-settings",
  }

  def on_mount(self) -> None:
    """Start dashboard polling, connect to service."""
    from lib.log_setup import setup_logging
    setup_logging()
    from lib import db
    db.migrate()
    self.query_one("#command-bar").display = False
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

  def _set_service_status(self, connected):
    """Update the header service indicator.

    Args:
      connected: True if connected to takt-service.
    """
    try:
      clock = self.query_one(
        "#clock-status", ClockStatus
      )
      clock.connected = connected
    except NoMatches:
      pass

  async def _do_connect_service(self) -> None:
    """Connect to takt-service via ZMQ."""
    from lib.service_client import ServiceClient
    client = ServiceClient()
    try:
      await client.connect()
      ok = await client.is_service_running()
      if ok:
        self._service_client = client
        self._set_service_status(True)
        client.subscribe("agent.update")
        client.subscribe("pipeline.event")
        client.subscribe("meta.update")
        client.on(
          "agent.update",
          self._on_agent_update,
        )
        client.on(
          "pipeline.event",
          self._on_pipeline_event,
        )
        client.on(
          "meta.update",
          self._on_meta_update,
        )
        log.info("Connected to takt-service")
      else:
        await client.disconnect()
        self._service_client = None
        self._set_service_status(False)
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
      self._set_service_status(False)

  def _connect_service(self) -> None:
    """Schedule service connection."""
    asyncio.ensure_future(
      self._do_connect_service()
    )

  def _on_agent_update(self, topic, data) -> None:
    """Handle agent.update events from service.

    Tracks state transitions and sends notifications on
    completed/failed transitions.

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
    # Track state transitions for notifications.
    aid = data.get("agent_id", "")
    new_state = data.get("state", "")
    old_state = self._prev_agent_states.get(aid)
    self._prev_agent_states[aid] = new_state
    if old_state and old_state != new_state:
      self._notify_agent_transition(
        aid, old_state, new_state
      )

  def _notify_agent_transition(
    self, agent_id, old_state, new_state,
  ):
    """Send TUI toast and desktop notification on finish.

    Args:
      agent_id: Agent ID string.
      old_state: Previous state string.
      new_state: New state string.
    """
    if new_state == "completed":
      self.notify(f"{agent_id} completed")
      from lib.notify import notify as desktop_notify
      desktop_notify("takt", f"{agent_id} completed")
    elif new_state == "failed":
      self.notify(
        f"{agent_id} failed", severity="error"
      )
      from lib.notify import notify as desktop_notify
      desktop_notify(
        "takt", f"{agent_id} failed",
        urgency="critical",
      )

  def _on_pipeline_event(self, topic, data) -> None:
    """Handle pipeline.event events from service.

    Notifies on run completion/failure events.

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
    # Notify on step/run finish events.
    new_status = data.get("new_status", "")
    entity = data.get("entity", "")
    label = data.get("reason", entity)
    if new_status == "passed" and entity == "run":
      self.notify(f"Run {label} passed")
      from lib.notify import notify as desktop_notify
      desktop_notify("takt", f"Run {label} passed")
    elif new_status == "failed" and entity == "run":
      self.notify(
        f"Run {label} failed", severity="error"
      )
      from lib.notify import notify as desktop_notify
      desktop_notify(
        "takt", f"Run {label} failed",
        urgency="critical",
      )

  def _on_meta_update(self, topic, data) -> None:
    """Handle meta.update events from service.

    Args:
      topic: Topic string.
      data: Dict with run_id and status.
    """
    try:
      meta_tab = self.query_one("#meta-tab", MetaTab)
      meta_tab.on_meta_update(data)
    except NoMatches:
      pass
    except Exception:
      log.debug(
        "meta update handler failed", exc_info=True
      )

  # -- Command bar --

  def action_show_command_bar(self) -> None:
    """Show the vim-style command bar."""
    bar = self.query_one("#command-bar")
    bar.display = True
    cmd_input = self.query_one("#command-input", Input)
    cmd_input.value = ""
    cmd_input.focus()

  def _hide_command_bar(self) -> None:
    """Hide the command bar and restore focus."""
    bar = self.query_one("#command-bar")
    bar.display = False
    try:
      tabs = self.query_one("#tabs", TabbedContent)
      tabs.focus()
    except NoMatches:
      pass

  def on_input_submitted(
    self, event: Input.Submitted
  ) -> None:
    """Dispatch :commands on Enter."""
    if event.input.id != "command-input":
      return
    cmd = event.input.value.strip().lower()
    self._hide_command_bar()
    if not cmd:
      return
    self._dispatch_command(cmd)

  def on_key(self, event: events.Key) -> None:
    """Handle Escape to dismiss command bar."""
    bar = self.query_one("#command-bar")
    if bar.display and event.key == "escape":
      event.stop()
      event.prevent_default()
      self._hide_command_bar()

  def _dispatch_command(self, cmd) -> None:
    """Parse and execute a command string.

    Args:
      cmd: Command string from the command bar.
    """
    # Tab switching.
    tab_id = self._TAB_ALIASES.get(cmd)
    if tab_id:
      try:
        tabs = self.query_one("#tabs", TabbedContent)
        tabs.active = tab_id
      except NoMatches:
        pass
      return
    # Built-in commands.
    if cmd in ("q", "quit"):
      self.exit()
    elif cmd in ("r", "refresh"):
      self.action_refresh()
    elif cmd in ("h", "help", "?"):
      self._show_help()
    elif cmd in ("start",):
      self.action_service_start()
    elif cmd in ("stop",):
      self.action_service_stop()
    elif cmd in ("restart",):
      self.action_service_restart()
    else:
      self.notify(
        f"Unknown command: {cmd}", severity="warning"
      )

  def _show_help(self) -> None:
    """Display available commands as a notification."""
    lines = [
      "Commands:",
      "  dashboard, pipeline, meta, agents,",
      "  targets, trigger, settings — switch tab",
      "  refresh — reload all data",
      "  start/stop/restart — service control",
      "  quit — exit takt",
    ]
    self.notify("\n".join(lines), timeout=8)

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
      self._set_service_status(False)
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
    """Refresh all tab data."""
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
      pipeline = self.query_one(
        "#pipeline-tab", PipelineTab
      )
      pipeline.refresh_data()
    except NoMatches:
      pass
    except Exception:
      log.debug("refresh pipeline failed", exc_info=True)
    try:
      meta = self.query_one("#meta-tab", MetaTab)
      meta.refresh_data()
    except NoMatches:
      pass
    except Exception:
      log.debug("refresh meta failed", exc_info=True)
    try:
      targets = self.query_one(
        "#targets-tab", TargetsTab
      )
      targets.refresh_data()
    except NoMatches:
      pass
    except Exception:
      log.debug("refresh targets failed", exc_info=True)
    try:
      trigger = self.query_one(
        "#trigger-tab", TriggerTab
      )
      trigger.refresh_data()
    except NoMatches:
      pass
    except Exception:
      log.debug("refresh trigger failed", exc_info=True)

  async def on_unmount(self) -> None:
    """Disconnect from service and cancel local agents."""
    if self._service_client:
      await self._service_client.disconnect()
    from lib import agent_registry
    for runner in agent_registry.list_active():
      runner.cancel()
