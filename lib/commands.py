"""Shared command dispatch for the takt CLI.

Each handler wraps existing lib functions and returns a
CommandResult. Used by bin/takt.py (CLI) and lib/api.py
(REST bridge).
"""

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any

from lib import db
from lib.config import (
  load_repos_config,
  parse_pipeline_roles,
)
from lib.git_utils import (
  GitError,
  get_branches,
  push_branch,
)
from lib.pipeline import SCRIPT_REGISTRY
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


@dataclass
class CommandResult:
  """Result of a command dispatch."""
  ok: bool
  output: str = ""
  data: Any = field(default_factory=dict)


# -- Workspace commands --

def ws_list(names_only=False, **_kw):
  """List all workspaces."""
  workspaces = list_workspaces()
  if names_only:
    names = [w["name"] for w in workspaces]
    return CommandResult(True, "\n".join(names), names)
  if not workspaces:
    return CommandResult(True, "No workspaces found.")
  lines = []
  lines.append(
    f"{'Workspace':<25} {'Repos':<40} {'Branch'}"
  )
  lines.append("-" * 80)
  for ws in workspaces:
    repos_str = (
      ", ".join(ws["repos"]) if ws["repos"]
      else "(empty)"
    )
    lines.append(
      f"{ws['name']:<25} {repos_str:<40} "
      f"{ws['branch']}"
    )
  return CommandResult(True, "\n".join(lines), workspaces)


def ws_create(name, repos, **_kw):
  """Create a new workspace."""
  try:
    ws_dir = create_workspace(name, repos)
  except (FileExistsError, ValueError, GitError) as e:
    return CommandResult(False, f"Error: {e}")
  msg = f"Workspace created: {ws_dir}\nBranch: {name}"
  return CommandResult(True, msg, {"path": str(ws_dir)})


def ws_delete(name, **_kw):
  """Delete a workspace."""
  try:
    delete_workspace(name)
  except FileNotFoundError as e:
    return CommandResult(False, f"Error: {e}")
  return CommandResult(True, f"Deleted workspace '{name}'.")


def ws_status(name, **_kw):
  """Show repo status in a workspace."""
  try:
    statuses = get_workspace_status(name)
  except FileNotFoundError as e:
    return CommandResult(False, f"Error: {e}")
  if not statuses:
    return CommandResult(
      True, f"Workspace '{name}' has no repos."
    )
  lines = [f"Workspace: {name}"]
  lines.append(
    f"{'Repo':<30} {'Branch':<25} {'Status'}"
  )
  lines.append("-" * 80)
  for s in statuses:
    lines.append(
      f"{s['repo']:<30} {s['branch']:<25} {s['status']}"
    )
  return CommandResult(True, "\n".join(lines), statuses)


# -- Target commands --

def target_list(names_only=False, **_kw):
  """List all targets."""
  targets = get_all_targets()
  if names_only:
    names = [t["name"] for t in targets]
    return CommandResult(True, "\n".join(names), names)
  if not targets:
    return CommandResult(
      True,
      "No targets configured.\n"
      "Edit config/targets.yaml to add targets.",
    )
  lines = []
  lines.append(
    f"{'Name':<15} {'Type':<10} {'Host':<20} "
    f"{'State':<12} {'Claimed By':<20} {'Description'}"
  )
  lines.append("-" * 80)
  for t in targets:
    lock = t["lock"]
    claimed = lock["workspace"] if lock else "-"
    tag = " [template]" if t.get("template") else ""
    if t["type"] == "vm":
      state = get_vm_state(t["name"]) or "?"
    else:
      state = "on"
    lines.append(
      f"{t['name']:<15} {t['type']:<10} "
      f"{t['host']:<20} {state:<12} {claimed:<20} "
      f"{t['description']}{tag}"
    )
  return CommandResult(True, "\n".join(lines), targets)


