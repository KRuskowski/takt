#!/usr/bin/env python3
"""MCP server exposing takt tools to Claude CLI sessions.

Wraps the takt REST API (default http://127.0.0.1:7433) as MCP
tools so agents running in workspaces can query pipeline state,
manage targets, trigger runs, etc.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from mcp.server.fastmcp import FastMCP

WORKSPACES_DIR = PROJECT_DIR.parent / "workspaces"

TAKT_API = os.environ.get(
  "TAKT_API_URL", "http://127.0.0.1:7433"
)
TAKT_TOKEN = os.environ.get("TAKT_API_TOKEN", "")

mcp = FastMCP(
  "takt",
  instructions=(
    "takt is a pipeline orchestration system for agentic "
    "software development. It manages workspaces (isolated "
    "repo clones with feature branches), build/test targets "
    "(VMs with exclusive locking), and multi-step pipelines "
    "where each step is an AI agent role (feature, test, "
    "deploy_qa, etc.) or a script. Agents never push to "
    "GitHub — takt handles all pushes and PR creation. "
    "All state is in SQLite (.state/takt.db). Root repos "
    "live at ~/dev/root/, workspaces at "
    "~/dev/workspaces/<name>/.\n\n"
    "takt also holds the shared knowledge base for this "
    "work. Call search() before guessing how something "
    "builds, why a decision was made, or whether a "
    "problem is already understood — it returns the "
    "relevant section of the docs, plus facts recorded by "
    "other agents, scoped per repo and stamped with the "
    "run that produced them. When you learn something the "
    "next agent would otherwise rediscover, call "
    "record_fact() so it is not lost with your session."
  ),
)


def _api(method, path, body=None):
  """Call the takt REST API.

  Args:
    method: HTTP method.
    path: API path (e.g. "/api/workspaces").
    body: Optional dict to send as JSON.

  Returns:
    Parsed JSON response data.
  """
  url = f"{TAKT_API}{path}"
  data = None
  if body is not None:
    data = json.dumps(body).encode()
  headers = {"Content-Type": "application/json"}
  # Set when talking to a scheduler that is not on this
  # machine. A local loopback service needs no token.
  if TAKT_TOKEN:
    headers["Authorization"] = f"Bearer {TAKT_TOKEN}"
  req = urllib.request.Request(
    url, data=data, method=method, headers=headers,
  )
  try:
    with urllib.request.urlopen(req, timeout=30) as resp:
      result = json.loads(resp.read())
      return result.get("data", result)
  except urllib.error.HTTPError as e:
    body_text = e.read().decode()
    try:
      err = json.loads(body_text)
      msg = err.get("message", body_text)
    except Exception:
      msg = body_text
    return {"error": msg, "status": e.code}
  except urllib.error.URLError as e:
    return {
      "error": f"takt-service unreachable: {e.reason}"
    }


# -- Workspace tools --

@mcp.tool()
def list_workspaces() -> str:
  """List all takt workspaces. A workspace is an isolated set of local repo clones at ~/dev/workspaces/<name>/ where agents work on a feature branch. The workspace name IS the branch name across all repos."""
  return json.dumps(_api("GET", "/api/workspaces"))


@mcp.tool()
def workspace_status(name: str) -> str:
  """Get per-repo git status for a workspace: branch, ahead/behind counts, dirty files. Use this to check if a workspace has uncommitted work or needs rebasing.

  Args:
    name: Workspace name.
  """
  return json.dumps(
    _api("GET", f"/api/workspaces/{name}/status")
  )


@mcp.tool()
def create_workspace(
  name: str,
  repos: list[str],
  base_branch: str = "",
) -> str:
  """Create a new workspace by cloning repos from ~/dev/root/ into ~/dev/workspaces/<name>/. Each clone gets a branch named after the workspace cut from base_branch. Use list_repos to see available repos. Workspace names MUST include the project name as a prefix (e.g. relay-appliance-cli, not just appliance-cli).

  Args:
    name: Workspace name (becomes the branch name).
    repos: List of repo names to clone.
    base_branch: Branch to cut from. If empty, uses each repo's default branch.
  """
  payload = {"name": name, "repos": repos}
  if base_branch:
    payload["base_branch"] = base_branch
  return json.dumps(
    _api("POST", "/api/workspaces", payload)
  )


@mcp.tool()
def delete_workspace(name: str) -> str:
  """Delete a workspace and all its local repo clones. Does not delete remote branches.

  Args:
    name: Workspace name.
  """
  return json.dumps(
    _api("DELETE", f"/api/workspaces/{name}")
  )


# -- Target tools --

@mcp.tool()
def list_targets() -> str:
  """List all build/test targets — Debian and Windows VMs on the vmnet (10.101.0.0/24). Shows each target's type, IP, claimed-by workspace (exclusive lock), and VM state (running/shut off). Targets marked as templates are read-only base images."""
  return json.dumps(_api("GET", "/api/targets"))


