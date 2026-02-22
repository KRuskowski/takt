"""Worktree lifecycle for pipeline runs.

Creates and removes git worktrees from bare root repos
for isolated pipeline execution. Each run gets a
directory under ~/dev/runs/<ws>-<run_id>/ with one
worktree per repo.
"""

import logging
import shutil
import subprocess

from lib.config import BASE_DIR, ROOT_DIR, load_repos_config

log = logging.getLogger("takt.worktree")

RUNS_DIR = BASE_DIR / "runs"


def get_run_dir(run_id, workspace):
  """Return the run directory path.

  Args:
    run_id: Integer run ID.
    workspace: Workspace name.

  Returns:
    Path to ~/dev/runs/<workspace>-<run_id>/.
  """
  return RUNS_DIR / f"{workspace}-{run_id}"


def create_run_worktrees(run_id, workspace, repos, branch):
  """Create git worktrees for a pipeline run.

  For each repo, creates a worktree from the bare root
  repo at ~/dev/runs/<ws>-<run_id>/<repo>/ on the
  given branch.

  Args:
    run_id: Integer run ID.
    workspace: Workspace name.
    repos: List of repo names.
    branch: Branch to check out.

  Returns:
    Path to the run directory.

  Raises:
    subprocess.CalledProcessError: If git worktree add
      fails for any repo.
  """
  run_dir = get_run_dir(run_id, workspace)
  run_dir.mkdir(parents=True, exist_ok=True)
  repos_config = load_repos_config().get("repos", {})
  for repo in repos:
    cfg = repos_config.get(repo, {})
    disk_path = cfg.get("path", repo)
    root_repo = _find_root_repo(disk_path)
    if root_repo is None:
      log.warning(
        "Root repo not found for %s, skipping", repo,
      )
      continue
    wt_path = run_dir / repo
    log.info(
      "Creating worktree for %s at %s", repo, wt_path,
    )
    subprocess.run(
      [
        "git", "-C", str(root_repo),
        "worktree", "add", str(wt_path), branch,
      ],
      capture_output=True, text=True, check=True,
    )
  return run_dir


def remove_run_worktrees(run_id, workspace, repos=None):
  """Remove git worktrees and run directory.

  Calls `git worktree remove` for each repo, then
  removes the run directory.

  Args:
    run_id: Integer run ID.
    workspace: Workspace name.
    repos: Optional list of repo names. If None, removes
      all subdirectories.
  """
  run_dir = get_run_dir(run_id, workspace)
  if not run_dir.exists():
    return
  if repos is None:
    repos = [
      d.name for d in run_dir.iterdir() if d.is_dir()
    ]
  repos_config = load_repos_config().get("repos", {})
  for repo in repos:
    wt_path = run_dir / repo
    if not wt_path.exists():
      continue
    cfg = repos_config.get(repo, {})
    disk_path = cfg.get("path", repo)
    root_repo = _find_root_repo(disk_path)
    if root_repo is None:
      log.warning(
        "Root repo not found for %s, removing dir", repo,
      )
      shutil.rmtree(wt_path, ignore_errors=True)
      continue
    log.info("Removing worktree for %s", repo)
    try:
      subprocess.run(
        [
          "git", "-C", str(root_repo),
          "worktree", "remove", "--force", str(wt_path),
        ],
        capture_output=True, text=True, check=True,
      )
    except subprocess.CalledProcessError as e:
      log.warning(
        "git worktree remove failed for %s: %s",
        repo, e.stderr,
      )
      shutil.rmtree(wt_path, ignore_errors=True)
  # Remove the run directory if empty.
  try:
    run_dir.rmdir()
  except OSError:
    # Not empty — some worktrees may have failed to remove.
    shutil.rmtree(run_dir, ignore_errors=True)


def _find_root_repo(disk_path):
  """Find the root repo path, handling bare repos.

  Checks for both normal repos (with .git/) and bare
  repos (directory is the git dir).

  Args:
    disk_path: Repo name or relative path.

  Returns:
    Path to the git repo, or None if not found.
  """
  repo_path = ROOT_DIR / disk_path
  if repo_path.exists():
    if (repo_path / ".git").is_dir():
      return repo_path
    if (repo_path / "HEAD").is_file():
      return repo_path
  # Try bare repo with .git suffix.
  bare = ROOT_DIR / f"{disk_path}.git"
  if bare.exists() and (bare / "HEAD").is_file():
    return bare
  return None