def target_claim(name, workspace, **_kw):
  """Claim a target for a workspace."""
  target = get_target(name)
  if target is None:
    return CommandResult(
      False, f"Error: target '{name}' not found."
    )
  if target.get("template"):
    return CommandResult(
      False,
      f"Error: '{name}' is a template. "
      f"Use bin/clone_vm.py to create a clone.",
    )
  lock = read_lock(name)
  if lock:
    return CommandResult(
      False,
      f"Error: target '{name}' already claimed by "
      f"'{lock['workspace']}' at {lock['claimed_at']}.",
    )
  write_lock(name, workspace)
  return CommandResult(
    True,
    f"Claimed '{name}' for workspace '{workspace}'.",
  )


def target_release(name, **_kw):
  """Release a target."""
  lock = release_lock(name)
  if lock is None:
    return CommandResult(
      True, f"Target '{name}' is not claimed."
    )
  ws = lock.get("workspace", "unknown")
  return CommandResult(
    True,
    f"Released '{name}' (was claimed by '{ws}').",
  )


def target_up(name, **_kw):
  """Start a VM target."""
  import shutil
  target = get_target(name)
  if target is None:
    return CommandResult(
      False, f"Error: target '{name}' not found."
    )
  if target.get("template"):
    return CommandResult(
      False,
      f"Error: '{name}' is a template.",
    )
  if target.get("type") == "hardware":
    return CommandResult(
      True, f"Target '{name}' is hardware — always on."
    )
  if not shutil.which("virsh"):
    return CommandResult(
      False,
      f"Warning: virsh not installed. "
      f"Cannot start VM '{name}'.",
    )
  result = subprocess.run(
    ["virsh", "start", name],
    capture_output=True, text=True,
  )
  if result.returncode == 0:
    return CommandResult(
      True, f"Started VM '{name}'."
    )
  return CommandResult(
    False,
    f"Failed to start VM '{name}': "
    f"{result.stderr.strip()}",
  )


def target_down(name, **_kw):
  """Stop a VM target."""
  import shutil
  target = get_target(name)
  if target is None:
    return CommandResult(
      False, f"Error: target '{name}' not found."
    )
  if target.get("type") == "hardware":
    return CommandResult(
      False,
      f"Target '{name}' is hardware — cannot shut down.",
    )
  if not shutil.which("virsh"):
    return CommandResult(
      False,
      f"Warning: virsh not installed. "
      f"Cannot stop VM '{name}'.",
    )
  result = subprocess.run(
    ["virsh", "shutdown", name],
    capture_output=True, text=True,
  )
  if result.returncode == 0:
    return CommandResult(
      True, f"Shutting down VM '{name}'."
    )
  return CommandResult(
    False,
    f"Failed to stop VM '{name}': "
    f"{result.stderr.strip()}",
  )


def target_run(name, command, **_kw):
  """Run a command on a target via SSH."""
  from pathlib import Path
  from lib.ssh_utils import SSHError, run_ssh
  target = get_target(name)
  if target is None:
    return CommandResult(
      False, f"Error: target '{name}' not found."
    )
  if target.get("template"):
    return CommandResult(
      False, f"Error: '{name}' is a template.",
    )
  host = target.get("host")
  if not host:
    return CommandResult(
      False,
      f"Error: no host configured for '{name}'.",
    )
  user = target.get("user")
  port = target.get("port")
  key = target.get("ssh_key")
  if key:
    key = str(Path(key).expanduser())
  try:
    output = run_ssh(
      host, command, user=user, port=port, key=key,
    )
    return CommandResult(True, output or "")
  except SSHError as e:
    return CommandResult(False, f"SSH error: {e}")


def target_status(name, **_kw):
  """Show target details and connectivity."""
  from lib.ssh_utils import check_connectivity
  from pathlib import Path
  target = get_target(name)
  if target is None:
    return CommandResult(
      False, f"Error: target '{name}' not found."
    )
  lock = read_lock(name)
  lines = [f"Target: {name}"]
  lines.append(f"  Type: {target.get('type', '?')}")
  if target.get("template"):
    lines.append("  Template: yes")
  if target.get("type") == "vm":
    state = get_vm_state(name) or "?"
    lines.append(f"  State: {state}")
  lines.append(f"  Host: {target.get('host', '?')}")
  lines.append(f"  User: {target.get('user', '?')}")
  lines.append(
    f"  Description: {target.get('description', '')}"
  )
  if lock:
    lines.append(f"  Claimed by: {lock['workspace']}")
    lines.append(f"  Claimed at: {lock['claimed_at']}")
  else:
    lines.append("  Claimed by: (none)")
  host = target.get("host")
  if host:
    key = target.get("ssh_key")
    if key:
      key = str(Path(key).expanduser())
    reachable = check_connectivity(
      host, user=target.get("user"),
      port=target.get("port"), key=key,
    )
    lines.append(
      f"  Connectivity: {'OK' if reachable else 'UNREACHABLE'}"
    )
  data = {"target": target, "lock": lock}
  return CommandResult(True, "\n".join(lines), data)