@mcp.tool()
def claim_target(name: str, workspace: str) -> str:
  """Claim a target VM for exclusive use by a workspace. Only one workspace can hold a target at a time. You MUST claim before deploying or running commands on a VM.

  Args:
    name: Target name (e.g. dev-01, dev-02, win11-build).
    workspace: Workspace to claim for.
  """
  return json.dumps(
    _api("POST", f"/api/targets/{name}/claim", {
      "workspace": workspace,
    })
  )


@mcp.tool()
def release_target(name: str) -> str:
  """Release a claimed target so other workspaces can use it. Always release when done, even on failure.

  Args:
    name: Target name.
  """
  return json.dumps(
    _api("POST", f"/api/targets/{name}/release")
  )


@mcp.tool()
def target_up(name: str) -> str:
  """Start a target VM via libvirt. The VM must exist in the target inventory.

  Args:
    name: Target name.
  """
  return json.dumps(
    _api("POST", f"/api/targets/{name}/up")
  )


@mcp.tool()
def target_down(name: str) -> str:
  """Shut down a target VM gracefully via libvirt.

  Args:
    name: Target name.
  """
  return json.dumps(
    _api("POST", f"/api/targets/{name}/down")
  )


# -- Pipeline tools --

@mcp.tool()
def get_pipeline(workspace: str) -> str:
  """Get the ordered pipeline steps configured for a workspace. Each step is a role (agent type from templates/pipeline_roles.md) or a script path. Steps run sequentially in worktrees created from the root repos.

  Args:
    workspace: Workspace name.
  """
  return json.dumps(
    _api("GET", f"/api/pipeline/{workspace}")
  )


@mcp.tool()
def set_pipeline(
  workspace: str, steps: list[str],
) -> str:
  """Set the pipeline steps for a workspace. Steps run in order when a pipeline is triggered. Available roles: feature, test, deploy_qa, bindings, packaging, changelog. You can also use script paths.

  Args:
    workspace: Workspace name.
    steps: Ordered list of step names (roles or scripts).
  """
  return json.dumps(
    _api("PUT", f"/api/pipeline/{workspace}", {
      "steps": steps,
    })
  )


# -- Run tools --

@mcp.tool()
def list_runs(
  workspace: str = "", limit: int = 20,
) -> str:
  """List pipeline runs with status (running, completed, failed, cancelled), trigger source, and timestamps. Each run executes the workspace's pipeline steps in order.

  Args:
    workspace: Filter by workspace (empty for all).
    limit: Max results.
  """
  params = f"?limit={limit}"
  if workspace:
    params += f"&workspace={workspace}"
  return json.dumps(
    _api("GET", f"/api/runs{params}")
  )


@mcp.tool()
def trigger_run(workspace: str) -> str:
  """Trigger a pipeline run for a workspace. Creates worktrees from root repos and executes the configured pipeline steps sequentially. The workspace must have a pipeline configured (use set_pipeline first).

  Args:
    workspace: Workspace name.
  """
  return json.dumps(
    _api("POST", "/api/runs", {
      "workspace": workspace,
    })
  )


@mcp.tool()
def get_run(run_id: int) -> str:
  """Get details of a pipeline run including its steps, their statuses, and timing.

  Args:
    run_id: Run ID.
  """
  return json.dumps(
    _api("GET", f"/api/runs/{run_id}")
  )


