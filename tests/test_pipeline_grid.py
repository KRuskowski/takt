"""Tests for the pipeline grid widget."""

import unittest

from tui.widgets.pipeline_grid import (
  _build_pipelines,
  _build_flow,
  _node_state,
  STATE_FAILED,
  STATE_MISSING,
  STATE_PASSED,
  STATE_PAUSED,
  STATE_RUNNING,
  STATE_SKIPPED,
  STATE_STALE,
  STATE_TRIGGERED,
)


class TestBuildPipelines(unittest.TestCase):
  """Tests for _build_pipelines()."""

  def test_single_workspace(self):
    """Steps from a single workspace preserve order."""
    workspaces = [{"name": "ws-a"}]

    def mock_pipeline(name):
      return [
        {"name": "test"},
        {"name": "push_to_github"},
      ]
    result = _build_pipelines(workspaces, mock_pipeline)
    self.assertEqual(
      result, {"ws-a": ["test", "push_to_github"]}
    )

  def test_multiple_workspaces_independent(self):
    """Each workspace gets its own step list."""
    workspaces = [{"name": "ws-a"}, {"name": "ws-b"}]
    pipelines = {
      "ws-a": [
        {"name": "test"}, {"name": "review"},
      ],
      "ws-b": [
        {"name": "review"}, {"name": "create_pr"},
      ],
    }
    result = _build_pipelines(
      workspaces, lambda n: pipelines[n]
    )
    self.assertEqual(
      result["ws-a"], ["test", "review"]
    )
    self.assertEqual(
      result["ws-b"], ["review", "create_pr"]
    )

  def test_empty_workspaces(self):
    """No workspaces produces empty dict."""
    result = _build_pipelines([], lambda n: [])
    self.assertEqual(result, {})

  def test_no_pipeline(self):
    """Workspace with no pipeline returns empty list."""
    workspaces = [{"name": "a"}]
    result = _build_pipelines(
      workspaces, lambda n: []
    )
    self.assertEqual(result, {"a": []})


class TestNodeState(unittest.TestCase):
  """Tests for _node_state()."""

  def test_no_pipeline(self):
    """No pipeline returns STATE_MISSING."""
    result = _node_state("test", {}, False)
    self.assertEqual(result, STATE_MISSING)

  def test_step_not_in_pipeline(self):
    """Step not in map returns STATE_MISSING."""
    result = _node_state("review", {"test": "completed"},
                         True)
    self.assertEqual(result, STATE_MISSING)

  def test_pending(self):
    """Pending step returns STATE_STALE."""
    result = _node_state("test", {"test": "pending"},
                         True)
    self.assertEqual(result, STATE_STALE)

  def test_queued(self):
    """Queued step returns STATE_TRIGGERED."""
    result = _node_state("test", {"test": "queued"},
                         True)
    self.assertEqual(result, STATE_TRIGGERED)

  def test_running(self):
    """Running step returns STATE_RUNNING."""
    result = _node_state("test", {"test": "running"},
                         True)
    self.assertEqual(result, STATE_RUNNING)

  def test_completed(self):
    """Completed step returns STATE_PASSED."""
    result = _node_state("test", {"test": "completed"},
                         True)
    self.assertEqual(result, STATE_PASSED)

  def test_failed(self):
    """Failed step returns STATE_FAILED."""
    result = _node_state("test", {"test": "failed"},
                         True)
    self.assertEqual(result, STATE_FAILED)

  def test_paused(self):
    """Paused step returns STATE_PAUSED."""
    result = _node_state("test", {"test": "paused"},
                         True)
    self.assertEqual(result, STATE_PAUSED)

  def test_skipped(self):
    """Skipped step returns STATE_SKIPPED."""
    result = _node_state("test", {"test": "skipped"},
                         True)
    self.assertEqual(result, STATE_SKIPPED)

  def test_cancelled(self):
    """Cancelled step returns STATE_FAILED."""
    result = _node_state("test", {"test": "cancelled"},
                         True)
    self.assertEqual(result, STATE_FAILED)


