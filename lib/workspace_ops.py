"""Workspace operations for programmatic use.

Extracted from bin/workspace.py so both the CLI and TUI
dashboard can share the same logic.
"""

import shutil
from string import Template

from lib.config import (
  CONTEXT_DIR,
  TEMPLATES_DIR,
  WORKSPACES_DIR,
  get_default_branch,
  get_repo_path,
  load_repos_config,
  validate_repo,
)
from lib.git_utils import (
  GitError,
  clone_local,
  create_branch,
  get_current_branch,
  get_index_mtime,
  get_status,
  init_submodules,
)


def _compute_last_active(base_dir, repos):
  """Compute the most recent activity timestamp.

  Checks .git/index mtime for each repo and CLAUDE.md
  mtime. Returns the newest timestamp.

  Args:
    base_dir: Path to the workspace directory.
    repos: List of repo directory names.

  Returns:
    Epoch float of the most recent activity, or 0.0.
  """
  mtimes = []
  for repo_name in repos:
    mtimes.append(get_index_mtime(base_dir / repo_name))
  claude_md = base_dir / "CLAUDE.md"
  if claude_md.exists():
    try:
      mtimes.append(claude_md.stat().st_mtime)
    except OSError:
      pass
  return max(mtimes) if mtimes else 0.0


def list_workspaces():
  """List all workspaces with metadata.

  Returns:
    List of dicts with keys: name, path, repos, branch,
    last_active.
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
      "last_active": _compute_last_active(ws_dir, repos),
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
  """Resolve a repo key to its filesystem path.

  Falls back to repo_name if not found in config.

  Args:
    repo_name: Repo name key.
    repos_config: Repos config dict.

  Returns:
    Filesystem path string.
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
    if not validate_repo(
      _resolve_repo_path(r, repos_config)
    )
  ]
  if invalid:
    raise ValueError(
      f"Invalid git repos: {', '.join(invalid)}"
    )
  ws_dir.mkdir(parents=True)
  try:
    for repo_name in repos:
      disk_path = _resolve_repo_path(
        repo_name, repos_config,
      )
      source = get_repo_path(disk_path)
      dest = ws_dir / repo_name
      clone_local(source, dest)
      create_branch(dest, name)
      init_submodules(dest)
  except GitError:
    shutil.rmtree(ws_dir, ignore_errors=True)
    raise
  _generate_workspace_claude_md(
    ws_dir, name, repos, repos_config,
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


def _build_git_rules(name):
  """Build the git rules text for a workspace CLAUDE.md.

  Args:
    name: Workspace (= branch) name.

  Returns:
    Git rules string.
  """
  return (
    f"- Branch name: `{name}` (same across all repos)\n"
    f"- Push to origin only "
    f"(origin = root repo at ~/dev/root/<repo>)\n"
    f"- NEVER push to GitHub. The operator handles "
    f"GitHub pushes.\n"
    f"- Sign all commits.\n"
    f"- Push order follows dependency chain "
    f"(upstream first)."
  )


def _generate_workspace_claude_md(ws_dir, name, repos,
                                  repos_config):
  """Generate a workspace CLAUDE.md from the template.

  Args:
    ws_dir: Workspace directory.
    name: Workspace name.
    repos: List of repo names.
    repos_config: Repos config dict.
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
  git_rules = _build_git_rules(name)
  content = tmpl.safe_substitute(
    workspace_name=name,
    role_section="(specify role when launching agent)",
    task_section="(specify task description)",
    acceptance_criteria="(specify acceptance criteria)",
    in_scope_repos=in_scope,
    reference_repos="- (none specified)",
    context_packets=context_packets,
    repo_table=repo_table,
    git_rules=git_rules,
    pipeline_section="",
    status="Not started",
  )
  out_path = ws_dir / "CLAUDE.md"
  out_path.write_text(content)


def workspace_git_summary(ws):
  """Get a brief git summary for TUI display.

  Args:
    ws: Workspace dict from list_workspaces().

  Returns:
    Dict mapping repo_name to short status string.
  """
  ws_dir = WORKSPACES_DIR / ws["name"]
  summaries = {}
  for repo_name in ws.get("repos", []):
    repo_path = ws_dir / repo_name
    if not repo_path.exists():
      summaries[repo_name] = "missing"
      continue
    try:
      status = get_status(repo_path)
      if not status:
        summaries[repo_name] = "clean"
      else:
        n = len(status.splitlines())
        summaries[repo_name] = f"{n} files"
    except GitError:
      summaries[repo_name] = "error"
  return summaries