@mcp.tool()
def cancel_run(run_id: int) -> str:
  """Cancel a running pipeline. Stops the current step and marks remaining steps as skipped.

  Args:
    run_id: Run ID.
  """
  return json.dumps(
    _api("POST", f"/api/runs/{run_id}/cancel")
  )


@mcp.tool()
def get_step_output(
  run_id: int, step_id: int, from_line: int = 0,
) -> str:
  """Get the log output from a pipeline step. Each line has a timestamp, kind (text/error/tool_use/thinking), and content. Use from_line to paginate for long outputs.

  Args:
    run_id: Run ID.
    step_id: Step ID.
    from_line: Start from this line number.
  """
  return json.dumps(
    _api(
      "GET",
      f"/api/runs/{run_id}/steps/{step_id}"
      f"/output?from={from_line}",
    )
  )


# -- Template and context tools --

@mcp.tool()
def list_templates() -> str:
  """List pipeline role templates from templates/. These define the CLAUDE.md instructions given to agents at each pipeline step (e.g. feature, test, deploy_qa, bindings, packaging, changelog)."""
  return json.dumps(_api("GET", "/api/templates"))


@mcp.tool()
def get_template(name: str) -> str:
  """Read a template file. Templates include workspace_claude.md (base workspace instructions), root_repo_claude.md (per-repo instructions), and pipeline_roles.md (agent role definitions).

  Args:
    name: Template filename (e.g. workspace_claude.md).
  """
  return json.dumps(
    _api("GET", f"/api/templates/{name}")
  )


@mcp.tool()
def list_context() -> str:
  """List context documentation files from context/. These contain architecture decisions, build instructions, VM setup guides, and other reference docs that agents can read for background."""
  return json.dumps(_api("GET", "/api/context"))


@mcp.tool()
def get_context(name: str) -> str:
  """Read a context documentation file. Key files: architecture.md (system design), building-repos.md (how to build the C++ repos), vm-templates.md (VM cloning), workstation-setup.md (new machine setup).

  Args:
    name: Context filename (e.g. architecture.md).
  """
  return json.dumps(
    _api("GET", f"/api/context/{name}")
  )


# -- Repo and agent tools --

@mcp.tool()
def list_repos() -> str:
  """List repos registered in config/repos.yaml with their GitHub org/name and push order. Push order determines the sequence for pushing branches to GitHub (lower = first, for dependency ordering)."""
  return json.dumps(_api("GET", "/api/repos"))


@mcp.tool()
def list_agents() -> str:
  """List agents currently running in pipeline steps. Shows each agent's workspace, step, status, and PID."""
  return json.dumps(_api("GET", "/api/agents"))


@mcp.tool()
def search(
  query: str,
  scope_kind: str = "",
  scope_value: str = "",
  kind: str = "",
  limit: int = 10,
) -> str:
  """Search takt's knowledge base and get back the relevant sections, ranked best-first. Covers the context docs, the pipeline role templates, and every fact recorded by other agents.

  This is the tool to reach for before guessing at how something works: how to build a repo, why an architectural decision was made, what a VM needs, or whether another agent already hit the problem you are looking at.

  Each result is one section, with the document path, the heading path, the line range, and a snippet — so you can cite it or open just that part of the file. Results also carry provenance: for recorded facts, which run and workspace produced them.

  Args:
    query: What you want to know. Natural language is fine. Wrap a span in double quotes to require that exact phrase.
    scope_kind: Narrow to a scope: 'repo', 'workspace', 'target', or 'global'. Scoped results rank above general ones, and other repos' facts are excluded. Leave empty to search everything.
    scope_value: The name the scope refers to, e.g. 'OTC.SDK'. Required when scope_kind is set to anything but 'global'.
    kind: 'fact' for only agent-recorded knowledge, 'doc' for only the checked-in documentation. Empty searches both.
    limit: Maximum results (default 10, max 100).
  """
  params = {"q": query, "limit": str(limit)}
  if scope_kind:
    params["scope_kind"] = scope_kind
  if scope_value:
    params["scope_value"] = scope_value
  if kind:
    params["kind"] = kind
  qs = urllib.parse.urlencode(params)
  return json.dumps(
    _api("GET", f"/api/context/search?{qs}")
  )


