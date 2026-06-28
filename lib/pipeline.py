"""Pipeline executor — runs pipeline steps sequentially.

Replaces the marker-based pipeline system. Steps are
either agent runs (LLM) or scripts (deterministic
Python functions). All state transitions go through
lib.db.
"""

import asyncio
import json
import logging
import subprocess
from collections import defaultdict

from lib.agent_runner import AgentInfo, AgentRunner, AgentState
from lib.config import (
  ROOT_DIR,
  get_repo_path,
  load_repos_config,
  parse_pipeline_roles,
  validate_repo,
)
from lib.git_utils import (
  GitError,
  get_branch_ref,
  get_branches,
  get_log,
  push_branch,
)
from pathlib import Path

from lib.config import WORKSPACES_DIR
from lib.notify import notify
from lib.protocol import serialize_sdk_message
from lib import db
from lib.worktree import (
  create_run_worktrees,
  get_run_dir,
  remove_run_worktrees,
)

log = logging.getLogger("takt.pipeline")


def write_run_result(workspace, run_id, db_path=None):
  """Write pipeline result to workspace .takt/ dir.

  Creates ~/dev/workspaces/<ws>/.takt/last-run.json with
  run status, step summaries, and error details so
  workspace agents can read it.

  Args:
    workspace: Workspace name.
    run_id: Completed run ID.
    db_path: Override for testing.
  """
  ws_dir = WORKSPACES_DIR / workspace
  if not ws_dir.exists():
    return
  takt_dir = ws_dir / ".takt"
  takt_dir.mkdir(exist_ok=True)

  run = db.get_run(run_id, db_path=db_path)
  steps = db.get_run_steps(run_id, db_path=db_path)

  step_summaries = []
  for s in steps:
    summary = {
      "name": s.get("role") or s.get("script", ""),
      "status": s["status"],
    }
    if s["status"] == "failed":
      output = db.get_output(
        s["id"], from_line=0, db_path=db_path,
      )
      summary["tail"] = output[-20:] if output else []
    step_summaries.append(summary)

  result = {
    "run_id": run_id,
    "workspace": workspace,
    "status": run["status"],
    "started_at": run.get("started_at"),
    "finished_at": run.get("finished_at"),
    "steps": step_summaries,
  }

  result_path = takt_dir / "last-run.json"
  result_path.write_text(json.dumps(result, indent=2))
  log.info(
    "Wrote run result to %s (%s)",
    result_path, run["status"],
  )


# -- Ref snapshot utilities (from pipeline_watch.py) --

def snapshot_all_refs(repos_config):
  """Snapshot current branch refs for all managed repos.

  Args:
    repos_config: Full repos config dict.

  Returns:
    Dict mapping "repo:branch" to commit hash.
  """
  refs = {}
  repos = repos_config.get("repos", {})
  for repo_name, cfg in repos.items():
    repo_path = get_repo_path(cfg.get("path", repo_name))
    if not validate_repo(cfg.get("path", repo_name)):
      continue
    try:
      branches = get_branches(repo_path)
    except GitError:
      continue
    for branch in branches:
      try:
        ref = get_branch_ref(repo_path, branch)
        refs[f"{repo_name}:{branch}"] = ref
      except GitError:
        continue
  return refs


def find_changes(old_refs, new_refs):
  """Compare old and new refs, return changes.

  Args:
    old_refs: Previous ref snapshot dict.
    new_refs: Current ref snapshot dict.

  Returns:
    List of dicts with keys: repo, branch, old_ref,
    new_ref, type ("new", "updated", "deleted").
  """
  changes = []
  all_keys = set(old_refs.keys()) | set(new_refs.keys())
  for key in sorted(all_keys):
    repo, branch = key.split(":", 1)
    old = old_refs.get(key)
    new = new_refs.get(key)
    if old is None and new is not None:
      changes.append({
        "repo": repo, "branch": branch,
        "old_ref": None, "new_ref": new, "type": "new",
      })
    elif old is not None and new is None:
      changes.append({
        "repo": repo, "branch": branch,
        "old_ref": old, "new_ref": None,
        "type": "deleted",
      })
    elif old != new:
      changes.append({
        "repo": repo, "branch": branch,
        "old_ref": old, "new_ref": new,
        "type": "updated",
      })
  return changes