# -- Pipeline commands --

def pipeline_set(workspace, steps, **_kw):
  """Define pipeline steps for a workspace."""
  db.migrate()
  roles = parse_pipeline_roles()
  parsed = []
  for name in steps:
    if name in SCRIPT_REGISTRY:
      parsed.append({"name": name, "step_type": "script"})
    elif name in roles:
      parsed.append({"name": name, "step_type": "agent"})
    else:
      avail = sorted(set(roles) | set(SCRIPT_REGISTRY))
      return CommandResult(
        False,
        f"Error: unknown step '{name}'. "
        f"Available: {', '.join(avail)}",
      )
  db.define_pipeline(workspace, parsed)
  lines = [f"Pipeline set for '{workspace}':"]
  for i, s in enumerate(parsed):
    lines.append(
      f"  {i}: {s['name']} ({s['step_type']})"
    )
  return CommandResult(True, "\n".join(lines), parsed)


def pipeline_show(workspace, **_kw):
  """Show configured pipeline steps."""
  db.migrate()
  steps = db.get_pipeline(workspace)
  if not steps:
    return CommandResult(
      True,
      f"No pipeline defined for '{workspace}'.",
    )
  lines = [f"Pipeline for '{workspace}':"]
  lines.append(
    f"{'Seq':<5} {'Name':<20} {'Type':<10} {'Timeout'}"
  )
  lines.append("-" * 50)
  for s in steps:
    lines.append(
      f"{s['seq']:<5} {s['name']:<20} "
      f"{s['step_type']:<10} {s['timeout_secs']}s"
    )
  return CommandResult(True, "\n".join(lines), steps)


def pipeline_runs(workspace, limit=20, **_kw):
  """Show pipeline run history."""
  db.migrate()
  runs = db.list_runs(workspace, limit=limit)
  if not runs:
    return CommandResult(
      True,
      f"No pipeline runs for '{workspace}'.",
    )
  lines = [f"Pipeline runs for '{workspace}':"]
  lines.append(
    f"{'ID':<6} {'Created':<26} {'Status':<10} "
    f"{'Trigger':<8} Repos"
  )
  lines.append("-" * 70)
  for run in runs:
    repos = json.loads(run.get("repos_json", "[]"))
    repos_str = ", ".join(repos) if repos else "-"
    lines.append(
      f"{run['id']:<6} {run['created_at']:<26} "
      f"{run['status']:<10} {run['trigger']:<8} "
      f"{repos_str}"
    )
  return CommandResult(True, "\n".join(lines), runs)


# -- Push commands --

