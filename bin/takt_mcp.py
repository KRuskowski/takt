#!/usr/bin/env python3
"""takt MCP server.

Exposes takt operations as MCP tools so Claude agents can
interact with takt programmatically. Uses stdio transport.

Direct lib/ imports — no HTTP hop, no sidecar. Works even
when takt-service is down for read-only operations.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from mcp.server.fastmcp import FastMCP

from lib import db
from lib.config import (
  CONFIG_DIR,
  TEMPLATES_DIR,
  WORKSPACES_DIR,
  load_repos_config,
  load_takt_config,
  load_targets_config,
)
from lib.ssh_utils import SSHError, run_ssh
from lib.target_ops import (
  get_all_targets,
  get_target,
  get_vm_state,
  is_template,
  read_lock,
  release_lock,
  write_lock,
)
from lib.workspace_ops import (
  create_workspace,
  delete_workspace,
  get_workspace_status,
  list_workspaces,
)

mcp = FastMCP("takt")


# -- Workspace tools --

@mcp.tool()
def workspace_list() -> str:
  """List all workspaces with their repos and branches."""
  workspaces = list_workspaces()
  return json.dumps(workspaces, default=str)


@mcp.tool()
def workspace_create(name: str, repos: list[str]) -> str:
  """Create a new workspace with local repo clones.

  Args:
    name: Workspace name (becomes the branch name).
    repos: List of repo names to clone.
  """
  try:
    path = create_workspace(name, repos)
    return json.dumps({
      "status": "created",
      "name": name,
      "path": str(path),
    })
  except FileExistsError:
    return json.dumps({
      "error": f"Workspace '{name}' already exists."
    })
  except (ValueError, Exception) as e:
    return json.dumps({"error": str(e)})


@mcp.tool()
def workspace_delete(name: str) -> str:
  """Delete a workspace and all its repo clones.

  Args:
    name: Workspace name.
  """
  try:
    delete_workspace(name)
    return json.dumps({
      "status": "deleted", "name": name,
    })
  except FileNotFoundError:
    return json.dumps({
      "error": f"Workspace '{name}' not found."
    })


@mcp.tool()
def workspace_status(name: str) -> str:
  """Show per-repo branch and git status for a workspace.

  Args:
    name: Workspace name.
  """
  try:
    status = get_workspace_status(name)
    return json.dumps(status)
  except FileNotFoundError:
    return json.dumps({
      "error": f"Workspace '{name}' not found."
    })


# -- Target tools --

@mcp.tool()
def target_list() -> str:
  """List all targets with their type, host, and lock status."""
  try:
    targets = get_all_targets()
  except FileNotFoundError:
    return json.dumps({
      "error": "targets.yaml not found."
    })
  return json.dumps(targets, default=str)


@mcp.tool()
def target_claim(name: str, workspace: str) -> str:
  """Claim a target for exclusive use by a workspace.

  Args:
    name: Target name.
    workspace: Workspace claiming the target.
  """
  try:
    target = get_target(name)
  except FileNotFoundError:
    return json.dumps({
      "error": "targets.yaml not found."
    })
  if target is None:
    return json.dumps({
      "error": f"Target '{name}' not found."
    })
  if is_template(name):
    return json.dumps({
      "error": f"'{name}' is a template. "
               f"Clone it first."
    })
  existing = read_lock(name)
  if existing:
    return json.dumps({
      "error": f"Target '{name}' already claimed by "
               f"workspace '{existing['workspace']}'."
    })
  write_lock(name, workspace)
  return json.dumps({
    "status": "claimed",
    "target": name,
    "workspace": workspace,
  })


@mcp.tool()
def target_release(name: str) -> str:
  """Release a previously claimed target.

  Args:
    name: Target name.
  """
  prev = release_lock(name)
  if prev is None:
    return json.dumps({
      "error": f"Target '{name}' is not claimed."
    })
  return json.dumps({
    "status": "released",
    "target": name,
    "was_workspace": prev.get("workspace"),
  })


@mcp.tool()
def target_up(name: str) -> str:
  """Start a VM target.

  Args:
    name: Target name.
  """
  try:
    target = get_target(name)
  except FileNotFoundError:
    return json.dumps({
      "error": "targets.yaml not found."
    })
  if target is None:
    return json.dumps({
      "error": f"Target '{name}' not found."
    })
  if is_template(name):
    return json.dumps({
      "error": f"'{name}' is a template."
    })
  if target.get("type") == "hardware":
    return json.dumps({
      "status": "ok",
      "message": f"'{name}' is hardware — always on.",
    })
  if not shutil.which("virsh"):
    return json.dumps({
      "error": "virsh not installed."
    })
  result = subprocess.run(
    ["virsh", "start", name],
    capture_output=True, text=True,
  )
  if result.returncode == 0:
    return json.dumps({
      "status": "started", "target": name,
    })
  return json.dumps({
    "error": f"Failed to start '{name}': "
             f"{result.stderr.strip()}"
  })


@mcp.tool()
def target_down(name: str) -> str:
  """Stop a VM target.

  Args:
    name: Target name.
  """
  try:
    target = get_target(name)
  except FileNotFoundError:
    return json.dumps({
      "error": "targets.yaml not found."
    })
  if target is None:
    return json.dumps({
      "error": f"Target '{name}' not found."
    })
  if target.get("type") == "hardware":
    return json.dumps({
      "error": f"'{name}' is hardware — cannot shut down."
    })
  if not shutil.which("virsh"):
    return json.dumps({
      "error": "virsh not installed."
    })
  result = subprocess.run(
    ["virsh", "shutdown", name],
    capture_output=True, text=True,
  )
  if result.returncode == 0:
    return json.dumps({
      "status": "shutting_down", "target": name,
    })
  return json.dumps({
    "error": f"Failed to stop '{name}': "
             f"{result.stderr.strip()}"
  })


@mcp.tool()
def target_run(name: str, command: str) -> str:
  """Run a command on a target via SSH.

  Args:
    name: Target name.
    command: Shell command to execute.
  """
  try:
    target = get_target(name)
  except FileNotFoundError:
    return json.dumps({
      "error": "targets.yaml not found."
    })
  if target is None:
    return json.dumps({
      "error": f"Target '{name}' not found."
    })
  if is_template(name):
    return json.dumps({
      "error": f"'{name}' is a template."
    })
  host = target.get("host")
  if not host:
    return json.dumps({
      "error": f"No host configured for '{name}'."
    })
  user = target.get("user")
  port = target.get("port")
  key = target.get("ssh_key")
  if key:
    key = str(Path(key).expanduser())
  try:
    output = run_ssh(
      host, command, user=user, port=port, key=key,
    )
    return json.dumps({
      "status": "ok",
      "target": name,
      "output": output,
    })
  except SSHError as e:
    return json.dumps({
      "error": f"SSH error on '{name}': {e}"
    })


@mcp.tool()
def target_status(name: str) -> str:
  """Show target details, lock status, and connectivity.

  Args:
    name: Target name.
  """
  try:
    target = get_target(name)
  except FileNotFoundError:
    return json.dumps({
      "error": "targets.yaml not found."
    })
  if target is None:
    return json.dumps({
      "error": f"Target '{name}' not found."
    })
  lock = read_lock(name)
  info = {
    "name": name,
    "type": target.get("type"),
    "host": target.get("host"),
    "user": target.get("user"),
    "template": target.get("template", False),
    "description": target.get("description", ""),
    "lock": lock,
  }
  if target.get("type") == "vm":
    info["vm_state"] = get_vm_state(name)
  return json.dumps(info, default=str)


# -- Pipeline tools --

@mcp.tool()
def pipeline_show(workspace: str) -> str:
  """Show the pipeline step configuration for a workspace.

  Args:
    workspace: Workspace name.
  """
  db.migrate()
  steps = db.get_pipeline(workspace)
  return json.dumps(steps, default=str)


@mcp.tool()
def pipeline_set(
  workspace: str, steps: list[str],
) -> str:
  """Set the pipeline steps for a workspace.

  Args:
    workspace: Workspace name.
    steps: List of step names (role slugs or scripts).
  """
  db.migrate()
  step_defs = []
  for name in steps:
    step_defs.append({
      "name": name,
      "step_type": "agent",
      "config": {},
      "timeout_secs": 1800,
    })
  db.define_pipeline(workspace, step_defs)
  return json.dumps({
    "status": "ok",
    "workspace": workspace,
    "steps": steps,
  })


@mcp.tool()
def pipeline_runs(
  workspace: str, limit: int = 20,
) -> str:
  """List pipeline runs for a workspace.

  Args:
    workspace: Workspace name.
    limit: Max number of runs to return.
  """
  db.migrate()
  runs = db.list_runs(workspace=workspace, limit=limit)
  return json.dumps(runs, default=str)


# -- Run tools --

@mcp.tool()
def run_get(run_id: int) -> str:
  """Get details for a pipeline run including its steps.

  Args:
    run_id: Run ID.
  """
  db.migrate()
  run = db.get_run(run_id)
  if run is None:
    return json.dumps({
      "error": f"Run {run_id} not found."
    })
  steps = db.get_run_steps(run_id)
  return json.dumps({
    "run": run, "steps": steps,
  }, default=str)


@mcp.tool()
def run_step_output(
  step_id: int, from_line: int = 0,
) -> str:
  """Get agent output lines for a pipeline step.

  Args:
    step_id: Step ID.
    from_line: Start from this line number.
  """
  db.migrate()
  lines = db.get_output(step_id, from_line=from_line)
  return json.dumps(lines, default=str)


@mcp.tool()
def run_trigger(workspace: str) -> str:
  """Trigger a manual pipeline run for a workspace.

  Args:
    workspace: Workspace name.
  """
  ws_dir = WORKSPACES_DIR / workspace
  if not ws_dir.is_dir():
    return json.dumps({
      "error": f"Workspace '{workspace}' not found."
    })
  db.migrate()
  try:
    status = get_workspace_status(workspace)
  except FileNotFoundError:
    return json.dumps({
      "error": f"Workspace '{workspace}' not found."
    })
  repos = [s["repo"] for s in status]
  refs = {}
  for s in status:
    repo_dir = ws_dir / s["repo"]
    try:
      head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True, text=True,
        cwd=str(repo_dir),
      )
      if head.returncode == 0:
        refs[s["repo"]] = head.stdout.strip()
    except Exception:
      pass
  run_id = db.create_run(
    workspace, "manual", repos,
    {f"{r}:{workspace}": h for r, h in refs.items()},
  )
  return json.dumps({
    "status": "triggered",
    "run_id": run_id,
    "workspace": workspace,
  })


@mcp.tool()
def run_cancel(run_id: int) -> str:
  """Cancel a running pipeline run.

  Args:
    run_id: Run ID to cancel.
  """
  db.migrate()
  run = db.get_run(run_id)
  if run is None:
    return json.dumps({
      "error": f"Run {run_id} not found."
    })
  if run.get("status") not in ("queued", "running"):
    return json.dumps({
      "error": f"Run {run_id} is '{run.get('status')}' "
               f"— cannot cancel."
    })
  steps = db.get_run_steps(run_id)
  for step in steps:
    if step.get("status") in ("queued", "running",
                               "pending"):
      db.advance_step(
        step["id"], "cancelled", reason="manual cancel",
      )
  db.advance_run(run_id)
  return json.dumps({
    "status": "cancelled", "run_id": run_id,
  })


# -- Push tool --

@mcp.tool()
def push_to_github(
  branch: str,
  repos: list[str] | None = None,
  dry_run: bool = False,
) -> str:
  """Push a branch from root repos to GitHub.

  Args:
    branch: Branch name to push.
    repos: Specific repos to push (default: all).
    dry_run: If true, only show what would be pushed.
  """
  cmd = [
    sys.executable,
    str(PROJECT_DIR / "bin" / "push_to_github.py"),
    branch,
  ]
  if repos:
    cmd += ["--repos"] + repos
  if dry_run:
    cmd.append("--dry-run")
  cmd.append("--yes")
  result = subprocess.run(
    cmd, capture_output=True, text=True,
    cwd=str(PROJECT_DIR),
  )
  return json.dumps({
    "status": "ok" if result.returncode == 0 else "error",
    "output": result.stdout,
    "errors": result.stderr,
  })


# -- Service tools --

@mcp.tool()
def service_status() -> str:
  """Check if takt-service is running."""
  result = subprocess.run(
    ["systemctl", "--user", "is-active", "takt-service"],
    capture_output=True, text=True,
  )
  active = result.stdout.strip()
  return json.dumps({
    "active": active == "active",
    "state": active,
  })


@mcp.tool()
def service_start() -> str:
  """Start takt-service via systemd."""
  result = subprocess.run(
    ["systemctl", "--user", "start", "takt-service"],
    capture_output=True, text=True,
  )
  if result.returncode == 0:
    return json.dumps({"status": "started"})
  return json.dumps({
    "error": result.stderr.strip(),
  })


@mcp.tool()
def service_stop() -> str:
  """Stop takt-service via systemd."""
  result = subprocess.run(
    ["systemctl", "--user", "stop", "takt-service"],
    capture_output=True, text=True,
  )
  if result.returncode == 0:
    return json.dumps({"status": "stopped"})
  return json.dumps({
    "error": result.stderr.strip(),
  })


# -- Config tools --

@mcp.tool()
def repos_list() -> str:
  """List configured repos from repos.yaml."""
  try:
    config = load_repos_config()
    return json.dumps(config, default=str)
  except FileNotFoundError:
    return json.dumps({
      "error": "repos.yaml not found."
    })


@mcp.tool()
def template_list() -> str:
  """List available pipeline templates."""
  templates_path = TEMPLATES_DIR
  if not templates_path.is_dir():
    return json.dumps([])
  files = sorted(templates_path.glob("*.md"))
  return json.dumps([f.stem for f in files])


@mcp.tool()
def template_read(name: str) -> str:
  """Read a pipeline template file.

  Args:
    name: Template name (without .md extension).
  """
  path = TEMPLATES_DIR / f"{name}.md"
  if not path.is_file():
    return json.dumps({
      "error": f"Template '{name}' not found."
    })
  return path.read_text()


@mcp.tool()
def template_write(name: str, content: str) -> str:
  """Write or update a pipeline template file.

  Args:
    name: Template name (without .md extension).
    content: Template content (markdown).
  """
  path = TEMPLATES_DIR / f"{name}.md"
  path.write_text(content)
  return json.dumps({
    "status": "written",
    "template": name,
    "path": str(path),
  })


@mcp.tool()
def workspace_claude_md_read(workspace: str) -> str:
  """Read the CLAUDE.md file for a workspace.

  Args:
    workspace: Workspace name.
  """
  path = WORKSPACES_DIR / workspace / "CLAUDE.md"
  if not path.is_file():
    return json.dumps({
      "error": f"No CLAUDE.md for workspace "
               f"'{workspace}'."
    })
  return path.read_text()


@mcp.tool()
def workspace_claude_md_write(
  workspace: str, content: str,
) -> str:
  """Write or update the CLAUDE.md file for a workspace.

  Args:
    workspace: Workspace name.
    content: CLAUDE.md content (markdown).
  """
  ws_dir = WORKSPACES_DIR / workspace
  if not ws_dir.is_dir():
    return json.dumps({
      "error": f"Workspace '{workspace}' not found."
    })
  path = ws_dir / "CLAUDE.md"
  path.write_text(content)
  return json.dumps({
    "status": "written",
    "workspace": workspace,
    "path": str(path),
  })


if __name__ == "__main__":
  mcp.run(transport="stdio")
