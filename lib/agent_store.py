"""Persistent storage for agent info and output.

Stores agent metadata as JSON and output lines as JSONL
under .state/agents/<safe_id>/.

Agent IDs containing '/' are converted to '-' for
directory names.
"""

import json
import logging

from lib.agent_runner import AgentInfo, AgentState
from lib.config import STATE_DIR

log = logging.getLogger("takt.store")

AGENTS_DIR = STATE_DIR / "agents"


def _safe_id(agent_id):
  """Convert agent_id to a filesystem-safe directory name.

  Args:
    agent_id: Agent ID string (e.g. "ws/role").

  Returns:
    Safe string with '/' replaced by '-'.
  """
  return agent_id.replace("/", "-")


class AgentStore:
  """Persistent storage for agent info and output lines.

  Each agent gets a directory under .state/agents/<safe_id>/
  containing info.json and output.jsonl.

  Attributes:
    base_dir: Root directory for agent storage.
  """

  def __init__(self, base_dir=None):
    """Initialize the store.

    Args:
      base_dir: Override base directory for testing.
        Defaults to AGENTS_DIR.
    """
    self.base_dir = base_dir or AGENTS_DIR

  def _agent_dir(self, agent_id):
    """Return the directory path for an agent.

    Args:
      agent_id: Agent ID string.

    Returns:
      Path to the agent's storage directory.
    """
    return self.base_dir / _safe_id(agent_id)

  def save_info(self, info):
    """Persist an AgentInfo to disk.

    Args:
      info: AgentInfo dataclass instance.
    """
    d = self._agent_dir(info.agent_id)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "info.json"
    data = {
      "agent_id": info.agent_id,
      "workspace": info.workspace,
      "role": info.role,
      "cwd": info.cwd,
      "model": info.model,
      "state": info.state.value,
      "session_id": info.session_id,
      "total_cost_usd": info.total_cost_usd,
      "num_turns": info.num_turns,
      "started_at": info.started_at,
      "finished_at": info.finished_at,
      "error": info.error,
    }
    with open(path, "w") as f:
      json.dump(data, f, indent=2)

  def load_info(self, agent_id):
    """Load an AgentInfo from disk.

    Args:
      agent_id: Agent ID string.

    Returns:
      AgentInfo instance, or None if not found.
    """
    path = self._agent_dir(agent_id) / "info.json"
    if not path.exists():
      return None
    try:
      with open(path) as f:
        data = json.load(f)
    except (json.JSONDecodeError, OSError):
      log.warning("Failed to load info for %s", agent_id)
      return None
    return AgentInfo(
      agent_id=data["agent_id"],
      workspace=data["workspace"],
      role=data["role"],
      cwd=data["cwd"],
      model=data.get("model", "sonnet"),
      state=AgentState(data.get("state", "pending")),
      session_id=data.get("session_id"),
      total_cost_usd=data.get("total_cost_usd", 0.0),
      num_turns=data.get("num_turns", 0),
      started_at=data.get("started_at", 0.0),
      finished_at=data.get("finished_at"),
      error=data.get("error"),
    )

  def append_output(self, agent_id, lines):
    """Append output lines to the agent's JSONL file.

    Args:
      agent_id: Agent ID string.
      lines: List of output line dicts.
    """
    d = self._agent_dir(agent_id)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "output.jsonl"
    with open(path, "a") as f:
      for line in lines:
        f.write(json.dumps(line) + "\n")

  def load_output(self, agent_id, from_line=0):
    """Load output lines from disk.

    Args:
      agent_id: Agent ID string.
      from_line: Skip lines with line_no < from_line.

    Returns:
      List of output line dicts.
    """
    path = self._agent_dir(agent_id) / "output.jsonl"
    if not path.exists():
      return []
    lines = []
    try:
      with open(path) as f:
        for raw in f:
          raw = raw.strip()
          if not raw:
            continue
          try:
            entry = json.loads(raw)
          except json.JSONDecodeError:
            continue
          if entry.get("line_no", 0) >= from_line:
            lines.append(entry)
    except OSError:
      log.warning(
        "Failed to load output for %s", agent_id
      )
    return lines

  def list_agents(self):
    """List all persisted agents.

    Returns:
      List of AgentInfo instances, sorted by started_at
      descending (newest first).
    """
    if not self.base_dir.exists():
      return []
    agents = []
    for d in self.base_dir.iterdir():
      if not d.is_dir():
        continue
      info_path = d / "info.json"
      if not info_path.exists():
        continue
      # Recover agent_id from info.json, not dirname.
      try:
        with open(info_path) as f:
          data = json.load(f)
        agent_id = data["agent_id"]
      except (json.JSONDecodeError, OSError, KeyError):
        continue
      info = self.load_info(agent_id)
      if info is not None:
        agents.append(info)
    agents.sort(
      key=lambda i: i.started_at, reverse=True
    )
    return agents