@mcp.tool()
def record_fact(
  body: str,
  scope_kind: str = "global",
  scope_value: str = "",
  title: str = "",
  source_run: int = 0,
  source_workspace: str = "",
) -> str:
  """Record something you learned that the next agent would otherwise have to rediscover.

  Worth recording: a build flag that turns out to be required, an API that does not behave as its docs claim, a VM quirk, a test that is flaky for a known reason, a dead end that looked promising. Not worth recording: anything already in the repo or its history, or anything true only of your current run.

  Scope it as narrowly as it is actually true. A fact about OTC.SDK recorded as global will surface while someone works on Conveyor, where it is noise or worse.

  Recording the same title again updates that fact in place rather than duplicating it, so correcting yourself is cheap. If a fact turns out to be wrong in a way worth remembering, record the replacement and call supersede_fact.

  Args:
    body: The fact, as markdown. Start with '# A short title' and then explain it, including what you observed that makes you believe it.
    scope_kind: 'global', 'repo', 'workspace' or 'target'.
    scope_value: What the scope names, e.g. 'OTC.SDK'. Required unless scope_kind is 'global'.
    title: Short summary. Derived from the body's first heading if omitted.
    source_run: Pipeline run id this came from, if any.
    source_workspace: Workspace you were working in.
  """
  payload = {
    "body": body,
    "scope_kind": scope_kind,
    "scope_value": scope_value,
  }
  if title:
    payload["title"] = title
  if source_run:
    payload["source_run"] = source_run
  if source_workspace:
    payload["source_workspace"] = source_workspace
  return json.dumps(
    _api("POST", "/api/context/facts", payload)
  )


@mcp.tool()
def list_facts(
  scope_kind: str = "",
  scope_value: str = "",
  limit: int = 50,
) -> str:
  """List recorded facts, most recently updated first. Use this to see what is already known about a repo or workspace before starting work; use search() when you have a specific question.

  Args:
    scope_kind: Filter by scope: 'global', 'repo', 'workspace' or 'target'.
    scope_value: The scope name, e.g. 'OTC.SDK'.
    limit: Maximum facts to return.
  """
  params = {"kind": "fact", "limit": str(limit)}
  if scope_kind:
    params["scope_kind"] = scope_kind
  if scope_value:
    params["scope_value"] = scope_value
  qs = urllib.parse.urlencode(params)
  return json.dumps(
    _api("GET", f"/api/context/docs?{qs}")
  )


@mcp.tool()
def supersede_fact(path: str, by_path: str) -> str:
  """Mark a recorded fact as replaced by a newer one. The old fact stops appearing in search but stays readable, so the record of what was once believed survives.

  Args:
    path: Path of the fact being retired, e.g. 'facts/otc-sdk/ffmpeg-arm64.md'. Shown as 'path' in search and list_facts results.
    by_path: Path of the fact that replaces it.
  """
  return json.dumps(
    _api("POST", "/api/context/supersede",
         {"path": path, "by_path": by_path})
  )


@mcp.tool()
def context_stats() -> str:
  """Show the size and shape of takt's knowledge base: how many documents and facts are indexed, how many sections, the estimated token total, and the breakdown by scope."""
  return json.dumps(_api("GET", "/api/context/stats"))


@mcp.tool()
def reindex_context() -> str:
  """Re-scan context/ and templates/ and update the search index. The service does this at startup and on every edit made through takt, so you only need this after editing those files directly on disk."""
  return json.dumps(
    _api("POST", "/api/context/reindex", {})
  )


# -- Account tools --

@mcp.tool()
def get_usage() -> str:
  """Get token usage stats for the active Claude account. Shows input/output tokens consumed across pipeline runs."""
  return json.dumps(_api("GET", "/api/agent/usage"))


@mcp.tool()
def list_accounts() -> str:
  """List configured Claude API accounts from config/takt.yaml. Each account has a label, config directory, and rate limit tier."""
  return json.dumps(_api("GET", "/api/agent/accounts"))


