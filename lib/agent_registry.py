"""Global registry of running and completed agents.

Module-level dict keyed by agent_id (e.g. "ws/role").
"""

from lib.agent_runner import AgentRunner, AgentState

_agents: dict[str, AgentRunner] = {}


def register(runner):
  """Register an agent runner.

  Args:
    runner: AgentRunner instance.
  """
  _agents[runner.info.agent_id] = runner


def unregister(agent_id):
  """Remove an agent from the registry.

  Args:
    agent_id: The agent ID to remove.
  """
  _agents.pop(agent_id, None)


def get(agent_id):
  """Get a runner by agent_id.

  Args:
    agent_id: The agent ID.

  Returns:
    AgentRunner or None.
  """
  return _agents.get(agent_id)


def is_running(agent_id):
  """Check if an agent is currently running.

  Args:
    agent_id: The agent ID.

  Returns:
    True if the agent exists and is in RUNNING state.
  """
  runner = _agents.get(agent_id)
  if runner is None:
    return False
  return runner.info.state == AgentState.RUNNING


def list_active():
  """List all runners in RUNNING state.

  Returns:
    List of AgentRunner instances.
  """
  return [
    r for r in _agents.values()
    if r.info.state == AgentState.RUNNING
  ]


def list_all():
  """List all registered runners.

  Returns:
    List of AgentRunner instances.
  """
  return list(_agents.values())


def list_failed():
  """List all runners in FAILED state.

  Returns:
    List of AgentRunner instances.
  """
  return [
    r for r in _agents.values()
    if r.info.state == AgentState.FAILED
  ]


def clear_finished():
  """Remove all completed, failed, and cancelled runners.

  Returns:
    Number of runners removed.
  """
  finished = [
    aid for aid, r in _agents.items()
    if r.info.state in (
      AgentState.COMPLETED,
      AgentState.FAILED,
      AgentState.CANCELLED,
    )
  ]
  for aid in finished:
    del _agents[aid]
  return len(finished)
