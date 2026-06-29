#!/usr/bin/env python3
"""MCP server exposing takt tools to Claude CLI sessions.

Wraps the takt REST API (default http://127.0.0.1:7433) as MCP
tools so agents running in workspaces can query pipeline state,
manage targets, trigger runs, etc.
"""

import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from mcp.server.fastmcp import FastMCP

WORKSPACES_DIR = PROJECT_DIR.parent / "workspaces"

TAKT_API = os.environ.get(
  "TAKT_API_URL", "http://127.0.0.1:7433"
)

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
    "~/dev/workspaces/<name>/."
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
  req = urllib.request.Request(
    url, data=data, method=method,
    headers={"Content-Type": "application/json"},
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
  name: str, repos: list[str],
) -> str:
  """Create a new workspace by cloning repos from ~/dev/root/ into ~/dev/workspaces/<name>/. Each clone gets a branch named after the workspace. Use list_repos to see available repos.

  Args:
    name: Workspace name (becomes the branch name).
    repos: List of repo names to clone.
  """
  return json.dumps(
    _api("POST", "/api/workspaces", {
      "name": name, "repos": repos,
    })
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
def search(query: str) -> str:
  """Full-text search across all template and context files. Returns matching filenames and line numbers. Useful for finding build instructions, API docs, or architecture decisions.

  Args:
    query: Search term.
  """
  encoded = urllib.request.quote(query)
  return json.dumps(
    _api("GET", f"/api/search?q={encoded}")
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


if __name__ == "__main__":
  mcp.run()