def group_by_branch(changes):
  """Group changes by branch name.

  Args:
    changes: List of change dicts.

  Returns:
    Dict mapping branch_name to list of change dicts.
  """
  groups = defaultdict(list)
  for change in changes:
    groups[change["branch"]].append(change)
  return dict(groups)


# -- Built-in script steps --

def script_push_to_github(run, config):
  """Push branch from root repos to GitHub.

  Args:
    run: Run dict from db.
    config: Step config dict (unused).

  Returns:
    Result dict with pushed repos and errors.
  """
  workspace = run["workspace"]
  repos = json.loads(run["repos_json"])
  repos_config = load_repos_config()
  all_repos = repos_config.get("repos", {})
  pushed = []
  errors = []
  # Sort by push_order.
  repo_cfgs = []
  for repo in repos:
    cfg = all_repos.get(repo, {})
    repo_cfgs.append((repo, cfg))
  repo_cfgs.sort(
    key=lambda x: x[1].get("push_order", 999)
  )
  for repo, cfg in repo_cfgs:
    repo_path = get_repo_path(cfg.get("path", repo))
    try:
      push_branch(repo_path, workspace)
      pushed.append(repo)
      log.info("Pushed %s/%s to GitHub", repo, workspace)
    except GitError as e:
      errors.append({"repo": repo, "error": str(e)})
      log.error(
        "Failed to push %s/%s: %s", repo, workspace, e,
      )
  status = "pass" if not errors else "fail"
  return {
    "status": status,
    "pushed": pushed,
    "errors": errors,
  }


def script_create_pr(run, config):
  """Create a GitHub PR using the gh CLI.

  Args:
    run: Run dict from db.
    config: Step config dict. Optional keys:
      - base: Base branch (default: main).
      - title: PR title override.
      - body: PR body override.

  Returns:
    Result dict with PR URLs or errors.
  """
  workspace = run["workspace"]
  repos = json.loads(run["repos_json"])
  repos_config = load_repos_config()
  all_repos = repos_config.get("repos", {})
  base = config.get("base", "main")
  prs = []
  errors = []
  for repo in repos:
    cfg = all_repos.get(repo, {})
    repo_path = get_repo_path(cfg.get("path", repo))
    # Check if branch exists on remote.
    try:
      remote_branches = get_branches(repo_path, remote=True)
    except GitError:
      continue
    remote_branch = f"origin/{workspace}"
    if remote_branch not in remote_branches:
      continue
    # Build PR title and body.
    title = config.get(
      "title",
      f"{workspace}: pipeline PR for {repo}",
    )
    try:
      body_log = get_log(
        repo_path, base=base, head=workspace,
        max_count=20,
      )
    except GitError:
      body_log = "(log unavailable)"
    body = config.get("body", "") or ""
    body += f"\n\n## Commits\n```\n{body_log}\n```"
    try:
      result = subprocess.run(
        [
          "gh", "pr", "create",
          "--repo", _gh_repo_name(repo_path),
          "--head", workspace,
          "--base", base,
          "--title", title,
          "--body", body,
        ],
        capture_output=True, text=True, check=True,
        cwd=str(repo_path),
      )
      url = result.stdout.strip()
      prs.append({"repo": repo, "url": url})
      log.info("Created PR for %s: %s", repo, url)
    except subprocess.CalledProcessError as e:
      # PR may already exist.
      if "already exists" in (e.stderr or ""):
        log.info("PR already exists for %s", repo)
        prs.append({"repo": repo, "url": "exists"})
      else:
        errors.append({"repo": repo, "error": e.stderr})
        log.error(
          "Failed to create PR for %s: %s",
          repo, e.stderr,
        )
  status = "pass" if not errors else "fail"
  return {"status": status, "prs": prs, "errors": errors}


