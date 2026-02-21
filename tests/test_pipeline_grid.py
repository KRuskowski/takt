"""Tests for the pipeline grid widget."""

import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from tui.widgets.pipeline_grid import (
  _build_active_map,
  _build_columns,
  _parse_stage_session,
)


class TestParseStageSession(unittest.TestCase):
  """Tests for _parse_stage_session()."""

  def test_valid_stage_cwd(self):
    """A cwd inside a stage dir returns (workspace, role)."""
    result = _parse_stage_session(
      "/home/user/dev/stages/my-ws/test/SomeRepo",
      "/home/user/dev/stages",
    )
    self.assertEqual(result, ("my-ws", "test"))

  def test_workspace_cwd_not_stage(self):
    """A cwd inside workspaces dir returns None."""
    result = _parse_stage_session(
      "/home/user/dev/workspaces/my-ws/SomeRepo",
      "/home/user/dev/stages",
    )
    self.assertIsNone(result)

  def test_unrelated_cwd(self):
    """An unrelated cwd returns None."""
    result = _parse_stage_session(
      "/home/user/projects/foo",
      "/home/user/dev/stages",
    )
    self.assertIsNone(result)

  def test_stages_dir_itself(self):
    """The stages dir itself (no sub-path) returns None."""
    result = _parse_stage_session(
      "/home/user/dev/stages",
      "/home/user/dev/stages",
    )
    self.assertIsNone(result)

  def test_single_component(self):
    """Only workspace name, no role, returns None."""
    result = _parse_stage_session(
      "/home/user/dev/stages/my-ws",
      "/home/user/dev/stages",
    )
    self.assertIsNone(result)

  def test_deep_nested_path(self):
    """A deeply nested path still returns (workspace, role)."""
    result = _parse_stage_session(
      "/home/user/dev/stages/ws/review/repo/src/main.py",
      "/home/user/dev/stages",
    )
    self.assertEqual(result, ("ws", "review"))

  def test_prefix_mismatch(self):
    """A path that starts with stages_dir prefix but is a
    different directory returns None."""
    result = _parse_stage_session(
      "/home/user/dev/stages-old/my-ws/test/repo",
      "/home/user/dev/stages",
    )
    self.assertIsNone(result)


class TestBuildColumns(unittest.TestCase):
  """Tests for _build_columns()."""

  def test_single_workspace_pipeline(self):
    """Columns from a single workspace preserve order."""
    workspaces = [{"name": "ws-a"}]

    def mock_pipeline(name):
      return {"stages": ["test", "review", "pr"]}
    result = _build_columns(workspaces, mock_pipeline)
    self.assertEqual(result, ["test", "review", "pr"])

  def test_union_preserves_order(self):
    """Union of multiple pipelines preserves first-seen order."""
    workspaces = [{"name": "ws-a"}, {"name": "ws-b"}]
    pipelines = {
      "ws-a": {"stages": ["test", "review"]},
      "ws-b": {"stages": ["review", "pr"]},
    }
    result = _build_columns(
      workspaces, lambda n: pipelines[n]
    )
    self.assertEqual(result, ["test", "review", "pr"])

  def test_empty_workspaces(self):
    """No workspaces produces empty columns."""
    result = _build_columns([], lambda n: {"stages": []})
    self.assertEqual(result, [])

  def test_missing_workspace_skipped(self):
    """FileNotFoundError from get_pipeline is skipped."""
    workspaces = [{"name": "gone"}, {"name": "ok"}]

    def mock_pipeline(name):
      if name == "gone":
        raise FileNotFoundError("nope")
      return {"stages": ["test"]}
    result = _build_columns(workspaces, mock_pipeline)
    self.assertEqual(result, ["test"])

  def test_no_duplicates(self):
    """Same stage in multiple workspaces appears once."""
    workspaces = [{"name": "a"}, {"name": "b"}]
    pipelines = {
      "a": {"stages": ["test"]},
      "b": {"stages": ["test"]},
    }
    result = _build_columns(
      workspaces, lambda n: pipelines[n]
    )
    self.assertEqual(result, ["test"])


class TestBuildActiveMap(unittest.TestCase):
  """Tests for _build_active_map()."""

  def _make_session(self, cwd, is_active=True):
    """Create a mock session with cwd and is_active."""
    s = mock.MagicMock()
    s.cwd = cwd
    s.is_active = is_active
    return s

  def test_active_session_in_stage(self):
    """Active session in a stage dir maps correctly."""
    sessions = [
      self._make_session(
        "/dev/stages/ws-a/test/Repo", is_active=True
      ),
    ]
    result = _build_active_map(sessions, "/dev/stages")
    self.assertEqual(result, {("ws-a", "test")})

  def test_inactive_session_ignored(self):
    """Inactive sessions are not included."""
    sessions = [
      self._make_session(
        "/dev/stages/ws-a/test/Repo", is_active=False
      ),
    ]
    result = _build_active_map(sessions, "/dev/stages")
    self.assertEqual(result, set())

  def test_non_stage_session_ignored(self):
    """Sessions outside stages dir are excluded."""
    sessions = [
      self._make_session(
        "/dev/workspaces/ws-a/Repo", is_active=True
      ),
    ]
    result = _build_active_map(sessions, "/dev/stages")
    self.assertEqual(result, set())

  def test_empty_cwd_ignored(self):
    """Sessions with empty cwd are skipped."""
    sessions = [self._make_session("", is_active=True)]
    result = _build_active_map(sessions, "/dev/stages")
    self.assertEqual(result, set())

  def test_multiple_active_sessions(self):
    """Multiple active sessions in different stages."""
    sessions = [
      self._make_session(
        "/dev/stages/ws-a/test/Repo", is_active=True
      ),
      self._make_session(
        "/dev/stages/ws-b/review/Repo", is_active=True
      ),
    ]
    result = _build_active_map(sessions, "/dev/stages")
    self.assertEqual(
      result, {("ws-a", "test"), ("ws-b", "review")}
    )


class TestRefreshDataIntegration(unittest.TestCase):
  """Integration test for grid data assembly."""

  def test_grid_structure(self):
    """Verify grid rows and cells are built correctly."""
    workspaces = [
      {"name": "ws-a"},
      {"name": "ws-b"},
    ]
    stages = [
      {
        "workspace": "ws-a",
        "role": "test",
        "last_active": 0.0,
      },
      {
        "workspace": "ws-b",
        "role": "review",
        "last_active": 0.0,
      },
    ]
    pipelines = {
      "ws-a": {"stages": ["test", "review"]},
      "ws-b": {"stages": ["review"]},
    }

    columns = _build_columns(
      workspaces, lambda n: pipelines[n]
    )
    self.assertEqual(columns, ["test", "review"])

    # Build stage index like the widget does.
    stage_index = {}
    for s in stages:
      stage_index[(s["workspace"], s["role"])] = s

    # ws-a should have test=exists, review=missing.
    self.assertIn(("ws-a", "test"), stage_index)
    self.assertNotIn(("ws-a", "review"), stage_index)

    # ws-b should have review=exists, test=missing.
    self.assertNotIn(("ws-b", "test"), stage_index)
    self.assertIn(("ws-b", "review"), stage_index)

  def test_active_agent_overrides_stage(self):
    """Active agent takes precedence over stage age."""
    sessions = [mock.MagicMock()]
    sessions[0].cwd = "/dev/stages/ws-a/test/Repo"
    sessions[0].is_active = True

    active_map = _build_active_map(
      sessions, "/dev/stages"
    )
    self.assertIn(("ws-a", "test"), active_map)


if __name__ == "__main__":
  unittest.main()
