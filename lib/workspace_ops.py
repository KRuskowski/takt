"""Workspace operations for programmatic use.

Extracted from bin/workspace.py so both the CLI and TUI
dashboard can share the same logic.
"""

import shutil
from pathlib import Path
from string import Template

from lib.config import (
  CONTEXT_DIR,
  TEMPLATES_DIR,
  TESTING_DIR,
  WORKSPACES_DIR,
  get_default_branch,
  get_repo_path,
  get_testing_repo_path,
  load_repos_config,
  validate_repo,
)
from lib.git_utils import (
  GitError,
  clone_local,
  create_branch,
  get_current_branch,
  get_status,
  run_git,
  set_receive_update,
)


def list_workspaces():
  """List all workspaces with metadata.

  Returns:
    List of dicts with keys: name, path, repos, branch.
  """
  if not WORKSPACES_DIR.exists():
    return []

  results = []
  for ws_dir in sorted(WORKSPACES_DIR.iterdir()):
    if not ws_dir.is_dir():
      continue
    repos = sorted(
      d.name for d in ws_dir.iterdir()
      if d.is_dir() and (d / ".git").exists()
    )
    branch = "?"
    if repos:
      try:
        branch = get_current_branch(ws_dir / repos[0])
      except GitError:
        pass
    results.append({
      "name": ws_dir.name,
      "path": str(ws_dir),
      "repos": repos,
      "branch": branch,
    })
  return results


def get_workspace_status(name):
  """Get per-repo branch and status for a workspace.

  Args:
    name: Workspace name.

  Returns:
    List of dicts with keys: repo, branch, status.

  Raises:
    FileNotFoundError: If workspace does not exist.
  """
  ws_dir = WORKSPACES_DIR / name
  if not ws_dir.exists():
    raise FileNotFoundError(
      f"Workspace '{name}' not found."
    )

  repos = sorted(
    d.name for d in ws_dir.iterdir()
    if d.is_dir() and (d / ".git").exists()
  )

  results = []
  for repo_name in repos:
    repo_path = ws_dir / repo_name
    try:
      branch = get_current_branch(repo_path)
    except GitError:
      branch = "?"
    try:
      status = get_status(repo_path)
      if not status:
        status_str = "clean"
      elif "\n" in status:
        lines = status.splitlines()
        status_str = f"{len(lines)} changed files"
      else:
        status_str = status
    except GitError:
      status_str = "error"
    results.append({
      "repo": repo_name,
      "branch": branch,
      "status": status_str,
    })
  return results


def _resolve_repo_path(repo_name, repos_config):
  """Resolve a repo key to its filesystem path via repos.yaml.

  Falls back to repo_name if not found in config.
  """
  cfg = repos_config.get(repo_name, {})
  return cfg.get("path", repo_name)


def create_workspace(name, repos):
  """Create a new workspace with local clones.

  Args:
    name: Workspace (= branch) name.
    repos: List of repo names (keys from repos.yaml).

  Returns:
    Path to the created workspace.

  Raises:
    FileExistsError: If workspace already exists.
    ValueError: If any repo is invalid.
    GitError: If cloning or branch creation fails.
  """
  ws_dir = WORKSPACES_DIR / name

  if ws_dir.exists():
    raise FileExistsError(
      f"Workspace '{name}' already exists at {ws_dir}"
    )

  repos_config = load_repos_config().get("repos", {})

  invalid = [
    r for r in repos
    if not validate_repo(_resolve_repo_path(r, repos_config))
  ]
  if invalid:
    raise ValueError(
      f"Invalid git repos: {', '.join(invalid)}"
    )

  ws_dir.mkdir(parents=True)

  try:
    for repo_name in repos:
      disk_path = _resolve_repo_path(repo_name, repos_config)
      source = get_repo_path(disk_path)
      dest = ws_dir / repo_name
      clone_local(source, dest)
      create_branch(dest, name)
  except GitError:
    shutil.rmtree(ws_dir, ignore_errors=True)
    raise

  _generate_workspace_claude_md(
    ws_dir, name, repos, repos_config
  )

  # Copy context packets.
  ctx_dest = ws_dir / "context"
  if CONTEXT_DIR.is_dir():
    shutil.copytree(CONTEXT_DIR, ctx_dest)

  return ws_dir


