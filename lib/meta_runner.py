"""Meta agent executor — runs agents on takt itself.

Meta agents operate on the takt project directory rather
than workspace repos. They have access to takt's own code,
config, templates, and workspace directories.
"""

import logging

from lib import db
from lib.agent_runner import AgentInfo, AgentRunner
from lib.config import (
  PROJECT_DIR,
  ROOT_DIR,
  WORKSPACES_DIR,
  load_repos_config,
)
from lib.protocol import serialize_sdk_message
from lib.workspace_ops import list_workspaces

log = logging.getLogger("takt.meta_runner")

MODEL_MAP = {
  "sonnet": "claude-sonnet-4-6",
  "opus": "claude-opus-4-6",
  "haiku": "claude-haiku-4-5",
}


def _build_context():
  """Build takt context string for meta agent prompts.

  Returns:
    Context string with project summary, workspaces,
    repos, and templates.
  """
  parts = []
  parts.append("# takt Project Context\n")
  # CLAUDE.md summary.
  claude_md = PROJECT_DIR / "CLAUDE.md"
  if claude_md.exists():
    text = claude_md.read_text()
    if len(text) > 2000:
      text = text[:2000] + "\n...(truncated)"
    parts.append(f"## CLAUDE.md\n{text}\n")
  # Workspace list.
  try:
    workspaces = list_workspaces()
    if workspaces:
      ws_lines = [
        f"- {ws['name']} ({len(ws.get('repos', []))} repos)"
        for ws in workspaces
      ]
      parts.append(
        "## Workspaces\n" + "\n".join(ws_lines) + "\n"
      )
  except Exception:
    pass
  # Repo list.
  try:
    repos_config = load_repos_config()
    repos = repos_config.get("repos", {})
    if repos:
      repo_lines = [f"- {name}" for name in sorted(repos)]
      parts.append(
        "## Repos\n" + "\n".join(repo_lines) + "\n"
      )
  except Exception:
    pass
  # Template list.
  templates_dir = PROJECT_DIR / "templates"
  if templates_dir.exists():
    tpl_files = sorted(templates_dir.glob("*.md"))
    if tpl_files:
      tpl_lines = [f"- {f.name}" for f in tpl_files]
      parts.append(
        "## Templates\n" + "\n".join(tpl_lines) + "\n"
      )
  return "\n".join(parts)


class MetaAgentExecutor:
  """Executes a meta agent against the takt project.

  Attributes:
    run_id: Meta agent run row ID.
    meta_agent: Dict with meta agent definition.
    on_output: Callback(run_id, lines) for output.
    on_status_update: Callback(run_id, status) for
      status changes.
  """

  def __init__(self, run_id, meta_agent,
               on_output=None, on_status_update=None,
               db_path=None):
    """Initialize the executor.

    Args:
      run_id: Meta agent run row ID.
      meta_agent: Dict from get_meta_agent().
      on_output: Callback(run_id, lines).
      on_status_update: Callback(run_id, status).
      db_path: Override path for testing.
    """
    self.run_id = run_id
    self.meta_agent = meta_agent
    self.on_output = on_output
    self.on_status_update = on_status_update
    self._db_path = db_path
    self._line_no = 0
    self._runner = None

  async def execute(self):
    """Run the meta agent to completion.

    Returns:
      Final status string (completed/failed/cancelled).
    """
    agent = self.meta_agent
    short_model = agent.get("model", "sonnet")
    model = MODEL_MAP.get(short_model, short_model)
    # Build prompt with takt context.
    context = _build_context()
    full_prompt = (
      f"{context}\n---\n\n{agent['prompt']}"
    )
    info = AgentInfo(
      agent_id=f"meta/{agent['name']}",
      workspace="",
      role=agent["name"],
      cwd=str(PROJECT_DIR),
      model=model,
    )
    add_dirs = [
      str(PROJECT_DIR),
      str(ROOT_DIR),
      str(WORKSPACES_DIR),
    ]
    self._runner = AgentRunner(info, add_dirs=add_dirs)
    # Transition to running.
    db.advance_meta_run(
      self.run_id, "running",
      db_path=self._db_path,
    )
    if self.on_status_update:
      self.on_status_update(self.run_id, "running")
    try:
      await self._runner.run(
        full_prompt, self._on_message,
      )
      state = info.state.value
      if state == "completed":
        db.advance_meta_run(
          self.run_id, "completed",
          cost_usd=info.total_cost_usd,
          num_turns=info.num_turns,
          db_path=self._db_path,
        )
      elif state == "cancelled":
        db.advance_meta_run(
          self.run_id, "cancelled",
          cost_usd=info.total_cost_usd,
          num_turns=info.num_turns,
          db_path=self._db_path,
        )
      else:
        db.advance_meta_run(
          self.run_id, "failed",
          error=info.error,
          cost_usd=info.total_cost_usd,
          num_turns=info.num_turns,
          db_path=self._db_path,
        )
      final_status = state
    except Exception as e:
      log.error(
        "Meta agent %s run %d failed: %s",
        agent["name"], self.run_id, e,
        exc_info=True,
      )
      db.advance_meta_run(
        self.run_id, "failed", error=str(e),
        db_path=self._db_path,
      )
      final_status = "failed"
    if self.on_status_update:
      self.on_status_update(self.run_id, final_status)
    return final_status

  def _on_message(self, msg):
    """Handle an SDK message from the agent.

    Serializes the message, records it in DB, and
    calls the output callback.

    Args:
      msg: SDK message object.
    """
    lines = serialize_sdk_message(msg, self._line_no)
    if not lines:
      return
    self._line_no += len(lines)
    try:
      db.record_meta_output(
        self.run_id, lines, db_path=self._db_path,
      )
    except Exception:
      log.debug(
        "Failed to record meta output", exc_info=True
      )
    if self.on_output:
      self.on_output(self.run_id, lines)

  def cancel(self):
    """Signal the running agent to stop."""
    if self._runner:
      self._runner.cancel()
