"""Workspace and stage operations for programmatic use.

Extracted from bin/workspace.py so both the CLI and TUI
dashboard can share the same logic.
"""

import shutil
from string import Template

import yaml

from lib.config import (
  CONTEXT_DIR,
  STAGES_DIR,
  TEMPLATES_DIR,
  WORKSPACES_DIR,
  get_default_branch,
  get_repo_path,
  load_repos_config,
  parse_pipeline_roles,
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
                                  repos_config,
                                  role_snippet=None):
  """Generate a workspace CLAUDE.md from the template.

  Args:
    ws_dir: Workspace or stage directory.
    name: Workspace name.
    repos: List of repo names.
    repos_config: Repos config dict.
    role_snippet: Optional role text to inject. If None, uses
      a placeholder.
  """
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

  role_section = (
    role_snippet
    if role_snippet
    else "(specify role when launching agent)"
  )

  content = tmpl.safe_substitute(
    workspace_name=name,
    role_section=role_section,
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


# -- Pipeline / stage operations --

def _load_pipeline(workspace_name):
  """Load pipeline.yaml for a workspace.

  Returns:
    List of role slugs in chain order, or empty list.
  """
  pipeline_path = STAGES_DIR / workspace_name / "pipeline.yaml"
  if not pipeline_path.exists():
    return []
  with open(pipeline_path) as f:
    data = yaml.safe_load(f) or {}
  return data.get("stages", [])


def _save_pipeline(workspace_name, stages):
  """Save pipeline.yaml for a workspace.

  Args:
    workspace_name: Workspace name.
    stages: List of role slugs in chain order.
  """
  ws_stages_dir = STAGES_DIR / workspace_name
  ws_stages_dir.mkdir(parents=True, exist_ok=True)
  pipeline_path = ws_stages_dir / "pipeline.yaml"
  with open(pipeline_path, "w") as f:
    yaml.safe_dump({"stages": stages}, f, default_flow_style=False)


def get_pipeline(workspace_name):
  """Get the full pipeline chain for a workspace.

  Returns:
    Dict with keys: workspace, stages (list of role slugs),
    chain (human-readable chain string).

  Raises:
    FileNotFoundError: If workspace does not exist.
  """
  ws_dir = WORKSPACES_DIR / workspace_name
  if not ws_dir.exists():
    raise FileNotFoundError(
      f"Workspace '{workspace_name}' not found."
    )

  stages = _load_pipeline(workspace_name)
  parts = ["workspace"] + stages + ["root"]
  chain = " -> ".join(parts)

  return {
    "workspace": workspace_name,
    "stages": stages,
    "chain": chain,
  }


def _rechain_remotes(workspace_name):
  """Rebuild the full remote chain from pipeline.yaml order.

  Chain: workspace -> stage1 -> stage2 -> ... -> root.
  Each link's repos get origin set to the next link.
  """
  stages = _load_pipeline(workspace_name)
  repos_config = load_repos_config().get("repos", {})

  ws_dir = WORKSPACES_DIR / workspace_name
  if not ws_dir.exists():
    return

  ws_repos = sorted(
    d.name for d in ws_dir.iterdir()
    if d.is_dir() and (d / ".git").exists()
  )

  # Build ordered list of directories in the chain.
  # Each entry is (dir, label) for the source of pushes.
  chain_dirs = [ws_dir]
  for role in stages:
    stage_dir = STAGES_DIR / workspace_name / role
    if stage_dir.exists():
      chain_dirs.append(stage_dir)

  # Set origins: each dir points to the next in the chain.
  # The last stage points to root.
  for i, src_dir in enumerate(chain_dirs):
    for repo_name in ws_repos:
      repo_path = src_dir / repo_name
      if not repo_path.exists():
        continue

      if i + 1 < len(chain_dirs):
        # Point to next stage.
        target = chain_dirs[i + 1] / repo_name
      else:
        # Last in chain: point to root.
        disk_path = _resolve_repo_path(
          repo_name, repos_config,
        )
        target = get_repo_path(disk_path)

      try:
        run_git(
          ["remote", "set-url", "origin", str(target)],
          cwd=repo_path,
        )
      except GitError:
        pass


def create_stage(workspace_name, role):
  """Create a stage for an existing workspace.

  Clones repos from root, checks out the workspace branch,
  injects the role snippet into the CLAUDE.md template, and
  rebuilds the remote chain.

  Args:
    workspace_name: Workspace (= branch) name.
    role: Role slug (e.g. "test", "review", "deploy_qa").

  Returns:
    Path to the created stage directory.

  Raises:
    FileNotFoundError: If workspace does not exist.
    FileExistsError: If stage already exists.
    ValueError: If role is not found in pipeline_roles.md.
    GitError: If cloning or branch operations fail.
  """
  ws_dir = WORKSPACES_DIR / workspace_name
  if not ws_dir.exists():
    raise FileNotFoundError(
      f"Workspace '{workspace_name}' not found."
    )

  roles = parse_pipeline_roles()
  if role not in roles:
    raise ValueError(
      f"Unknown role '{role}'. "
      f"Available: {', '.join(sorted(roles.keys()))}"
    )

  stage_dir = STAGES_DIR / workspace_name / role
  if stage_dir.exists():
    raise FileExistsError(
      f"Stage '{role}' for '{workspace_name}' already "
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
  except (GitError, Exception):
    shutil.rmtree(stage_dir, ignore_errors=True)
    raise

  # Generate CLAUDE.md with role snippet injected.
  _generate_workspace_claude_md(
    stage_dir, workspace_name, ws_repos, repos_config,
    role_snippet=roles[role],
  )

  # Copy context packets.
  ctx_dest = stage_dir / "context"
  if CONTEXT_DIR.is_dir():
    shutil.copytree(CONTEXT_DIR, ctx_dest)

  # Update pipeline and rebuild remote chain.
  pipeline = _load_pipeline(workspace_name)
  if role not in pipeline:
    pipeline.append(role)
    _save_pipeline(workspace_name, pipeline)
  _rechain_remotes(workspace_name)

  return stage_dir


def delete_stage(workspace_name, role):
  """Delete a stage and rebuild the remote chain.

  Args:
    workspace_name: Workspace name.
    role: Role slug to remove.

  Raises:
    FileNotFoundError: If stage does not exist.
  """
  stage_dir = STAGES_DIR / workspace_name / role
  if not stage_dir.exists():
    raise FileNotFoundError(
      f"Stage '{role}' for '{workspace_name}' not found."
    )

  shutil.rmtree(stage_dir)

  # Update pipeline.
  pipeline = _load_pipeline(workspace_name)
  if role in pipeline:
    pipeline.remove(role)
    _save_pipeline(workspace_name, pipeline)

  # Rebuild remote chain with this stage removed.
  _rechain_remotes(workspace_name)

  # Clean up empty workspace stages dir.
  ws_stages_dir = STAGES_DIR / workspace_name
  remaining = [
    d for d in ws_stages_dir.iterdir()
    if d.is_dir() and d.name != "pipeline.yaml"
  ]
  if not remaining:
    shutil.rmtree(ws_stages_dir)


def list_stages(workspace_name=None):
  """List stages, optionally filtered by workspace.

  Args:
    workspace_name: If given, list only stages for this
      workspace. Otherwise list all stages.

  Returns:
    List of dicts with keys: workspace, role, path, repos,
    branch.
  """
  if not STAGES_DIR.exists():
    return []

  results = []

  if workspace_name:
    ws_dirs = [STAGES_DIR / workspace_name]
  else:
    ws_dirs = sorted(STAGES_DIR.iterdir())

  for ws_dir in ws_dirs:
    if not ws_dir.is_dir():
      continue
    for role_dir in sorted(ws_dir.iterdir()):
      if not role_dir.is_dir():
        continue
      # Skip pipeline.yaml (it's a file, not dir).
      repos = sorted(
        d.name for d in role_dir.iterdir()
        if d.is_dir() and (d / ".git").exists()
      )
      branch = "?"
      if repos:
        try:
          branch = get_current_branch(
            role_dir / repos[0],
          )
        except GitError:
          pass
      results.append({
        "workspace": ws_dir.name,
        "role": role_dir.name,
        "path": str(role_dir),
        "repos": repos,
        "branch": branch,
      })

  return results
