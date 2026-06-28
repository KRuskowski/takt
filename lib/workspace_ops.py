"""Workspace operations for programmatic use.

Extracted from bin/workspace.py so both the CLI and TUI
dashboard can share the same logic.
"""

import shutil
import subprocess
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
  run_git,
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
      # Set git identity per-repo so agents commit
      # correctly regardless of global gitconfig.
      run_git(
        ["config", "user.name", "Karl Ruskowski"],
        cwd=dest,
      )
      run_git(
        ["config", "user.email",
         "karl.ruskowski@optris.de"],
        cwd=dest,
      )
      run_git(
        ["config", "commit.gpgsign", "false"],
        cwd=dest,
      )
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
  # Install pre-commit hooks.
  _install_hooks(ws_dir, repos)
  return ws_dir


def add_repo_to_workspace(name, repo):
  """Add a repo to an existing workspace.

  Clones the repo from ROOT_DIR into the workspace
  directory and checks out the workspace branch.

  Args:
    name: Workspace name (= branch name).
    repo: Repo name to add.

  Raises:
    FileNotFoundError: If workspace doesn't exist.
  """
  ws_dir = WORKSPACES_DIR / name
  if not ws_dir.is_dir():
    raise FileNotFoundError(
      f"Workspace '{name}' not found."
    )
  repo_path = get_repo_path(repo)
  if repo_path is None:
    raise ValueError(f"Unknown repo: {repo}")
  dest = ws_dir / repo
  if dest.is_dir():
    raise FileExistsError(
      f"Repo '{repo}' already in workspace."
    )
  subprocess.run(
    ["git", "clone", str(repo_path), str(dest)],
    check=True, capture_output=True, text=True,
  )
  subprocess.run(
    ["git", "checkout", "-b", name],
    cwd=str(dest),
    check=True, capture_output=True, text=True,
  )


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
    f"- Commit as: Karl Ruskowski "
    f"<karl.ruskowski@optris.de>\n"
    f"- Do NOT co-author commits\n"
    f"- Do NOT sign commits (no GPG key)\n"
    f"- Push to origin only "
    f"(origin = root repo at ~/dev/root/<repo>)\n"
    f"- NEVER push to GitHub. The operator handles "
    f"GitHub pushes.\n"
    f"- Push order follows dependency chain "
    f"(upstream first)."
  )


def _build_section(repos):
  """Build per-repo build instructions.

  Args:
    repos: List of repo names.

  Returns:
    Build instructions string.
  """
  sections = []
  for repo in repos:
    if repo in ("OTC.Relay", "OTC.SDK.Server",
                "OTC.SDK.View"):
      sections.append(
        f"### {repo}\n"
        f"```bash\n"
        f"cd {repo}\n"
        f"cmake --preset default\n"
        f"cmake --build build --parallel\n"
        f"```\n"
        f"- Do NOT install deps manually — "
        f"FetchContent handles C++ deps\n"
        f"- If a tool is missing, report it and stop"
      )
    elif repo == "OTC.SDK":
      sections.append(
        f"### {repo}\n"
        f"Proprietary SDK — prebuilt, do not build."
      )
  if not sections:
    return "Follow each repo's CLAUDE.md for build steps."
  return "\n\n".join(sections)


_PRE_COMMIT_HOOK = """\
#!/bin/sh
# Installed by takt. Runs checks before commit.
TAKT_DIR="{takt_dir}"
REPO_NAME="$(basename "$(pwd)")"
if [ -x "$TAKT_DIR/.venv/bin/python3" ]; then
  "$TAKT_DIR/.venv/bin/python3" -c "
import sys, json
sys.path.insert(0, '$TAKT_DIR')
from lib.checks import check_build, check_secrets
ws = '$(dirname "$(pwd)")'
r = check_secrets(ws, ['$REPO_NAME'])
if r['status'] == 'fail':
    print('takt pre-commit: secrets detected!')
    for h in r.get('hits', []):
        print(f'  {{h[\"line\"][:80]}}')
    sys.exit(1)
"
fi
"""


def _install_hooks(ws_dir, repos):
  """Install takt pre-commit hooks in workspace repos.

  Args:
    ws_dir: Path to the workspace directory.
    repos: List of repo names.
  """
  takt_dir = str(
    Path(__file__).resolve().parent.parent
  )
  for repo in repos:
    hooks_dir = ws_dir / repo / ".git" / "hooks"
    if not hooks_dir.exists():
      continue
    hook = hooks_dir / "pre-commit"
    hook.write_text(
      _PRE_COMMIT_HOOK.format(takt_dir=takt_dir)
    )
    hook.chmod(0o755)


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
  build_instructions = _build_section(repos)
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
    build_section=build_instructions,
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
