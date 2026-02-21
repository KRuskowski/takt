"""Git operations via subprocess."""

import subprocess
from pathlib import Path


class GitError(Exception):
  """Raised when a git command fails."""

  def __init__(self, cmd, returncode, stderr):
    self.cmd = cmd
    self.returncode = returncode
    self.stderr = stderr
    super().__init__(
      f"git command failed (rc={returncode}): {' '.join(cmd)}"
      f"\n{stderr}"
    )


def run_git(args, cwd=None, check=True):
  """Run a git command and return stdout.

  Args:
    args: List of git arguments (without 'git' prefix).
    cwd: Working directory for the command.
    check: If True, raise GitError on non-zero exit.

  Returns:
    stdout as a stripped string.
  """
  cmd = ["git"] + list(args)
  result = subprocess.run(
    cmd, capture_output=True, text=True, cwd=cwd,
  )
  if check and result.returncode != 0:
    raise GitError(cmd, result.returncode, result.stderr.strip())
  return result.stdout.strip()


def clone_local(source_path, dest_path):
  """Clone a local repo. Source becomes origin.

  Args:
    source_path: Path to the source repo.
    dest_path: Path for the new clone.
  """
  run_git(["clone", str(source_path), str(dest_path)])


def create_branch(repo_path, branch_name, checkout=True):
  """Create and optionally checkout a new branch.

  Args:
    repo_path: Path to the repo.
    branch_name: Name of the branch to create.
    checkout: If True, switch to the new branch.
  """
  if checkout:
    run_git(["checkout", "-b", branch_name], cwd=repo_path)
  else:
    run_git(["branch", branch_name], cwd=repo_path)


def get_current_branch(repo_path):
  """Return the current branch name."""
  return run_git(
    ["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path,
  )


def get_branches(repo_path, remote=False):
  """Return list of branch names.

  Args:
    repo_path: Path to the repo.
    remote: If True, list remote branches instead of local.

  Returns:
    List of branch name strings.
  """
  args = ["branch"]
  if remote:
    args.append("-r")
  output = run_git(args, cwd=repo_path)
  if not output:
    return []
  return [
    b.strip().lstrip("* ") for b in output.splitlines()
  ]


def get_branch_ref(repo_path, branch=None):
  """Return the commit hash for a branch (default: HEAD).

  Args:
    repo_path: Path to the repo.
    branch: Branch name, or None for HEAD.

  Returns:
    Commit hash string.
  """
  ref = branch or "HEAD"
  return run_git(["rev-parse", ref], cwd=repo_path)


def get_diff(repo_path, base=None, head=None, max_lines=500):
  """Return diff output, truncated to max_lines.

  Args:
    repo_path: Path to the repo.
    base: Base ref for comparison.
    head: Head ref for comparison.
    max_lines: Maximum lines to return.

  Returns:
    Diff text, possibly truncated.
  """
  args = ["diff", "--stat"]
  if base and head:
    args.append(f"{base}...{head}")
  elif base:
    args.append(base)

  stat = run_git(args, cwd=repo_path)

  # Full diff.
  full_args = ["diff"]
  if base and head:
    full_args.append(f"{base}...{head}")
  elif base:
    full_args.append(base)

  full = run_git(full_args, cwd=repo_path)
  lines = full.splitlines()
  if len(lines) > max_lines:
    truncated = "\n".join(lines[:max_lines])
    truncated += (
      f"\n\n... truncated ({len(lines) - max_lines}"
      f" lines omitted) ..."
    )
    return f"{stat}\n\n{truncated}"
  return f"{stat}\n\n{full}" if full else stat


def get_log(repo_path, base=None, head=None, max_count=20):
  """Return formatted git log.

  Args:
    repo_path: Path to the repo.
    base: Base ref (show commits after this).
    head: Head ref (show commits up to this).
    max_count: Maximum number of commits.

  Returns:
    Formatted log string.
  """
  args = [
    "log", "--oneline", "--no-decorate", f"-{max_count}",
  ]
  if base and head:
    args.append(f"{base}..{head}")
  elif head:
    args.append(head)
  return run_git(args, cwd=repo_path, check=False)


def push_branch(repo_path, branch, remote="origin"):
  """Push a branch to a remote.

  Args:
    repo_path: Path to the repo.
    branch: Branch name to push.
    remote: Remote name (default: origin).
  """
  run_git(["push", remote, branch], cwd=repo_path)


def set_config(repo_path, key, value):
  """Set a git config value in a repo.

  Args:
    repo_path: Path to the repo.
    key: Config key (e.g. 'receive.denyCurrentBranch').
    value: Config value.
  """
  run_git(["config", key, value], cwd=repo_path)


def set_receive_update(repo_path):
  """Configure a repo to accept pushes and update its working tree.

  Sets receive.denyCurrentBranch=updateInstead so pushes to the
  checked-out branch update the working tree automatically.

  Args:
    repo_path: Path to the repo.
  """
  set_config(
    repo_path, "receive.denyCurrentBranch", "updateInstead",
  )


def get_status(repo_path, short=True):
  """Return git status output.

  Args:
    repo_path: Path to the repo.
    short: If True, use short format.

  Returns:
    Status string.
  """
  args = ["status"]
  if short:
    args.append("--short")
  return run_git(args, cwd=repo_path)