def delete_workspace(name):
  """Delete a workspace directory.

  Args:
    name: Workspace name.

  Raises:
    FileNotFoundError: If workspace does not exist.
  """
  ws_dir = WORKSPACES_DIR / name
  if not ws_dir.exists():
    raise FileNotFoundError(
      f"Workspace '{name}' not found."
    )
  shutil.rmtree(ws_dir)


def _generate_workspace_claude_md(ws_dir, name, repos,
                                  repos_config):
  """Generate a workspace CLAUDE.md from the template."""
  tmpl_path = TEMPLATES_DIR / "workspace_claude.md"
  if not tmpl_path.exists():
    return

  tmpl = Template(tmpl_path.read_text())

  rows = []
  for repo_name in repos:
    cfg = repos_config.get(repo_name, {})
    repo_path = ws_dir / repo_name
    default_br = cfg.get(
      "default_branch", get_default_branch(repo_path)
    )
    push_order = cfg.get("push_order", "?")
    rows.append(
      f"| {repo_name} | {default_br} | {push_order} |"
    )
  repo_table = "\n".join(rows)

  packets = []
  if CONTEXT_DIR.is_dir():
    for f in sorted(CONTEXT_DIR.iterdir()):
      if f.is_file() and f.suffix == ".md":
        packets.append(f"- `context/{f.name}`")
  context_packets = (
    "\n".join(packets) if packets else "- (none)"
  )

  in_scope = "\n".join(f"- `{r}/`" for r in repos)

  content = tmpl.safe_substitute(
    workspace_name=name,
    role_section="(specify role when launching agent)",
    task_section="(specify task description)",
    acceptance_criteria="(specify acceptance criteria)",
    in_scope_repos=in_scope,
    reference_repos="- (none specified)",
    context_packets=context_packets,
    repo_table=repo_table,
    status="Not started",
  )

  out_path = ws_dir / "CLAUDE.md"
  out_path.write_text(content)


def create_testing_stage(workspace_name):
  """Create a testing stage for an existing workspace.

  Creates non-bare clones of the workspace's repos in
  ~/dev/testing/<workspace>/. Each clone:
  - Clones from root repo (origin = root)
  - Checks out the workspace branch
  - Accepts pushes via receive.denyCurrentBranch=updateInstead
  - Gets a CLAUDE.md with the testing agent role

  The workspace repos' origin is re-pointed to the testing
  stage repos, so the chain becomes:
    workspace -> testing -> root -> GitHub

  Args:
    workspace_name: Name of the workspace to create a stage for.

  Returns:
    Path to the testing stage directory.

  Raises:
    FileNotFoundError: If workspace does not exist.
    FileExistsError: If testing stage already exists.
    GitError: If cloning or branch operations fail.
  """
  ws_dir = WORKSPACES_DIR / workspace_name
  if not ws_dir.exists():
    raise FileNotFoundError(
      f"Workspace '{workspace_name}' not found."
    )

  stage_dir = TESTING_DIR / workspace_name
  if stage_dir.exists():
    raise FileExistsError(
      f"Testing stage '{workspace_name}' already exists "
      f"at {stage_dir}"
    )

  repos_config = load_repos_config().get("repos", {})

  # Find git repos in the workspace.
  ws_repos = sorted(
    d.name for d in ws_dir.iterdir()
    if d.is_dir() and (d / ".git").exists()
  )
  if not ws_repos:
    raise ValueError(
      f"Workspace '{workspace_name}' has no repos."
    )

  stage_dir.mkdir(parents=True)

  try:
    for repo_name in ws_repos:
      disk_path = _resolve_repo_path(repo_name, repos_config)
      source = get_repo_path(disk_path)
      dest = stage_dir / repo_name

      # Clone from root repo.
      clone_local(source, dest)

      # Check out workspace branch (create if needed).
      ws_repo = ws_dir / repo_name
      ws_branch = get_current_branch(ws_repo)
      try:
        create_branch(dest, ws_branch)
      except GitError:
        # Branch may already exist from the clone.
        run_git(["checkout", ws_branch], cwd=dest)

      # Allow workspace to push here.
      set_receive_update(dest)

      # Re-point workspace repo origin to this testing repo.
      run_git(
        ["remote", "set-url", "origin", str(dest)],
        cwd=ws_repo,
      )
  except (GitError, Exception):
    # Revert workspace origins on failure.
    for repo_name in ws_repos:
      ws_repo = ws_dir / repo_name
      if ws_repo.exists():
        disk_path = _resolve_repo_path(
          repo_name, repos_config,
        )
        root_path = get_repo_path(disk_path)
        try:
          run_git(
            ["remote", "set-url", "origin", str(root_path)],
            cwd=ws_repo,
          )
        except GitError:
          pass
    shutil.rmtree(stage_dir, ignore_errors=True)
    raise

  _generate_testing_claude_md(
    stage_dir, workspace_name, ws_repos, repos_config,
  )

  # Copy context packets.
  ctx_dest = stage_dir / "context"
  if CONTEXT_DIR.is_dir():
    shutil.copytree(CONTEXT_DIR, ctx_dest)

  return stage_dir