def script_merge_upstream(run, config):
  """Fetch and merge default branch into workspace branch.

  Args:
    run: Run dict from db.
    config: Step config dict (unused).

  Returns:
    Result dict with merged repos and errors.
  """
  repos = json.loads(run["repos_json"])
  repos_config = load_repos_config()
  all_repos = repos_config.get("repos", {})
  merged = []
  errors = []
  for repo in repos:
    cfg = all_repos.get(repo, {})
    repo_path = get_repo_path(cfg.get("path", repo))
    default_br = cfg.get("default_branch", "main")
    wt_dir = run.get("worktree_dir")
    if wt_dir:
      work_path = f"{wt_dir}/{repo}"
    else:
      work_path = str(repo_path)
    try:
      subprocess.run(
        ["git", "fetch", "origin", default_br],
        capture_output=True, text=True, check=True,
        cwd=work_path,
      )
      subprocess.run(
        ["git", "merge", f"origin/{default_br}",
         "--no-edit"],
        capture_output=True, text=True, check=True,
        cwd=work_path,
      )
      merged.append(repo)
    except subprocess.CalledProcessError as e:
      errors.append({"repo": repo, "error": e.stderr})
      log.error(
        "Failed to merge upstream for %s: %s",
        repo, e.stderr,
      )
  status = "pass" if not errors else "fail"
  return {
    "status": status,
    "merged": merged,
    "errors": errors,
  }


def script_check_stubs(run, config):
  """Scan workspace repos for stub patterns in the diff.

  Checks only lines added since the branch diverged from the
  default branch. Flags TODO, FIXME, NotImplementedError,
  bare pass, empty bodies, etc.

  Config options:
    mode: "diff" (default, only new lines) or "full" (all files)
    fail_on_new: true (default) — fail the step if new stubs found

  Args:
    run: Run dict from db.
    config: Step config dict.

  Returns:
    Result dict with hits, summary, and pass/fail status.
  """
  from lib.stub_finder import (
    scan_directory,
    scan_git_diff,
    format_hits,
  )
  repos = json.loads(run["repos_json"])
  repos_config = load_repos_config()
  all_repos = repos_config.get("repos", {})
  mode = config.get("mode", "diff")
  fail_on_new = config.get("fail_on_new", True)
  workspace = run["workspace"]
  all_hits = []
  for repo in repos:
    cfg = all_repos.get(repo, {})
    default_br = cfg.get("default_branch", "main")
    wt_dir = run.get("worktree_dir")
    if wt_dir:
      repo_path = f"{wt_dir}/{repo}"
    else:
      repo_path = get_repo_path(cfg.get("path", repo))
    repo_path = str(repo_path)
    if mode == "diff":
      result = scan_git_diff(
        repo_path, base=f"origin/{default_br}",
      )
    else:
      result = scan_directory(repo_path)
    for h in result["new_hits"]:
      h["repo"] = repo
    all_hits.extend(result["new_hits"])
  total = len(all_hits)
  if all_hits:
    log.warning(
      "Stub check: %d stub(s) found in %s:\n%s",
      total, workspace, format_hits(all_hits),
    )
  else:
    log.info("Stub check: clean (%s)", workspace)
  status = "fail" if (fail_on_new and total > 0) else "pass"
  return {
    "status": status,
    "hits": all_hits,
    "summary": {"total": total},
  }


# Registry of built-in script steps.
def _make_check_step(check_fn):
  """Wrap a lib.checks function as a pipeline step."""
  def step(run, config):
    repos = json.loads(run["repos_json"])
    workspace = run["workspace"]
    wt_dir = run.get("worktree_dir")
    if wt_dir:
      ws_path = wt_dir
    else:
      ws_path = str(WORKSPACES_DIR / workspace)
    result = check_fn(ws_path, repos, **{
      k: v for k, v in config.items()
      if k not in ("step", "role", "script")
    })
    return {
      "status": result["status"],
      "summary": result.get("summary", ""),
      "detail": result,
    }
  return step


def _make_check_step_no_kwargs(check_fn):
  """Wrap a check that only takes ws_path + repos."""
  def step(run, config):
    repos = json.loads(run["repos_json"])
    workspace = run["workspace"]
    wt_dir = run.get("worktree_dir")
    ws_path = wt_dir if wt_dir else str(
      WORKSPACES_DIR / workspace
    )
    result = check_fn(ws_path, repos)
    return {
      "status": result["status"],
      "summary": result.get("summary", ""),
      "detail": result,
    }
  return step


from lib.checks import (
  check_build,
  check_diff_size,
  check_freshness,
  check_secrets,
  check_tests,
)

