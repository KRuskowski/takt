"""Git operations via subprocess."""

import os
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

  Does NOT initialize submodules — callers should call
  init_submodules() after checking out the correct branch,
  since submodule pointers may differ between branches.

  Args:
    source_path: Path to the source repo.
    dest_path: Path for the new clone.
  """
  run_git(["clone", str(source_path), str(dest_path)])


def _parse_submodules(repo_path):
  """Parse .gitmodules and return submodule name/path pairs.

  Args:
    repo_path: Path to the repo.

  Returns:
    List of (name, relative_path) tuples.
  """
  repo_path = Path(repo_path)
  output = run_git(
    ["config", "--file", ".gitmodules",
     "--get-regexp", r"submodule\..*\.path"],
    cwd=repo_path, check=False,
  )
  if not output:
    return []
  result = []
  for line in output.splitlines():
    # Format: submodule.<name>.path <path>
    key, path = line.split(None, 1)
    # Extract name from submodule.<name>.path
    name = key.removeprefix("submodule.").removesuffix(".path")
    result.append((name, path))
  return result


def _resolve_submodule_git_dir(worktree_path):
  """Resolve a submodule worktree path to its git object dir.

  Submodule worktrees contain a .git file pointing to
  .git/modules/<name>. Returns the resolved git dir if
  found, otherwise returns the worktree path as-is (for
  regular repos).

  Args:
    worktree_path: Path to the submodule worktree.

  Returns:
    Resolved Path to the git object directory.
  """
  git_file = worktree_path / ".git"
  if git_file.is_file():
    text = git_file.read_text().strip()
    if text.startswith("gitdir:"):
      rel = text.split(":", 1)[1].strip()
      return (worktree_path / rel).resolve()
  return worktree_path


def _enable_sha_fetch(git_dir):
  """Enable fetching specific SHAs from a git repo.

  Sets uploadPack.allowReachableSHA1InWant=true so that
  clients can fetch commits by SHA even when those commits
  aren't on any branch (e.g. detached HEAD).

  Args:
    git_dir: Path to the git directory (bare or .git/).
  """
  run_git(
    ["config", "uploadPack.allowReachableSHA1InWant",
     "true"],
    cwd=git_dir,
  )


def init_submodules(repo_path, reference=None):
  """Initialize and update submodules if the repo has any.

  No-op if the repo has no .gitmodules file.

  Args:
    repo_path: Path to the repo.
    reference: Optional path to a repo whose submodule
      worktrees contain the needed objects. Used when
      submodules have local-only commits (e.g. agent
      changes) that aren't at the upstream URL.
  """
  repo_path = Path(repo_path)
  if not (repo_path / ".gitmodules").exists():
    return
  if reference:
    reference = Path(reference)
    for name, rel_path in _parse_submodules(repo_path):
      sub_worktree = reference / rel_path
      if sub_worktree.is_dir():
        # Resolve the actual git object dir — submodule
        # worktrees use a .git file pointing to
        # .git/modules/<path>.
        git_dir = _resolve_submodule_git_dir(sub_worktree)
        run_git(
          ["config", f"submodule.{name}.url",
           str(git_dir)],
          cwd=repo_path,
        )
        # Allow the reference repo to serve detached HEAD
        # commits by SHA.
        _enable_sha_fetch(git_dir)
  # Allow file:// protocol — needed when submodule URLs
  # point to local paths (reference repos or local origins).
  run_git(
    ["-c", "protocol.file.allow=always",
     "submodule", "update", "--init", "--recursive"],
    cwd=repo_path,
  )


def rechain_submodule_remotes(repo_path, target_repo):
  """Update submodule origins to match the pipeline chain.

  For each submodule in repo_path, sets its origin URL to the
  corresponding submodule in target_repo. Also enables SHA
  fetching on the target so detached HEAD commits are servable.

  No-op if repo_path has no .gitmodules file.

  Args:
    repo_path: Path to the repo whose submodules to update.
    target_repo: Path to the repo whose submodules are the
      new fetch source.
  """
  repo_path = Path(repo_path)
  target_repo = Path(target_repo)
  if not (repo_path / ".gitmodules").exists():
    return
  for name, rel_path in _parse_submodules(repo_path):
    sub_path = repo_path / rel_path
    target_sub = target_repo / rel_path
    if not sub_path.is_dir() or not target_sub.is_dir():
      continue
    target_git_dir = _resolve_submodule_git_dir(target_sub)
    # Update parent config.
    run_git(
      ["config", f"submodule.{name}.url",
       str(target_git_dir)],
      cwd=repo_path,
    )
    # Update submodule's own origin.
    try:
      run_git(
        ["remote", "set-url", "origin",
         str(target_git_dir)],
        cwd=sub_path,
      )
    except GitError:
      pass
    # Allow target to serve detached HEAD commits.
    _enable_sha_fetch(target_git_dir)
    # Allow this submodule to serve them too (for the
    # next stage in the chain).
    local_git_dir = _resolve_submodule_git_dir(sub_path)
    _enable_sha_fetch(local_git_dir)


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


def install_push_hook(repo_path):
  """Install a post-receive hook that writes a .pipeline-push marker.

  The marker file signals to agents that upstream pushed new
  commits. Each push appends a timestamped line.

  Args:
    repo_path: Path to the repo.
  """
  repo_path = Path(repo_path)
  hooks_dir = repo_path / ".git" / "hooks"
  hooks_dir.mkdir(parents=True, exist_ok=True)
  hook_path = hooks_dir / "post-receive"
  hook_path.write_text(
    '#!/bin/bash\n'
    '# Signal that upstream pushed new commits.\n'
    '# Use GIT_DIR/.. because --show-toplevel resolves\n'
    '# wrong when GIT_DIR is set (as in hook context).\n'
    'REPO_ROOT="$(cd "$(git rev-parse --git-dir)/.." '
    '&& pwd)"\n'
    'while read old new ref; do\n'
    '  echo "$(date -Is) $old $new $ref" \\\n'
    '    >> "$REPO_ROOT/.pipeline-push"\n'
    'done\n'
    '# Update submodules if present.\n'
    'if [ -f "$REPO_ROOT/.gitmodules" ]; then\n'
    '  unset GIT_DIR\n'
    '  git -C "$REPO_ROOT" '
    '-c protocol.file.allow=always \\\n'
    '    submodule update --init --recursive\n'
    'fi\n'
  )
  hook_path.chmod(0o755)


def get_index_mtime(repo_path):
  """Return the mtime of .git/index as an epoch float.

  Args:
    repo_path: Path to the repo.

  Returns:
    Modification time as epoch float, or 0.0 if the file
    does not exist.
  """
  index = os.path.join(str(repo_path), ".git", "index")
  try:
    return os.path.getmtime(index)
  except OSError:
    return 0.0


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
