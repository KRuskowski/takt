"""Tests for lib.pr_ops."""

import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from lib.pr_ops import (
  _get_github_slug,
  get_github_slugs,
  list_all_prs,
  list_prs_for_branch,
)


class TestGetGithubSlug(unittest.TestCase):
  """Tests for _get_github_slug."""

  @patch("lib.pr_ops.subprocess.run")
  def test_ssh_remote(self, mock_run):
    """Parses SSH-format GitHub remote."""
    mock_run.return_value = MagicMock(
      returncode=0,
      stdout=(
        "origin\tgit@github.com:acme/Repo.git (fetch)\n"
        "origin\tgit@github.com:acme/Repo.git (push)\n"
      ),
    )
    self.assertEqual(
      _get_github_slug(Path("/fake")), "acme/Repo"
    )

  @patch("lib.pr_ops.subprocess.run")
  def test_https_remote(self, mock_run):
    """Parses HTTPS-format GitHub remote."""
    mock_run.return_value = MagicMock(
      returncode=0,
      stdout=(
        "origin\thttps://github.com/acme/Repo.git (fetch)\n"
      ),
    )
    self.assertEqual(
      _get_github_slug(Path("/fake")), "acme/Repo"
    )

  @patch("lib.pr_ops.subprocess.run")
  def test_no_github_remote(self, mock_run):
    """Returns None when no GitHub remote exists."""
    mock_run.return_value = MagicMock(
      returncode=0,
      stdout="origin\t/home/user/dev/root/foo (fetch)\n",
    )
    self.assertIsNone(_get_github_slug(Path("/fake")))

  @patch("lib.pr_ops.subprocess.run")
  def test_git_failure(self, mock_run):
    """Returns None on git command failure."""
    mock_run.return_value = MagicMock(returncode=128)
    self.assertIsNone(_get_github_slug(Path("/fake")))

  @patch("lib.pr_ops.subprocess.run")
  def test_ssh_without_dot_git(self, mock_run):
    """Parses SSH remote without .git suffix."""
    mock_run.return_value = MagicMock(
      returncode=0,
      stdout=(
        "gh\tgit@github.com:acme/Repo (fetch)\n"
      ),
    )
    self.assertEqual(
      _get_github_slug(Path("/fake")), "acme/Repo"
    )


class TestGetGithubSlugs(unittest.TestCase):
  """Tests for get_github_slugs."""

  @patch("lib.pr_ops._get_github_slug")
  @patch("lib.pr_ops.ROOT_DIR", Path("/fake/root"))
  @patch("lib.pr_ops.load_repos_config")
  def test_builds_slug_map(self, mock_config, mock_slug):
    """Builds slug map from repos with GitHub remotes."""
    mock_config.return_value = {
      "repos": {
        "Foo": {"path": "Foo"},
        "Bar": {"path": "Bar"},
      }
    }
    # Foo exists, Bar doesn't.
    exists_orig = Path.exists

    def fake_exists(p):
      if str(p) == "/fake/root/Foo":
        return True
      if str(p) == "/fake/root/Bar":
        return False
      return exists_orig(p)

    mock_slug.return_value = "acme/Foo"
    with patch.object(Path, "exists", fake_exists):
      result = get_github_slugs()
    self.assertEqual(result, {"Foo": "acme/Foo"})


class TestListPrsForBranch(unittest.TestCase):
  """Tests for list_prs_for_branch."""

  @patch("lib.pr_ops.subprocess.run")
  def test_returns_prs(self, mock_run):
    """Returns parsed PR list from gh output."""
    prs = [
      {
        "number": 42,
        "title": "Add feature",
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "url": "https://github.com/acme/Repo/pull/42",
      }
    ]
    mock_run.return_value = MagicMock(
      returncode=0,
      stdout=json.dumps(prs),
    )
    result = list_prs_for_branch("acme/Repo", "feat-x")
    self.assertEqual(len(result), 1)
    self.assertEqual(result[0]["number"], 42)

  @patch("lib.pr_ops.subprocess.run")
  def test_returns_empty_on_error(self, mock_run):
    """Returns empty list on gh failure."""
    mock_run.return_value = MagicMock(
      returncode=1, stdout=""
    )
    self.assertEqual(
      list_prs_for_branch("acme/Repo", "feat-x"), []
    )

  @patch("lib.pr_ops.subprocess.run")
  def test_returns_empty_on_timeout(self, mock_run):
    """Returns empty list on timeout."""
    mock_run.side_effect = subprocess.TimeoutExpired(
      "gh", 15
    )
    self.assertEqual(
      list_prs_for_branch("acme/Repo", "feat-x"), []
    )


class TestListAllPrs(unittest.TestCase):
  """Tests for list_all_prs."""

  @patch("lib.pr_ops.shutil.which", return_value=None)
  def test_gh_not_installed(self, _):
    """Returns available=False when gh not found."""
    rows, available = list_all_prs()
    self.assertEqual(rows, [])
    self.assertFalse(available)

  @patch("lib.pr_ops.list_prs_for_branch")
  @patch("lib.pr_ops.get_github_slugs")
  @patch("lib.pr_ops.shutil.which", return_value="/usr/bin/gh")
  def test_gathers_prs(self, _, mock_slugs, mock_prs):
    """Gathers PRs across workspaces and repos."""
    mock_slugs.return_value = {"Foo": "acme/Foo"}
    mock_prs.return_value = [
      {
        "number": 10,
        "title": "Fix bug",
        "isDraft": True,
        "mergeable": "CONFLICTING",
        "url": "https://github.com/acme/Foo/pull/10",
      }
    ]
    with patch(
      "lib.workspace_ops.list_workspaces"
    ) as mock_ws:
      mock_ws.return_value = [
        {"name": "feat-x", "branch": "feat-x"},
      ]
      rows, available = list_all_prs()
    self.assertTrue(available)
    self.assertEqual(len(rows), 1)
    self.assertEqual(rows[0]["workspace"], "feat-x")
    self.assertEqual(rows[0]["repo"], "Foo")
    self.assertTrue(rows[0]["is_draft"])

  @patch("lib.pr_ops.get_github_slugs", return_value={})
  @patch("lib.pr_ops.shutil.which", return_value="/usr/bin/gh")
  def test_no_slugs(self, _, __):
    """Returns empty when no repos have GitHub remotes."""
    rows, available = list_all_prs()
    self.assertEqual(rows, [])
    self.assertTrue(available)


if __name__ == "__main__":
  unittest.main()