SCRIPT_REGISTRY = {
  "push_to_github": script_push_to_github,
  "create_pr": script_create_pr,
  "merge_upstream": script_merge_upstream,
  "check_stubs": script_check_stubs,
  "check_freshness": _make_check_step_no_kwargs(
    check_freshness,
  ),
  "check_build": _make_check_step_no_kwargs(
    check_build,
  ),
  "check_tests": _make_check_step_no_kwargs(
    check_tests,
  ),
  "check_secrets": _make_check_step_no_kwargs(
    check_secrets,
  ),
  "check_diff_size": _make_check_step(
    check_diff_size,
  ),
}


def _gh_repo_name(repo_path):
  """Extract the GitHub owner/repo from git remote.

  Args:
    repo_path: Path to the git repo.

  Returns:
    "owner/repo" string, or empty string on failure.
  """
  try:
    result = subprocess.run(
      ["git", "remote", "get-url", "origin"],
      capture_output=True, text=True, check=True,
      cwd=str(repo_path),
    )
    url = result.stdout.strip()
    # Handle SSH and HTTPS URLs.
    if url.startswith("git@"):
      # git@github.com:owner/repo.git
      url = url.split(":", 1)[1]
    elif "github.com/" in url:
      url = url.split("github.com/", 1)[1]
    return url.removesuffix(".git")
  except (subprocess.CalledProcessError, IndexError):
    return ""


# -- Pipeline executor --