def delete_testing_stage(workspace_name):
  """Delete a testing stage and restore workspace origins.

  Args:
    workspace_name: Workspace name.

  Raises:
    FileNotFoundError: If testing stage does not exist.
  """
  stage_dir = TESTING_DIR / workspace_name
  if not stage_dir.exists():
    raise FileNotFoundError(
      f"Testing stage '{workspace_name}' not found."
    )

  repos_config = load_repos_config().get("repos", {})
  ws_dir = WORKSPACES_DIR / workspace_name

  # Restore workspace origins to root repos.
  if ws_dir.exists():
    ws_repos = sorted(
      d.name for d in ws_dir.iterdir()
      if d.is_dir() and (d / ".git").exists()
    )
    for repo_name in ws_repos:
      ws_repo = ws_dir / repo_name
      disk_path = _resolve_repo_path(repo_name, repos_config)
      root_path = get_repo_path(disk_path)
      try:
        run_git(
          ["remote", "set-url", "origin", str(root_path)],
          cwd=ws_repo,
        )
      except GitError:
        pass

  shutil.rmtree(stage_dir)


def list_testing_stages():
  """List all testing stages with metadata.

  Returns:
    List of dicts with keys: name, path, repos, branch.
  """
  if not TESTING_DIR.exists():
    return []

  results = []
  for stage_dir in sorted(TESTING_DIR.iterdir()):
    if not stage_dir.is_dir():
      continue
    repos = sorted(
      d.name for d in stage_dir.iterdir()
      if d.is_dir() and (d / ".git").exists()
    )
    branch = "?"
    if repos:
      try:
        branch = get_current_branch(stage_dir / repos[0])
      except GitError:
        pass
    results.append({
      "name": stage_dir.name,
      "path": str(stage_dir),
      "repos": repos,
      "branch": branch,
    })
  return results


def _generate_testing_claude_md(stage_dir, name, repos,
                                repos_config):
  """Generate a testing stage CLAUDE.md from the template."""
  tmpl_path = TEMPLATES_DIR / "testing_stage_claude.md"
  if not tmpl_path.exists():
    return

  tmpl = Template(tmpl_path.read_text())

  rows = []
  for repo_name in repos:
    cfg = repos_config.get(repo_name, {})
    repo_path = stage_dir / repo_name
    default_br = cfg.get(
      "default_branch", get_default_branch(repo_path),
    )
    push_order = cfg.get("push_order", "?")
    rows.append(
      f"| {repo_name} | {default_br} | {push_order} |"
    )
  repo_table = "\n".join(rows)

  packets = []
  if CONTEXT_DIR.is_dir():
    for f in sorted(CONTEXT_DIR.iterdir()):
      if f.is_file() and f.suffix == ".md":
        packets.append(f"- `context/{f.name}`")
  context_packets = (
    "\n".join(packets) if packets else "- (none)"
  )

  in_scope = "\n".join(f"- `{r}/`" for r in repos)

  content = tmpl.safe_substitute(
    workspace_name=name,
    in_scope_repos=in_scope,
    context_packets=context_packets,
    repo_table=repo_table,
    status="Not started",
  )

  out_path = stage_dir / "CLAUDE.md"
  out_path.write_text(content)
