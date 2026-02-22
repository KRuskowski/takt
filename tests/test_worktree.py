"""Tests for lib.worktree — git worktree lifecycle."""

import subprocess
from unittest.mock import patch

import pytest

from lib import worktree


@pytest.fixture
def fake_root(tmp_path):
  """Create a fake bare root repo with a branch."""
  root_dir = tmp_path / "root"
  root_dir.mkdir()
  repo = root_dir / "test-repo"
  # Create a normal repo, then convert to bare-ish.
  subprocess.run(
    ["git", "init", str(repo)],
    capture_output=True, check=True,
  )
  subprocess.run(
    ["git", "-C", str(repo), "config",
     "user.email", "test@test.com"],
    capture_output=True, check=True,
  )
  subprocess.run(
    ["git", "-C", str(repo), "config",
     "user.name", "Test"],
    capture_output=True, check=True,
  )
  subprocess.run(
    ["git", "-C", str(repo),
     "commit", "--allow-empty", "-m", "init"],
    capture_output=True, check=True,
  )
  subprocess.run(
    ["git", "-C", str(repo),
     "branch", "test-branch"],
    capture_output=True, check=True,
  )
  return root_dir, repo


@pytest.fixture
def patched_dirs(tmp_path, fake_root):
  """Patch config dirs to use temp directories."""
  root_dir, _ = fake_root
  runs_dir = tmp_path / "runs"
  with patch.object(worktree, "ROOT_DIR", root_dir), \
       patch.object(worktree, "RUNS_DIR", runs_dir), \
       patch("lib.worktree.load_repos_config", return_value={
         "repos": {
           "test-repo": {"path": "test-repo"},
         },
       }):
    yield tmp_path, root_dir, runs_dir


class TestGetRunDir:
  """get_run_dir tests."""

  def test_path_format(self):
    """Returns ~/dev/runs/<ws>-<id>."""
    result = worktree.get_run_dir(42, "my-feature")
    assert result.name == "my-feature-42"


class TestCreateRunWorktrees:
  """create_run_worktrees tests."""

  def test_creates_worktree(self, patched_dirs):
    """Creates a git worktree for each repo."""
    _, _, runs_dir = patched_dirs
    run_dir = worktree.create_run_worktrees(
      1, "test-ws", ["test-repo"], "test-branch",
    )
    assert run_dir.exists()
    wt = run_dir / "test-repo"
    assert wt.is_dir()
    # Verify it's a valid git worktree.
    result = subprocess.run(
      ["git", "-C", str(wt), "rev-parse", "--is-inside-work-tree"],
      capture_output=True, text=True,
    )
    assert result.stdout.strip() == "true"
    # Verify correct branch.
    result = subprocess.run(
      ["git", "-C", str(wt),
       "rev-parse", "--abbrev-ref", "HEAD"],
      capture_output=True, text=True,
    )
    assert result.stdout.strip() == "test-branch"

  def test_skips_missing_repo(self, patched_dirs):
    """Skips repos that don't exist in root."""
    run_dir = worktree.create_run_worktrees(
      2, "test-ws", ["nonexistent"], "test-branch",
    )
    assert run_dir.exists()
    assert not (run_dir / "nonexistent").exists()


class TestRemoveRunWorktrees:
  """remove_run_worktrees tests."""

  def test_removes_worktree(self, patched_dirs):
    """Removes worktree and run directory."""
    run_dir = worktree.create_run_worktrees(
      3, "test-ws", ["test-repo"], "test-branch",
    )
    assert run_dir.exists()
    worktree.remove_run_worktrees(
      3, "test-ws", ["test-repo"],
    )
    assert not run_dir.exists()

  def test_removes_all_when_repos_none(self, patched_dirs):
    """Removes all repos when repos=None."""
    run_dir = worktree.create_run_worktrees(
      4, "test-ws", ["test-repo"], "test-branch",
    )
    worktree.remove_run_worktrees(4, "test-ws")
    assert not run_dir.exists()

  def test_noop_when_not_exists(self, patched_dirs):
    """No error when run dir doesn't exist."""
    worktree.remove_run_worktrees(99, "nope")


class TestFindRootRepo:
  """_find_root_repo tests."""

  def test_finds_normal_repo(self, patched_dirs):
    """Finds a normal (non-bare) repo."""
    result = worktree._find_root_repo("test-repo")
    assert result is not None
    assert result.name == "test-repo"

  def test_returns_none_for_missing(self, patched_dirs):
    """Returns None for nonexistent repo."""
    result = worktree._find_root_repo("no-such-repo")
    assert result is None

  def test_finds_bare_repo(self, tmp_path):
    """Finds a bare repo with .git suffix."""
    root_dir = tmp_path / "bare_root"
    root_dir.mkdir()
    bare = root_dir / "myrepo.git"
    subprocess.run(
      ["git", "init", "--bare", str(bare)],
      capture_output=True, check=True,
    )
    with patch.object(worktree, "ROOT_DIR", root_dir):
      result = worktree._find_root_repo("myrepo")
    assert result is not None
    assert result.name == "myrepo.git"
