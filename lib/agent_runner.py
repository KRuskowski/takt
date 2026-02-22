"""SDK wrapper for running Claude agents inline.

Uses claude-code-sdk to run agents as subprocesses with
async streaming. Each agent is one query() call.
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum

from claude_code_sdk import (
  ClaudeCodeOptions,
  ProcessError,
  ResultMessage,
  SystemMessage,
  query,
)
from claude_code_sdk._internal import client as _sdk_client
from claude_code_sdk._internal import message_parser
from claude_code_sdk._internal.message_parser import (
  MessageParseError,
)
from claude_code_sdk.types import StreamEvent

log = logging.getLogger("takt.agent")

# Monkey-patch the SDK message parser to handle unknown
# event types (e.g. rate_limit_event) instead of raising.
_original_parse = message_parser.parse_message

_TRANSIENT_PATTERNS = re.compile(
  r"rate_limit|overloaded|529|too many requests|capacity",
  re.IGNORECASE,
)
_MAX_RETRIES = 2
_BACKOFF_SECS = (30, 60)


def _patched_parse(data):
  """Parse message, returning StreamEvent for unknown types.

  Only catches MessageParseError so real bugs propagate.
  """
  try:
    return _original_parse(data)
  except MessageParseError:
    log.debug("Skipped unparseable event: %s", data)
    return StreamEvent(
      uuid=data.get("uuid", ""),
      session_id=data.get("session_id", ""),
      event=data,
      parent_tool_use_id=data.get(
        "parent_tool_use_id"
      ),
    )

message_parser.parse_message = _patched_parse
_sdk_client.parse_message = _patched_parse


def _is_transient(err):
  """Check if a ProcessError looks transient.

  Args:
    err: ProcessError instance.

  Returns:
    True if stderr matches a transient pattern.
  """
  return bool(
    err.stderr and _TRANSIENT_PATTERNS.search(err.stderr)
  )


class AgentState(Enum):
  """Agent lifecycle states."""
  PENDING = "pending"
  RUNNING = "running"
  COMPLETED = "completed"
  FAILED = "failed"
  CANCELLED = "cancelled"


@dataclass
class AgentInfo:
  """Metadata for a running or completed agent."""

  agent_id: str
  workspace: str
  role: str
  cwd: str
  model: str = "sonnet"
  state: AgentState = AgentState.PENDING
  session_id: str | None = None
  total_cost_usd: float = 0.0
  num_turns: int = 0
  started_at: float = field(
    default_factory=time.time
  )
  finished_at: float | None = None
  error: str | None = None


class AgentRunner:
  """Runs a Claude agent via the SDK and streams messages.

  Attributes:
    info: AgentInfo with current state and metadata.
  """

  def __init__(self, info, permission_mode="bypassPermissions",
               add_dirs=None):
    """Initialize the runner.

    Args:
      info: AgentInfo describing the agent.
      permission_mode: SDK permission mode string.
      add_dirs: Extra directories to allow tool access to.
    """
    self.info = info
    self._permission_mode = permission_mode
    self._add_dirs = add_dirs or []
    self._cancelled = False
    self._task = None

  async def run(self, prompt, on_message):
    """Run the agent and stream messages.

    Retries up to _MAX_RETRIES times for transient
    ProcessError failures (rate limits, overload).

    Args:
      prompt: The prompt string to send.
      on_message: Callback(msg) for each SDK message.
        Called from the async context.
    """
    self.info.state = AgentState.RUNNING
    self.info.started_at = time.time()
    log.info(
      "Agent %s starting (model=%s, cwd=%s)",
      self.info.agent_id,
      self.info.model,
      self.info.cwd,
    )
    opts = ClaudeCodeOptions(
      cwd=self.info.cwd,
      permission_mode=self._permission_mode,
      model=self.info.model,
      add_dirs=self._add_dirs,
      settings='{"sandbox":{"enabled":false}}',
    )
    attempt = 0
    while True:
      try:
        async for msg in query(
          prompt=prompt, options=opts
        ):
          if self._cancelled:
            break
          if on_message:
            on_message(msg)
          if isinstance(msg, ResultMessage):
            self.info.session_id = msg.session_id
            if msg.total_cost_usd is not None:
              self.info.total_cost_usd = (
                msg.total_cost_usd
              )
            self.info.num_turns = msg.num_turns
        if self._cancelled:
          self.info.state = AgentState.CANCELLED
        else:
          self.info.state = AgentState.COMPLETED
          log.info(
            "Agent %s completed "
            "(cost=$%.4f, turns=%d)",
            self.info.agent_id,
            self.info.total_cost_usd,
            self.info.num_turns,
          )
        break
      except ProcessError as e:
        if _is_transient(e) and attempt < _MAX_RETRIES:
          attempt += 1
          delay = _BACKOFF_SECS[
            min(attempt - 1, len(_BACKOFF_SECS) - 1)
          ]
          log.warning(
            "Agent %s transient error "
            "(attempt %d/%d), retrying in %ds: %s",
            self.info.agent_id,
            attempt,
            _MAX_RETRIES + 1,
            delay,
            e,
          )
          if on_message:
            on_message(SystemMessage(
              subtype="retry",
              data={
                "message":
                  f"Transient error, retrying in "
                  f"{delay}s (attempt "
                  f"{attempt}/{_MAX_RETRIES + 1})..."
              },
            ))
          await asyncio.sleep(delay)
          if self._cancelled:
            self.info.state = AgentState.CANCELLED
            break
          continue
        self.info.error = str(e)
        self.info.state = AgentState.FAILED
        log.error(
          "Agent %s failed: %s",
          self.info.agent_id,
          e,
          exc_info=True,
        )
        raise
      except Exception as e:
        self.info.error = str(e)
        self.info.state = AgentState.FAILED
        log.error(
          "Agent %s failed: %s",
          self.info.agent_id,
          e,
          exc_info=True,
        )
        raise
      finally:
        self.info.finished_at = time.time()

  def cancel(self):
    """Signal the agent to stop."""
    self._cancelled = True