def push(branch, repos=None, dry_run=False, yes=False,
         **_kw):
  """Push branches from root repos to GitHub."""
  from lib.config import get_repo_path, validate_repo
  repos_config = load_repos_config()
  all_repos = repos_config.get("repos", {})
  found = []
  for repo_name, cfg in all_repos.items():
    if repos and repo_name not in repos:
      continue
    repo_path = get_repo_path(cfg.get("path", repo_name))
    if not validate_repo(cfg.get("path", repo_name)):
      continue
    try:
      branches = get_branches(repo_path)
      if branch in branches:
        found.append((repo_name, cfg))
    except GitError:
      continue
  found.sort(key=lambda x: x[1].get("push_order", 999))
  if not found:
    return CommandResult(
      False,
      f"No repos found with branch '{branch}'.",
    )
  lines = [
    f"Branch '{branch}' found in {len(found)} repo(s):"
  ]
  lines.append(
    f"{'Order':<7} {'Repo':<30} {'Description'}"
  )
  lines.append("-" * 60)
  for repo_name, cfg in found:
    order = cfg.get("push_order", "?")
    desc = cfg.get("description", "")
    lines.append(f"{order:<7} {repo_name:<30} {desc}")
  if dry_run:
    lines.append("\n(dry run — nothing pushed)")
    return CommandResult(True, "\n".join(lines))
  # Push in order.
  errors = []
  for repo_name, cfg in found:
    repo_path = get_repo_path(cfg.get("path", repo_name))
    lines.append(f"\nPushing {repo_name}...")
    try:
      push_branch(repo_path, branch)
      lines.append(f"  {repo_name}: OK")
    except GitError as e:
      lines.append(f"  {repo_name}: FAILED — {e}")
      errors.append(repo_name)
  if errors:
    lines.append(
      f"\nFailed to push: {', '.join(errors)}"
    )
    return CommandResult(False, "\n".join(lines))
  lines.append("\nAll repos pushed successfully.")
  return CommandResult(True, "\n".join(lines))


# -- Service commands --

def service_start(**_kw):
  """Start takt-service via systemctl."""
  result = subprocess.run(
    ["systemctl", "--user", "start", "takt-service"],
    capture_output=True, text=True,
  )
  if result.returncode == 0:
    return CommandResult(True, "takt-service started.")
  return CommandResult(
    False, f"Failed: {result.stderr.strip()}"
  )


def service_stop(**_kw):
  """Stop takt-service via systemctl."""
  result = subprocess.run(
    ["systemctl", "--user", "stop", "takt-service"],
    capture_output=True, text=True,
  )
  if result.returncode == 0:
    return CommandResult(True, "takt-service stopped.")
  return CommandResult(
    False, f"Failed: {result.stderr.strip()}"
  )


def service_restart(**_kw):
  """Restart takt-service via systemctl."""
  result = subprocess.run(
    ["systemctl", "--user", "restart", "takt-service"],
    capture_output=True, text=True,
  )
  if result.returncode == 0:
    return CommandResult(True, "takt-service restarted.")
  return CommandResult(
    False, f"Failed: {result.stderr.strip()}"
  )


def service_status(**_kw):
  """Show takt-service status."""
  result = subprocess.run(
    ["systemctl", "--user", "status", "takt-service"],
    capture_output=True, text=True,
  )
  return CommandResult(
    result.returncode == 0, result.stdout.strip(),
  )


# -- Dispatch table --

COMMANDS = {
  "ws": {
    "list": ws_list,
    "create": ws_create,
    "delete": ws_delete,
    "status": ws_status,
  },
  "target": {
    "list": target_list,
    "claim": target_claim,
    "release": target_release,
    "up": target_up,
    "down": target_down,
    "run": target_run,
    "status": target_status,
  },
  "pipeline": {
    "set": pipeline_set,
    "show": pipeline_show,
    "runs": pipeline_runs,
  },
  "push": {
    "push": push,
  },
  "service": {
    "start": service_start,
    "stop": service_stop,
    "restart": service_restart,
    "status": service_status,
  },
}


def dispatch(group, sub, **kwargs):
  """Dispatch a command by group and subcommand.

  Args:
    group: Command group (ws, target, pipeline, ...).
    sub: Subcommand within the group.
    **kwargs: Arguments passed to the handler.

  Returns:
    CommandResult.
  """
  group_cmds = COMMANDS.get(group)
  if group_cmds is None:
    return CommandResult(
      False, f"Unknown command group: {group}"
    )
  handler = group_cmds.get(sub)
  if handler is None:
    avail = ", ".join(sorted(group_cmds))
    return CommandResult(
      False,
      f"Unknown subcommand '{sub}' for '{group}'. "
      f"Available: {avail}",
    )
  return handler(**kwargs)


def completions(group=None):
  """Return completion candidates.

  Args:
    group: If given, return subcommands for that group.
      If None, return top-level groups.

  Returns:
    List of completion strings.
  """
  if group is None:
    return sorted(COMMANDS.keys())
  group_cmds = COMMANDS.get(group)
  if group_cmds is None:
    return []
  return sorted(group_cmds.keys())
