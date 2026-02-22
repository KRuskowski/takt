"""Git status helpers for workspace overview.

Provides compact per-repo status and aggregated workspace
summary using lib.git_utils.run_git.
"""

from lib.git_utils import run_git


def compact_status(repo_path):
  """Return compact git status for a single repo.

  Runs git status --porcelain and git rev-list to determine
  dirty state and ahead/behind counts relative to upstream.

  Args:
    repo_path: Path to the git repository.

  Returns:
    Dict with keys: dirty (bool), ahead (int), behind (int),
    label (str). Label is a compact summary like "ok", "*",
    "+2 -1", "*+2".
  """
  porcelain = run_git(
    ["status", "--porcelain"], cwd=repo_path, check=False,
  )
  dirty = bool(porcelain.strip())

  ahead = 0
  behind = 0
  counts = run_git(
    ["rev-list", "--left-right", "--count",
     "HEAD...@{upstream}"],
    cwd=repo_path, check=False,
  )
  if counts.strip():
    parts = counts.strip().split()
    if len(parts) == 2:
      try:
        ahead = int(parts[0])
        behind = int(parts[1])
      except ValueError:
        pass

  label = _build_label(dirty, ahead, behind)
  return {
    "dirty": dirty,
    "ahead": ahead,
    "behind": behind,
    "label": label,
  }


def _build_label(dirty, ahead, behind):
  """Build a compact label string from status components.

  Args:
    dirty: Whether the worktree has uncommitted changes.
    ahead: Number of commits ahead of upstream.
    behind: Number of commits behind upstream.

  Returns:
    Compact label string.
  """
  parts = []
  if dirty:
    parts.append("*")
  if ahead:
    parts.append(f"+{ahead}")
  if behind:
    parts.append(f"-{behind}")
  return "".join(parts) if parts else "ok"


def workspace_git_summary(ws_path, repos):
  """Aggregate git status across workspace repos.

  Args:
    ws_path: Path to the workspace directory.
    repos: List of repo directory names within ws_path.

  Returns:
    Summary string like "ok", "*", "2* +3".
  """
  if not repos:
    return "ok"

  total_dirty = 0
  total_ahead = 0
  total_behind = 0

  for repo_name in repos:
    repo_path = ws_path / repo_name
    if not (repo_path / ".git").exists():
      continue
    status = compact_status(repo_path)
    if status["dirty"]:
      total_dirty += 1
    total_ahead += status["ahead"]
    total_behind += status["behind"]

  parts = []
  if total_dirty == 1:
    parts.append("*")
  elif total_dirty > 1:
    parts.append(f"{total_dirty}*")
  if total_ahead:
    parts.append(f"+{total_ahead}")
  if total_behind:
    parts.append(f"-{total_behind}")
  return " ".join(parts) if parts else "ok"
