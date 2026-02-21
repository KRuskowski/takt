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
  UTILITY_DIR,
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


def _create_stage(workspace_name, stage_base_dir, stage_type,
                   template_name, repoint_upstream=True):
  """Create a stage for an existing workspace.

  Clones repos from root into stage_base_dir/<workspace>/.
  Each clone checks out the workspace branch and gets a
  CLAUDE.md from the named template.

  Args:
    workspace_name: Workspace (= branch) name.
    stage_base_dir: Base directory for this stage type.
    stage_type: Human-readable stage name for messages.
    template_name: Filename in templates/ for CLAUDE.md.
    repoint_upstream: If True, re-point the upstream stage's
      origin to this stage (workspace origin for testing,
      testing origin for utility).

  Returns:
    Path to the created stage directory.

  Raises:
    FileNotFoundError: If workspace does not exist.
    FileExistsError: If stage already exists.
    GitError: If cloning or branch operations fail.
  """
  ws_dir = WORKSPACES_DIR / workspace_name
  if not ws_dir.exists():
    raise FileNotFoundError(
      f"Workspace '{workspace_name}' not found."
    )

  stage_dir = stage_base_dir / workspace_name
  if stage_dir.exists():
    raise FileExistsError(
      f"{stage_type} stage '{workspace_name}' already "
      f"exists at {stage_dir}"
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

  # Determine upstream repos to re-point. For testing stages,
  # upstream is the workspace. For utility stages, upstream is
  # the testing stage.
  if repoint_upstream:
    if stage_base_dir == UTILITY_DIR:
      upstream_dir = TESTING_DIR / workspace_name
    else:
      upstream_dir = ws_dir
  else:
    upstream_dir = None

  stage_dir.mkdir(parents=True)

  try:
    for repo_name in ws_repos:
      disk_path = _resolve_repo_path(repo_name, repos_config)
      source = get_repo_path(disk_path)
      dest = stage_dir / repo_name

      clone_local(source, dest)

      # Check out workspace branch (create if needed).
      ws_repo = ws_dir / repo_name
      ws_branch = get_current_branch(ws_repo)
      try:
        create_branch(dest, ws_branch)
      except GitError:
        run_git(["checkout", ws_branch], cwd=dest)

      # Allow upstream to push here.
      set_receive_update(dest)

      # Re-point upstream origin to this stage repo.
      if upstream_dir:
        upstream_repo = upstream_dir / repo_name
        if upstream_repo.exists():
          run_git(
            ["remote", "set-url", "origin", str(dest)],
            cwd=upstream_repo,
          )
  except (GitError, Exception):
    # Revert upstream origins on failure.
    if upstream_dir:
      for repo_name in ws_repos:
        upstream_repo = upstream_dir / repo_name
        if upstream_repo and upstream_repo.exists():
          disk_path = _resolve_repo_path(
            repo_name, repos_config,
          )
          root_path = get_repo_path(disk_path)
          try:
            run_git(
              ["remote", "set-url", "origin",
               str(root_path)],
              cwd=upstream_repo,
            )
          except GitError:
            pass
    shutil.rmtree(stage_dir, ignore_errors=True)
    raise

  _generate_stage_claude_md(
    stage_dir, workspace_name, ws_repos, repos_config,
    template_name,
  )

  # Copy context packets.
  ctx_dest = stage_dir / "context"
  if CONTEXT_DIR.is_dir():
    shutil.copytree(CONTEXT_DIR, ctx_dest)

  return stage_dir


def _delete_stage(workspace_name, stage_base_dir, stage_type,
                  upstream_dir=None):
  """Delete a stage and restore upstream origins to root.

  Args:
    workspace_name: Workspace name.
    stage_base_dir: Base directory for this stage type.
    stage_type: Human-readable stage name for messages.
    upstream_dir: Directory whose repos should have their
      origins restored. None to skip.

  Raises:
    FileNotFoundError: If stage does not exist.
  """
  stage_dir = stage_base_dir / workspace_name
  if not stage_dir.exists():
    raise FileNotFoundError(
      f"{stage_type} stage '{workspace_name}' not found."
    )

  repos_config = load_repos_config().get("repos", {})

  if upstream_dir and upstream_dir.exists():
    upstream_repos = sorted(
      d.name for d in upstream_dir.iterdir()
      if d.is_dir() and (d / ".git").exists()
    )
    for repo_name in upstream_repos:
      repo = upstream_dir / repo_name
      disk_path = _resolve_repo_path(repo_name, repos_config)
      root_path = get_repo_path(disk_path)
      try:
        run_git(
          ["remote", "set-url", "origin", str(root_path)],
          cwd=repo,
        )
      except GitError:
        pass

  shutil.rmtree(stage_dir)


def _list_stages(stage_base_dir):
  """List all stages in a base directory.

  Returns:
    List of dicts with keys: name, path, repos, branch.
  """
  if not stage_base_dir.exists():
    return []

  results = []
  for stage_dir in sorted(stage_base_dir.iterdir()):
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


def _generate_stage_claude_md(stage_dir, name, repos,
                              repos_config, template_name):
  """Generate a stage CLAUDE.md from the named template."""
  tmpl_path = TEMPLATES_DIR / template_name
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


# -- Testing stage public API --

def create_testing_stage(workspace_name):
  """Create a testing stage for a workspace.

  Workspace repos' origins are re-pointed to the testing
  stage. Chain: workspace -> testing -> root.
  """
  return _create_stage(
    workspace_name, TESTING_DIR, "Testing",
    "testing_stage_claude.md",
  )


def delete_testing_stage(workspace_name):
  """Delete a testing stage and restore workspace origins."""
  ws_dir = WORKSPACES_DIR / workspace_name
  _delete_stage(
    workspace_name, TESTING_DIR, "Testing",
    upstream_dir=ws_dir,
  )


def list_testing_stages():
  """List all testing stages."""
  return _list_stages(TESTING_DIR)


# -- Utility stage public API --

def create_utility_stage(workspace_name):
  """Create a utility stage for a workspace.

  Testing stage repos' origins are re-pointed to the utility
  stage. Chain: workspace -> testing -> utility -> root.

  The utility agent watches for pushes and creates PRs on
  GitHub.
  """
  return _create_stage(
    workspace_name, UTILITY_DIR, "Utility",
    "utility_stage_claude.md",
  )


def delete_utility_stage(workspace_name):
  """Delete a utility stage and restore testing origins."""
  testing_dir = TESTING_DIR / workspace_name
  _delete_stage(
    workspace_name, UTILITY_DIR, "Utility",
    upstream_dir=testing_dir,
  )


def list_utility_stages():
  """List all utility stages."""
  return _list_stages(UTILITY_DIR)