class PipelineExecutor:
  """Executes pipeline runs: worktree setup, sequential
  steps, teardown.

  Attributes:
    on_output: Optional callback(step_id, lines) for
      streaming agent output.
    on_step_update: Optional callback(step_id, status)
      for step status changes.
  """

  def __init__(self, on_output=None, on_step_update=None,
               db_path=None):
    """Initialize the executor.

    Args:
      on_output: Callback(step_id, lines) called when
        agent produces output.
      on_step_update: Callback(step_id, status) called
        on step transitions.
      db_path: Override DB path for testing.
    """
    self.on_output = on_output
    self.on_step_update = on_step_update
    self._db_path = db_path

  async def execute_run(self, run_id):
    """Execute a full pipeline run.

    Sets up worktrees, runs steps sequentially, tears
    down worktrees. Updates run and step statuses in DB.

    Args:
      run_id: Run row ID.

    Returns:
      Final run status string.
    """
    run = db.get_run(run_id, db_path=self._db_path)
    if run is None:
      log.error("Run %d not found", run_id)
      return "failed"
    workspace = run["workspace"]
    repos = json.loads(run["repos_json"])
    # Transition run to running.
    with db._connect(self._db_path) as conn:
      conn.execute(
        "UPDATE runs SET status = 'running', "
        "started_at = strftime('%Y-%m-%dT%H:%M:%fZ',"
        "'now') WHERE id = ?",
        (run_id,),
      )
    db.log_event(
      "run", run_id, "queued", "running",
      "executor started", db_path=self._db_path,
    )
    # Setup worktrees.
    run_dir = None
    if repos:
      try:
        run_dir = self.setup_worktrees(
          run_id, workspace, repos,
        )
        db.set_run_worktree(
          run_id, str(run_dir),
          db_path=self._db_path,
        )
      except Exception as e:
        log.error(
          "Worktree setup failed for run %d: %s",
          run_id, e,
        )
        with db._connect(self._db_path) as conn:
          conn.execute(
            "UPDATE runs SET status = 'failed', "
            "finished_at = strftime("
            "'%Y-%m-%dT%H:%M:%fZ','now') "
            "WHERE id = ?",
            (run_id,),
          )
        db.log_event(
          "run", run_id, "running", "failed",
          f"worktree setup: {e}",
          db_path=self._db_path,
        )
        return "failed"
    # Run steps sequentially.
    steps = db.get_run_steps(
      run_id, db_path=self._db_path,
    )
    for step in steps:
      step_id = step["id"]
      # Queue the step.
      db.advance_step(
        step_id, "queued", db_path=self._db_path,
      )
      self._notify_step(step_id, "queued")
      # Run the step.
      db.advance_step(
        step_id, "running", db_path=self._db_path,
      )
      self._notify_step(step_id, "running")
      try:
        await self.run_step(step, run, run_dir)
      except asyncio.CancelledError:
        db.advance_step(
          step_id, "cancelled",
          reason="run cancelled",
          db_path=self._db_path,
        )
        self._notify_step(step_id, "cancelled")
        break
      except Exception as e:
        log.error(
          "Step %s failed: %s", step["name"], e,
          exc_info=True,
        )
        db.advance_step(
          step_id, "failed",
          error=str(e), db_path=self._db_path,
        )
        self._notify_step(step_id, "failed")
        break
      # Check step result.
      updated = db.get_step(
        step_id, db_path=self._db_path,
      )
      if updated["status"] != "completed":
        break
    # Update run status.
    run_status = db.advance_run(
      run_id, db_path=self._db_path,
    )
    # Write result to workspace for agent pickup.
    if run_status in ("passed", "failed", "cancelled"):
      try:
        write_run_result(
          workspace, run_id, db_path=self._db_path,
        )
      except Exception as e:
        log.warning(
          "Failed to write run result for %s: %s",
          workspace, e,
        )
    # Teardown worktrees.
    if run_dir and repos:
      try:
        self.teardown_worktrees(
          run_id, workspace, repos,
        )
      except Exception as e:
        log.warning(
          "Worktree teardown failed for run %d: %s",
          run_id, e,
        )
    # Notify.
    if run_status == "passed":
      notify(
        f"Pipeline passed: {workspace}",
        "All steps completed successfully.",
      )
    elif run_status == "failed":
      notify(
        f"Pipeline failed: {workspace}",
        f"Run {run_id} failed.",
        urgency="critical",
      )
    return run_status

  def setup_worktrees(self, run_id, workspace, repos):
    """Create worktrees for a run.

    Args:
      run_id: Run row ID.
      workspace: Workspace name (= branch).
      repos: List of repo names.

    Returns:
      Path to the run directory.
    """
    return create_run_worktrees(
      run_id, workspace, repos, workspace,
    )

  def teardown_worktrees(self, run_id, workspace, repos):
    """Remove worktrees for a run.

    Args:
      run_id: Run row ID.
      workspace: Workspace name.
      repos: List of repo names.
    """
    remove_run_worktrees(run_id, workspace, repos)

  async def run_step(self, step, run, run_dir):
    """Dispatch a step to the appropriate handler.

    Args:
      step: Step dict from db.
      run: Run dict from db.
      run_dir: Path to run worktree directory.
    """
    if step["step_type"] == "agent":
      await self.run_agent_step(step, run, run_dir)
    elif step["step_type"] == "script":
      await self.run_script_step(step, run)
    else:
      raise ValueError(
        f"Unknown step_type: {step['step_type']}"
      )

  async def run_agent_step(self, step, run, run_dir):
    """Run an agent step.

    Builds a prompt from the role template, launches
    AgentRunner, streams output to SQLite.

    Args:
      step: Step dict from db.
      run: Run dict from db.
      run_dir: Path to run worktree directory.
    """
    step_id = step["id"]
    config = json.loads(step["config_json"])
    role = step["name"]
    workspace = run["workspace"]
    repos = json.loads(run["repos_json"])
    # Build prompt.
    prompt = self._build_agent_prompt(
      role, workspace, repos, config, run,
    )
    # Determine working directory.
    if run_dir and repos:
      cwd = str(run_dir / repos[0]) if len(repos) == 1 \
        else str(run_dir)
    else:
      cwd = str(get_run_dir(run["id"], workspace))
    # Create agent info.
    agent_id = f"run-{run['id']}/{role}"
    info = AgentInfo(
      agent_id=agent_id,
      workspace=workspace,
      role=role,
      cwd=cwd,
      model=config.get("model", "sonnet"),
    )
    # Track output line count.
    line_count = [0]

    def on_message(msg):
      lines = serialize_sdk_message(msg, line_count[0])
      if not lines:
        return
      line_count[0] += len(lines)
      db.record_output(
        step_id, lines, db_path=self._db_path,
      )
      if self.on_output:
        self.on_output(step_id, lines)
    # Run with timeout.
    timeout = step.get("timeout_secs", 1800)
    from lib.config import PROJECT_DIR
    add_dirs = [str(PROJECT_DIR), str(ROOT_DIR)]
    runner = AgentRunner(info, add_dirs=add_dirs)
    try:
      await asyncio.wait_for(
        runner.run(prompt, on_message),
        timeout=timeout,
      )
    except asyncio.TimeoutError:
      runner.cancel()
      raise TimeoutError(
        f"Agent step {role} timed out after {timeout}s"
      )
    # Read .stage-result.json if present.
    result = self._read_stage_result(cwd)
    if info.state == AgentState.COMPLETED:
      if result and result.get("status") == "fail":
        db.advance_step(
          step_id, "failed",
          result_json=json.dumps(result),
          error=result.get("summary", "agent reported fail"),
          cost_usd=info.total_cost_usd,
          num_turns=info.num_turns,
          db_path=self._db_path,
        )
      else:
        db.advance_step(
          step_id, "completed",
          result_json=json.dumps(result) if result else None,
          cost_usd=info.total_cost_usd,
          num_turns=info.num_turns,
          db_path=self._db_path,
        )
    else:
      db.advance_step(
        step_id, "failed",
        error=info.error or "agent did not complete",
        cost_usd=info.total_cost_usd,
        num_turns=info.num_turns,
        db_path=self._db_path,
      )
    self._notify_step(
      step_id,
      db.get_step(step_id, db_path=self._db_path)["status"],
    )

  async def run_script_step(self, step, run):
    """Run a built-in script step.

    Args:
      step: Step dict from db.
      run: Run dict from db.
    """
    step_id = step["id"]
    name = step["name"]
    config = json.loads(step["config_json"])
    func = SCRIPT_REGISTRY.get(name)
    if func is None:
      raise ValueError(f"Unknown script: {name}")
    loop = asyncio.get_running_loop()
    # Run script in executor thread.
    result = await loop.run_in_executor(
      None, func, run, config,
    )
    result_json = json.dumps(result)
    if result.get("status") == "pass":
      db.advance_step(
        step_id, "completed",
        result_json=result_json,
        db_path=self._db_path,
      )
    else:
      db.advance_step(
        step_id, "failed",
        result_json=result_json,
        error=json.dumps(result.get("errors", [])),
        db_path=self._db_path,
      )
    self._notify_step(
      step_id,
      db.get_step(step_id, db_path=self._db_path)["status"],
    )

  def _build_agent_prompt(self, role, workspace, repos,
                          config, run):
    """Build an agent prompt from role template and context.

    Args:
      role: Role slug (e.g. "test", "review").
      config: Step config dict.
      workspace: Workspace name.
      repos: List of repo names.
      run: Run dict from db.

    Returns:
      Prompt string.
    """
    roles = parse_pipeline_roles()
    role_snippet = roles.get(role, "")
    parts = []
    if role_snippet:
      parts.append(f"# Role: {role}\n\n{role_snippet}\n")
    parts.append(f"Branch: `{workspace}`")
    parts.append(f"Repos: {', '.join(repos)}")
    # Add commit context.
    refs = json.loads(run["head_refs_json"])
    repos_config = load_repos_config().get("repos", {})
    for repo in repos:
      ref = refs.get(repo)
      if not ref:
        continue
      cfg = repos_config.get(repo, {})
      repo_path = get_repo_path(
        cfg.get("path", repo)
      )
      try:
        log_text = get_log(
          repo_path, head=ref, max_count=10,
        )
        if log_text:
          parts.append(f"\n## {repo} recent commits\n```\n{log_text}\n```")
      except GitError:
        pass
    # Add custom prompt from config.
    custom = config.get("prompt")
    if custom:
      parts.append(f"\n{custom}")
    parts.append(
      "\nWrite results to .stage-result.json:\n"
      '  {"status":"pass|fail","summary":"...",'
      '"failures":[]}\n'
      "Do NOT push. Do NOT modify git remotes."
    )
    return "\n".join(parts)

  def _read_stage_result(self, cwd):
    """Read .stage-result.json from the working directory.

    Args:
      cwd: Working directory path string.

    Returns:
      Parsed dict, or None if not found.
    """
    from pathlib import Path
    result_path = Path(cwd) / ".stage-result.json"
    if not result_path.exists():
      return None
    try:
      with open(result_path) as f:
        return json.load(f)
    except (json.JSONDecodeError, OSError):
      return None

  def _notify_step(self, step_id, status):
    """Call the step update callback if set.

    Args:
      step_id: Step row ID.
      status: New status string.
    """
    if self.on_step_update:
      self.on_step_update(step_id, status)
