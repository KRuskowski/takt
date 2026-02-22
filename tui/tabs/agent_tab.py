"""Agent tab — streaming output from an inline Claude agent."""

import logging

from textual.app import ComposeResult
from textual.widgets import Static
from textual import work

from claude_code_sdk import (
  AssistantMessage,
  CLIConnectionError,
  CLIJSONDecodeError,
  CLINotFoundError,
  ProcessError,
  ResultMessage,
  SystemMessage,
)

from lib.agent_runner import AgentState
from tui.widgets.agent_output import (
  render_block,
  render_message,
)
from tui.widgets.selectable_log import SelectableLog

log = logging.getLogger("takt.agent_tab")


class AgentTab(Static):
  """Displays streaming agent output with a status bar."""

  DEFAULT_CSS = """
  AgentTab {
    height: 1fr;
  }
  """

  def __init__(self, runner, **kwargs):
    """Initialize the agent tab.

    Args:
      runner: AgentRunner to stream from.
    """
    super().__init__(**kwargs)
    self._runner = runner

  def compose(self) -> ComposeResult:
    yield Static(
      self._status_text(),
      id="agent-status",
      classes="agent-status-bar",
    )
    yield SelectableLog(
      id="agent-log",
      highlight=True,
      markup=True,
      wrap=True,
      classes="agent-output",
    )

  def on_mount(self) -> None:
    """Start the agent run."""
    self._start_agent()

  @work(thread=False)
  async def _start_agent(self) -> None:
    """Run the agent asynchronously.

    Uses thread=False (async worker) since the SDK's
    query() is an async iterator. All callbacks run on
    the event loop, so widget methods are called directly.
    """
    prompt = getattr(self._runner, '_prompt', None)
    if prompt is None:
      return
    try:
      await self._runner.run(
        prompt, self._handle_sdk_message
      )
    except CLINotFoundError:
      self._append_error(
        "Claude Code CLI not found. Install with: "
        "npm install -g @anthropic-ai/claude-code"
      )
    except ProcessError as e:
      stderr = (e.stderr or "")[:200]
      code = (
        f" (exit code {e.exit_code})"
        if e.exit_code is not None else ""
      )
      self._append_error(
        f"Process failed{code}: {stderr}"
      )
    except CLIConnectionError as e:
      self._append_error(
        f"Connection failed: {e}"
      )
    except CLIJSONDecodeError as e:
      self._append_error(
        f"Bad JSON from CLI: {e.line[:200]}"
      )
    except Exception as e:
      self._append_error(
        f"{type(e).__name__}: {e}"
      )
    finally:
      self._update_status()
      self._on_agent_finished()

  def _handle_sdk_message(self, msg) -> None:
    """Handle an SDK message from the agent.

    Called from the async iterator on the event loop,
    so widget updates are safe to do directly. Also
    buffers output on the runner for the agents tab.

    Args:
      msg: SDK message object.
    """
    if isinstance(msg, AssistantMessage):
      for block in msg.content:
        rendered = render_block(block)
        if rendered:
          self._buffer_and_append(rendered)
    elif isinstance(msg, ResultMessage):
      rendered = render_message(msg)
      if rendered:
        self._buffer_and_append(rendered)
    elif isinstance(msg, SystemMessage):
      from rich.text import Text
      text = msg.data.get("message", msg.subtype)
      t = Text()
      t.append("\u26a0 ", style="bold #ffa726")
      t.append(str(text), style="#ffa726")
      self._buffer_and_append(t)
    self._update_status()

  def _buffer_and_append(self, text) -> None:
    """Buffer on runner and append to the log."""
    if not hasattr(self._runner, '_output_buffer'):
      self._runner._output_buffer = []
    self._runner._output_buffer.append(text)
    log_widget = self.query_one("#agent-log", SelectableLog)
    log_widget.write(text)

  def _append_error(self, error_text) -> None:
    """Append an error message to the log."""
    from rich.text import Text
    t = Text()
    t.append("[error] ", style="bold #ef5350")
    t.append(error_text, style="#ef5350")
    self._buffer_and_append(t)

  def _update_status(self) -> None:
    """Update the status bar."""
    status = self.query_one(
      "#agent-status", Static
    )
    status.update(self._status_text())

  def _status_text(self) -> str:
    """Build status bar text from agent info."""
    info = self._runner.info
    state = info.state.value
    parts = [
      f"[{state}]",
      info.agent_id,
      f"model:{info.model}",
    ]
    if info.total_cost_usd > 0:
      parts.append(f"${info.total_cost_usd:.4f}")
    if info.num_turns > 0:
      parts.append(f"{info.num_turns} turns")
    return "  ".join(parts)

  def _on_agent_finished(self) -> None:
    """Update tab title with status icon."""
    info = self._runner.info
    tab_id = (
      f"tab-agent-"
      f"{info.agent_id.replace('/', '-')}"
    )
    state = info.state
    if state == AgentState.COMPLETED:
      icon = " ✓"
    elif state == AgentState.FAILED:
      icon = " ✗"
    elif state == AgentState.CANCELLED:
      icon = " ⏹"
    else:
      icon = ""
    try:
      from textual.widgets import TabbedContent
      tabs = self.app.query_one(
        "#tabs", TabbedContent
      )
      tab = tabs.get_tab(tab_id)
      if tab:
        tab.label = (
          f"{info.agent_id}{icon}"
        )
    except Exception:
      log.debug(
        "Failed to update tab title for %s",
        info.agent_id,
        exc_info=True,
      )