@mcp.tool()
def set_active_account(account: str) -> str:
  """Switch the active Claude account used for pipeline agent runs.

  Args:
    account: Account name (e.g. default).
  """
  return json.dumps(
    _api("POST", "/api/agent/accounts/active", {
      "account": account,
    })
  )


@mcp.tool()
def workspace_health(name: str) -> str:
  """Full health report for a workspace. Checks how many commits behind master each repo is, scans for secrets in the diff, reports total diff size, and includes the last pipeline run result. Use this to assess whether a workspace is ready to push or needs attention.

  Args:
    name: Workspace name.
  """
  from lib.checks import workspace_health as _health
  ws_path = WORKSPACES_DIR / name
  if not ws_path.exists():
    return json.dumps({
      "error": f"Workspace '{name}' not found."
    })
  result = _health(str(ws_path))
  result["workspace"] = name
  return json.dumps(result, default=str)


@mcp.tool()
def workspace_last_run(name: str) -> str:
  """Read the last pipeline run result for a workspace. Returns overall status (pass/fail), per-step summaries, and error tails. Useful for checking if the last build/test cycle passed before pushing.

  Args:
    name: Workspace name.
  """
  result_path = (
    WORKSPACES_DIR / name / ".takt" / "last-run.json"
  )
  if not result_path.exists():
    return json.dumps({
      "error": f"No run results for '{name}'."
    })
  try:
    return result_path.read_text()
  except Exception as e:
    return json.dumps({"error": str(e)})


@mcp.tool()
def list_issues(
  status: str = "",
  workspace: str = "",
  tracker: str = "",
) -> str:
  """List issues tracked in the takt ledger. Shows which issues are assigned to which workspaces and their current status. Filter by status (open, assigned, in_progress, review, merged, closed, blocked, wont_fix), workspace name, or tracker (e.g. github:Optris/OTC.SDK).

  Args:
    status: Filter by status (empty = all).
    workspace: Filter by workspace name (empty = all).
    tracker: Filter by tracker string (empty = all).
  """
  params = {}
  if status:
    params["status"] = status
  if workspace:
    params["workspace"] = workspace
  if tracker:
    params["tracker"] = tracker
  qs = "&".join(f"{k}={v}" for k, v in params.items())
  url = f"/api/issues?{qs}" if qs else "/api/issues"
  return json.dumps(_api("GET", url))


@mcp.tool()
def assign_issue(
  tracker: str,
  issue_id: str,
  workspace: str,
  summary: str = "",
) -> str:
  """Assign an issue to a workspace. Atomic — fails with 409 if already assigned to a different workspace. Use this before starting work so other agents can see the issue is taken. Tracker format: github:Org/Repo or gitlab:group/project.

  Args:
    tracker: Issue tracker (e.g. github:Optris/OTC.SDK).
    issue_id: Issue number or ID.
    workspace: Workspace to assign to.
    summary: One-line description of the issue.
  """
  return json.dumps(
    _api("POST", "/api/issues/assign", {
      "tracker": tracker,
      "issue_id": issue_id,
      "workspace": workspace,
      "summary": summary,
    })
  )


@mcp.tool()
def unassign_issue(
  tracker: str, issue_id: str,
) -> str:
  """Remove a workspace assignment from an issue, setting it back to open.

  Args:
    tracker: Issue tracker.
    issue_id: Issue number or ID.
  """
  return json.dumps(
    _api("POST", "/api/issues/unassign", {
      "tracker": tracker,
      "issue_id": issue_id,
    })
  )


@mcp.tool()
def update_issue_status(
  tracker: str,
  issue_id: str,
  status: str,
) -> str:
  """Transition an issue to a new status. Valid transitions: open -> assigned/wont_fix, assigned -> in_progress/open/wont_fix, in_progress -> review/open/blocked/wont_fix, blocked -> in_progress/open/wont_fix, review -> merged/in_progress, merged -> closed/in_progress, closed -> open, wont_fix -> open.

  Args:
    tracker: Issue tracker.
    issue_id: Issue number or ID.
    status: New status.
  """
  return json.dumps(
    _api("POST", "/api/issues/status", {
      "tracker": tracker,
      "issue_id": issue_id,
      "status": status,
    })
  )


if __name__ == "__main__":
  mcp.run()
