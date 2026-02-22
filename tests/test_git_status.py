"""Tests for lib.git_status."""

import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib.git_status import (
  _build_label,
  compact_status,
  workspace_git_summary,
)


class TestBuildLabel(unittest.TestCase):
  """Tests for _build_label()."""

  def test_clean(self):
    """Clean repo returns 'ok'."""
    self.assertEqual(_build_label(False, 0, 0), "ok")

  def test_dirty_only(self):
    """Dirty worktree returns '*'."""
    self.assertEqual(_build_label(True, 0, 0), "*")

  def test_ahead_only(self):
    """Ahead commits return '+N'."""
    self.assertEqual(_build_label(False, 3, 0), "+3")

  def test_behind_only(self):
    """Behind commits return '-N'."""
    self.assertEqual(_build_label(False, 0, 2), "-2")

  def test_dirty_and_ahead(self):
    """Dirty + ahead returns '*+N'."""
    self.assertEqual(_build_label(True, 5, 0), "*+5")

  def test_all_components(self):
    """Dirty + ahead + behind returns '*+N-M'."""
    self.assertEqual(
      _build_label(True, 2, 1), "*+2-1"
    )

  def test_ahead_and_behind(self):
    """Ahead + behind returns '+N-M'."""
    self.assertEqual(
      _build_label(False, 4, 3), "+4-3"
    )


class TestCompactStatus(unittest.TestCase):
  """Tests for compact_status()."""

  @mock.patch("lib.git_status.run_git")
  def test_clean_repo(self, mock_run):
    """Clean repo with no upstream diff returns ok."""
    mock_run.side_effect = [
      "",   # status --porcelain
      "0\t0",  # rev-list counts
    ]
    result = compact_status("/fake/repo")
    self.assertFalse(result["dirty"])
    self.assertEqual(result["ahead"], 0)
    self.assertEqual(result["behind"], 0)
    self.assertEqual(result["label"], "ok")

  @mock.patch("lib.git_status.run_git")
  def test_dirty_repo(self, mock_run):
    """Dirty repo returns dirty=True and '*' label."""
    mock_run.side_effect = [
      " M file.py",  # status --porcelain
      "0\t0",
    ]
    result = compact_status("/fake/repo")
    self.assertTrue(result["dirty"])
    self.assertEqual(result["label"], "*")

  @mock.patch("lib.git_status.run_git")
  def test_ahead_commits(self, mock_run):
    """Repo ahead of upstream returns correct count."""
    mock_run.side_effect = [
      "",      # clean
      "3\t0",  # 3 ahead
    ]
    result = compact_status("/fake/repo")
    self.assertEqual(result["ahead"], 3)
    self.assertEqual(result["label"], "+3")

  @mock.patch("lib.git_status.run_git")
  def test_behind_commits(self, mock_run):
    """Repo behind upstream returns correct count."""
    mock_run.side_effect = [
      "",
      "0\t2",
    ]
    result = compact_status("/fake/repo")
    self.assertEqual(result["behind"], 2)
    self.assertEqual(result["label"], "-2")

  @mock.patch("lib.git_status.run_git")
  def test_no_upstream(self, mock_run):
    """Repo with no upstream returns 0 ahead/behind."""
    mock_run.side_effect = [
      "",  # clean
      "",  # no upstream (empty output)
    ]
    result = compact_status("/fake/repo")
    self.assertEqual(result["ahead"], 0)
    self.assertEqual(result["behind"], 0)
    self.assertEqual(result["label"], "ok")

  @mock.patch("lib.git_status.run_git")
  def test_dirty_and_ahead(self, mock_run):
    """Dirty repo ahead of upstream."""
    mock_run.side_effect = [
      "?? new.py\n M old.py",
      "5\t0",
    ]
    result = compact_status("/fake/repo")
    self.assertTrue(result["dirty"])
    self.assertEqual(result["ahead"], 5)
    self.assertEqual(result["label"], "*+5")


class TestWorkspaceGitSummary(unittest.TestCase):
  """Tests for workspace_git_summary()."""

  def test_empty_repos(self):
    """No repos returns 'ok'."""
    result = workspace_git_summary(Path("/fake"), [])
    self.assertEqual(result, "ok")

  @mock.patch("lib.git_status.compact_status")
  def test_all_clean(self, mock_cs):
    """All clean repos returns 'ok'."""
    mock_cs.return_value = {
      "dirty": False, "ahead": 0, "behind": 0,
      "label": "ok",
    }
    ws = Path("/fake/ws")
    with mock.patch.object(Path, "exists", return_value=True):
      result = workspace_git_summary(ws, ["repo1", "repo2"])
    self.assertEqual(result, "ok")

  @mock.patch("lib.git_status.compact_status")
  def test_one_dirty(self, mock_cs):
    """One dirty repo returns '*'."""
    mock_cs.return_value = {
      "dirty": True, "ahead": 0, "behind": 0,
      "label": "*",
    }
    ws = Path("/fake/ws")
    with mock.patch.object(Path, "exists", return_value=True):
      result = workspace_git_summary(ws, ["repo1"])
    self.assertEqual(result, "*")

  @mock.patch("lib.git_status.compact_status")
  def test_multiple_dirty(self, mock_cs):
    """Multiple dirty repos returns 'N*'."""
    mock_cs.return_value = {
      "dirty": True, "ahead": 0, "behind": 0,
      "label": "*",
    }
    ws = Path("/fake/ws")
    with mock.patch.object(Path, "exists", return_value=True):
      result = workspace_git_summary(
        ws, ["repo1", "repo2", "repo3"]
      )
    self.assertEqual(result, "3*")

  @mock.patch("lib.git_status.compact_status")
  def test_ahead_aggregated(self, mock_cs):
    """Ahead counts aggregate across repos."""
    mock_cs.side_effect = [
      {"dirty": False, "ahead": 2, "behind": 0,
       "label": "+2"},
      {"dirty": False, "ahead": 3, "behind": 0,
       "label": "+3"},
    ]
    ws = Path("/fake/ws")
    with mock.patch.object(Path, "exists", return_value=True):
      result = workspace_git_summary(
        ws, ["repo1", "repo2"]
      )
    self.assertEqual(result, "+5")

  @mock.patch("lib.git_status.compact_status")
  def test_mixed_summary(self, mock_cs):
    """Mixed dirty + ahead returns 'N* +M'."""
    mock_cs.side_effect = [
      {"dirty": True, "ahead": 1, "behind": 0,
       "label": "*+1"},
      {"dirty": True, "ahead": 2, "behind": 1,
       "label": "*+2-1"},
    ]
    ws = Path("/fake/ws")
    with mock.patch.object(Path, "exists", return_value=True):
      result = workspace_git_summary(
        ws, ["repo1", "repo2"]
      )
    self.assertEqual(result, "2* +3 -1")


if __name__ == "__main__":
  unittest.main()
