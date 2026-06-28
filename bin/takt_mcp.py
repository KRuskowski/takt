#!/usr/bin/env python3
"""MCP server exposing takt tools to Claude CLI sessions.

Wraps the takt REST API (default http://127.0.0.1:7433) as MCP
tools so agents running in workspaces can query pipeline state,
manage targets, trigger runs, etc.
"""

import json
import os
import urllib.request
import urllib.error

from mcp.server.fastmcp import FastMCP

TAKT_API = os.environ.get(
  "TAKT_API_URL", "http://127.0.0.1:7433"
)

mcp = FastMCP("takt")


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
  """List all takt workspaces."""
  return json.dumps(_api("GET", "/api/workspaces"))


@mcp.tool()
def workspace_status(name: str) -> str:
  """Get repo status for a workspace.

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
  """Create a new workspace with local clones.

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
  """Delete a workspace.

  Args:
    name: Workspace name.
  """
  return json.dumps(
    _api("DELETE", f"/api/workspaces/{name}")
  )


# -- Target tools --

@mcp.tool()
def list_targets() -> str:
  """List all build/test targets (VMs and hardware)."""
  return json.dumps(_api("GET", "/api/targets"))


@mcp.tool()
def claim_target(name: str, workspace: str) -> str:
  """Claim a target for exclusive use by a workspace.

  Args:
    name: Target name (e.g. deb-02).
    workspace: Workspace to claim for.
  """
  return json.dumps(
    _api("POST", f"/api/targets/{name}/claim", {
      "workspace": workspace,
    })
  )


@mcp.tool()
def release_target(name: str) -> str:
  """Release a claimed target.

  Args:
    name: Target name.
  """
  return json.dumps(
    _api("POST", f"/api/targets/{name}/release")
  )


@mcp.tool()
def target_up(name: str) -> str:
  """Start a target VM.

  Args:
    name: Target name.
  """
  return json.dumps(
    _api("POST", f"/api/targets/{name}/up")
  )


@mcp.tool()
def target_down(name: str) -> str:
  """Stop a target VM.

  Args:
    name: Target name.
  """
  return json.dumps(
    _api("POST", f"/api/targets/{name}/down")
  )


# -- Pipeline tools --

@mcp.tool()
def get_pipeline(workspace: str) -> str:
  """Get pipeline steps for a workspace.

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
  """Set pipeline steps for a workspace.

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
  """List pipeline runs.

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
  """Trigger a pipeline run for a workspace.

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
  """Get details of a pipeline run.

  Args:
    run_id: Run ID.
  """
  return json.dumps(
    _api("GET", f"/api/runs/{run_id}")
  )


@mcp.tool()
def cancel_run(run_id: int) -> str:
  """Cancel a running pipeline.

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
  """Get output from a pipeline step.

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
  """List available pipeline role templates."""
  return json.dumps(_api("GET", "/api/templates"))


@mcp.tool()
def get_template(name: str) -> str:
  """Read a template file.

  Args:
    name: Template filename (e.g. workspace_claude.md).
  """
  return json.dumps(
    _api("GET", f"/api/templates/{name}")
  )


@mcp.tool()
def list_context() -> str:
  """List context documentation files."""
  return json.dumps(_api("GET", "/api/context"))


@mcp.tool()
def get_context(name: str) -> str:
  """Read a context documentation file.

  Args:
    name: Context filename (e.g. architecture.md).
  """
  return json.dumps(
    _api("GET", f"/api/context/{name}")
  )


# -- Repo and agent tools --

@mcp.tool()
def list_repos() -> str:
  """List configured repos with push order."""
  return json.dumps(_api("GET", "/api/repos"))


@mcp.tool()
def list_agents() -> str:
  """List running agents."""
  return json.dumps(_api("GET", "/api/agents"))


@mcp.tool()
def search(query: str) -> str:
  """Search context and template files.

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
  """Get token usage stats across accounts."""
  return json.dumps(_api("GET", "/api/agent/usage"))


@mcp.tool()
def list_accounts() -> str:
  """List configured Claude accounts."""
  return json.dumps(_api("GET", "/api/agent/accounts"))


@mcp.tool()
def set_active_account(account: str) -> str:
  """Switch the active Claude account.

  Args:
    account: Account name (e.g. work, private, default).
  """
  return json.dumps(
    _api("POST", "/api/agent/accounts/active", {
      "account": account,
    })
  )


if __name__ == "__main__":
  mcp.run()
