"""SDK wrapper for running Claude agents inline.

Uses claude-agent-sdk to run agents as subprocesses with
async streaming. Each agent is one query() call.

The SDK does not talk to the API itself -- it locates the
`claude` binary and drives it over stream-json. So the CLI
is the requirement on any machine running agent steps,
whether through this module or, on a worker, through
lib/worker.py's direct subprocess call.
"""

import asyncio
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from enum import Enum

from claude_agent_sdk import (
  ClaudeAgentOptions,
  ProcessError,
  ResultMessage,
  SystemMessage,
  query,
)

log = logging.getLogger("takt.agent")

# Leaving system_prompt unset does NOT mean "the default".
# This SDK sends `--system-prompt ""` for None, which starts
# the agent with no system prompt at all -- no Claude Code
# behaviour, no tool conventions. The preset asks for the
# CLI's own default explicitly, which is what pipeline roles
# are written against. (The predecessor claude-code-sdk
# omitted the flag when it was falsy, so porting without
# this would have silently stripped it from every agent.)
_CLAUDE_CODE_PRESET = {
  "type": "preset",
  "preset": "claude_code",
}

_TRANSIENT_PATTERNS = re.compile(
  r"rate_limit|overloaded|529|too many requests|capacity",
  re.IGNORECASE,
)
_MAX_RETRIES = 2
_BACKOFF_SECS = (30, 60)


def claude_cli_path():
  """Return the `claude` binary agent steps should run.

  claude-agent-sdk ships its own copy of the CLI (~292 MB)
  and prefers it over PATH. For takt that is the wrong
  default: a worker runs agent steps by invoking PATH's
  `claude` directly (lib/worker.py), and declares the
  capability from `shutil.which("claude")`. Letting the
  scheduler quietly use a different, bundled build means the
  same step runs on two different Claude Code versions
  depending on where it lands -- here 2.1.310 bundled versus
  2.1.220 on PATH -- and the operator's own upgrades stop
  affecting pipeline runs.

  So PATH wins, and the bundled copy is the fallback for a
  machine with no CLI installed. TAKT_CLAUDE_CLI overrides
  both.

  Returns:
    Path to the binary, or None to let the SDK choose (which
    picks its bundled copy).
  """
  override = os.environ.get("TAKT_CLAUDE_CLI")
  if override:
    return override
  return shutil.which("claude")


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
    cli_path = claude_cli_path()
    log.info(
      "Agent %s using claude CLI: %s",
      self.info.agent_id, cli_path or "SDK-bundled",
    )
    opts = ClaudeAgentOptions(
      cwd=self.info.cwd,
      permission_mode=self._permission_mode,
      model=self.info.model,
      add_dirs=self._add_dirs,
      settings='{"sandbox":{"enabled":false}}',
      system_prompt=_CLAUDE_CODE_PRESET,
      cli_path=cli_path,
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