class TestBuildFlow(unittest.TestCase):
  """Tests for _build_flow()."""

  def test_no_workspaces(self):
    """Empty workspace list produces empty text."""
    result = _build_flow([], {}, {})
    self.assertEqual(result.plain, "")

  def test_all_passed(self):
    """All completed steps show check icons."""
    workspaces = [{"name": "ws-a"}]
    pipelines = {"ws-a": ["test", "push"]}
    step_maps = {
      "ws-a": {
        "test": "completed",
        "push": "completed",
      },
    }
    result = _build_flow(
      workspaces, pipelines, step_maps
    )
    plain = result.plain
    self.assertIn("✓ test", plain)
    self.assertIn("✓ push", plain)

  def test_failed_step(self):
    """Failed step shows cross icon."""
    workspaces = [{"name": "ws-a"}]
    pipelines = {"ws-a": ["test"]}
    step_maps = {"ws-a": {"test": "failed"}}
    result = _build_flow(
      workspaces, pipelines, step_maps
    )
    self.assertIn("✗ test", result.plain)

  def test_running_step(self):
    """Running step shows dot icon."""
    workspaces = [{"name": "ws-a"}]
    pipelines = {"ws-a": ["test"]}
    step_maps = {"ws-a": {"test": "running"}}
    result = _build_flow(
      workspaces, pipelines, step_maps
    )
    self.assertIn("● test", result.plain)

  def test_no_pipeline(self):
    """Workspace without pipeline shows label."""
    workspaces = [{"name": "ws-a"}]
    pipelines = {"ws-a": []}
    result = _build_flow(workspaces, pipelines, {})
    plain = result.plain
    self.assertIn("(no pipeline)", plain)

  def test_arrow_chain_ends_with_root(self):
    """The middle row ends with root."""
    workspaces = [{"name": "ws-a"}]
    pipelines = {"ws-a": ["test", "push"]}
    result = _build_flow(workspaces, pipelines, {})
    plain = result.plain
    lines = plain.split("\n")
    root_lines = [ln for ln in lines if "root" in ln]
    self.assertEqual(len(root_lines), 1)
    self.assertTrue(
      root_lines[0].rstrip().endswith("root")
    )

  def test_multiple_workspaces_separated(self):
    """Multiple workspaces are separated by blank line."""
    workspaces = [{"name": "ws-a"}, {"name": "ws-b"}]
    pipelines = {"ws-a": ["test"], "ws-b": ["review"]}
    result = _build_flow(workspaces, pipelines, {})
    plain = result.plain
    self.assertIn("ws-a", plain)
    self.assertIn("ws-b", plain)
    lines = plain.split("\n")
    blank_indices = [
      i for i, line in enumerate(lines)
      if line == "" and 0 < i < len(lines) - 1
    ]
    self.assertGreater(len(blank_indices), 0)

  def test_per_workspace_order(self):
    """Each workspace shows its own step order."""
    workspaces = [{"name": "ws-a"}, {"name": "ws-b"}]
    pipelines = {
      "ws-a": ["test", "review"],
      "ws-b": ["review", "test"],
    }
    step_maps = {
      "ws-a": {"test": "completed", "review": "running"},
      "ws-b": {"review": "completed", "test": "failed"},
    }
    result = _build_flow(
      workspaces, pipelines, step_maps
    )
    plain = result.plain
    lines = plain.split("\n")
    # Find middle lines containing step names.
    ws_a_line = [
      ln for ln in lines if "ws-a" in ln
    ][0]
    ws_b_line = [
      ln for ln in lines if "ws-b" in ln
    ][0]
    # ws-a has test before review.
    self.assertLess(
      ws_a_line.index("test"),
      ws_a_line.index("review"),
    )
    # ws-b has review before test.
    self.assertLess(
      ws_b_line.index("review"),
      ws_b_line.index("test"),
    )

  def test_flow_line_count(self):
    """Workspace with pipeline produces 3 content lines."""
    workspaces = [{"name": "ws-a"}]
    pipelines = {"ws-a": ["test"]}
    result = _build_flow(workspaces, pipelines, {})
    lines = result.plain.split("\n")
    non_empty = [ln for ln in lines if ln.strip()]
    self.assertEqual(len(non_empty), 3)

  def test_no_pipeline_single_line(self):
    """Workspace without pipeline produces 1 line."""
    workspaces = [{"name": "ws-a"}]
    pipelines = {"ws-a": []}
    result = _build_flow(workspaces, pipelines, {})
    lines = result.plain.split("\n")
    non_empty = [ln for ln in lines if ln.strip()]
    self.assertEqual(len(non_empty), 1)

  def test_name_on_middle_line(self):
    """Workspace name appears on the middle line."""
    workspaces = [{"name": "ws-a"}]
    pipelines = {"ws-a": ["test"]}
    result = _build_flow(workspaces, pipelines, {})
    lines = result.plain.split("\n")
    non_empty = [ln for ln in lines if ln.strip()]
    middle = non_empty[1]
    self.assertIn("ws-a", middle)
    self.assertTrue("│" in middle or "┊" in middle)

  def test_name_column_alignment(self):
    """Names are padded to align boxes."""
    workspaces = [
      {"name": "a"}, {"name": "long-name"},
    ]
    pipelines = {"a": ["test"], "long-name": ["test"]}
    result = _build_flow(workspaces, pipelines, {})
    lines = result.plain.split("\n")
    name_lines = [
      ln for ln in lines
      if ("│" in ln or "┊" in ln)
    ]
    self.assertEqual(len(name_lines), 2)

    def border_pos(ln):
      for i, ch in enumerate(ln):
        if ch in ("│", "┊"):
          return i
      return -1
    pos0 = border_pos(name_lines[0])
    pos1 = border_pos(name_lines[1])
    self.assertEqual(pos0, pos1)

  def test_single_step(self):
    """A single step still renders a complete box."""
    workspaces = [{"name": "ws-a"}]
    pipelines = {"ws-a": ["test"]}
    step_maps = {"ws-a": {"test": "completed"}}
    result = _build_flow(
      workspaces, pipelines, step_maps
    )
    plain = result.plain
    self.assertIn("╭", plain)
    self.assertIn("╰", plain)
    self.assertIn("test", plain)
    self.assertIn("root", plain)


class TestNodeStateDisplayMapping(unittest.TestCase):
  """Tests for all step status display mappings."""

  def test_paused_shows_bars(self):
    """Paused step shows ║ icon."""
    workspaces = [{"name": "ws-a"}]
    pipelines = {"ws-a": ["test"]}
    step_maps = {"ws-a": {"test": "paused"}}
    result = _build_flow(
      workspaces, pipelines, step_maps
    )
    self.assertIn("║ test", result.plain)

  def test_skipped_shows_dash(self):
    """Skipped step shows — icon."""
    workspaces = [{"name": "ws-a"}]
    pipelines = {"ws-a": ["test"]}
    step_maps = {"ws-a": {"test": "skipped"}}
    result = _build_flow(
      workspaces, pipelines, step_maps
    )
    self.assertIn("— test", result.plain)

  def test_triggered_shows_arrow(self):
    """Queued step shows ▸ icon."""
    workspaces = [{"name": "ws-a"}]
    pipelines = {"ws-a": ["test"]}
    step_maps = {"ws-a": {"test": "queued"}}
    result = _build_flow(
      workspaces, pipelines, step_maps
    )
    self.assertIn("▸ test", result.plain)


if __name__ == "__main__":
  unittest.main()
