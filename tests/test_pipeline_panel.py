"""Tests for PipelinePanel widget."""

import sys
import unittest
from collections import deque
from pathlib import Path
from unittest import mock

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

# Mock textual modules so tests run without the library.
_textual_mocks = {}
for mod_name in (
    "textual", "textual.app", "textual.containers",
    "textual.widgets", "textual.work",
):
  if mod_name not in sys.modules:
    _textual_mocks[mod_name] = mock.MagicMock()
    sys.modules[mod_name] = _textual_mocks[mod_name]

# Provide a real base class for Vertical so super().__init__
# works.
_vertical_cls = type("Vertical", (), {"__init__": lambda s, **kw: None})
sys.modules["textual.containers"].Vertical = _vertical_cls

# Provide a passthrough @work decorator.
sys.modules["textual"].work = lambda **kw: (lambda fn: fn)

from tui.widgets.pipeline import MAX_EVENTS, PipelinePanel


class TestPipelinePanelToggle(unittest.TestCase):
  """Tests for toggle_watching behavior."""

  def test_starts_paused(self):
    """Panel starts with watching disabled."""
    panel = PipelinePanel()
    self.assertFalse(panel.watching)

  def test_toggle_enables(self):
    """First toggle enables watching."""
    panel = PipelinePanel()
    panel._watching = False
    panel._watching = not panel._watching
    self.assertTrue(panel._watching)

  def test_toggle_disables(self):
    """Second toggle disables watching."""
    panel = PipelinePanel()
    panel._watching = True
    panel._watching = not panel._watching
    self.assertFalse(panel._watching)


class TestPipelinePanelEvents(unittest.TestCase):
  """Tests for event logging."""

  def test_event_deque_max_size(self):
    """Event log respects MAX_EVENTS limit."""
    panel = PipelinePanel()
    for i in range(MAX_EVENTS + 10):
      panel._events.appendleft({
        "time": f"{i:02d}:00:00",
        "stage": "ws/test",
        "repos": "repo",
        "event": "triggered",
      })
    self.assertEqual(len(panel._events), MAX_EVENTS)

  def test_events_ordered_newest_first(self):
    """Events are prepended (newest first)."""
    panel = PipelinePanel()
    panel._events.appendleft({
      "time": "10:00:00",
      "stage": "ws/test",
      "repos": "repo-a",
      "event": "triggered",
    })
    panel._events.appendleft({
      "time": "10:01:00",
      "stage": "ws/review",
      "repos": "repo-b",
      "event": "error",
    })
    self.assertEqual(panel._events[0]["time"], "10:01:00")
    self.assertEqual(panel._events[1]["time"], "10:00:00")

  def test_event_fields(self):
    """Events contain expected fields."""
    panel = PipelinePanel()
    panel._events.appendleft({
      "time": "12:00:00",
      "stage": "feat/test",
      "repos": "core, ui",
      "event": "triggered",
    })
    ev = panel._events[0]
    self.assertEqual(ev["time"], "12:00:00")
    self.assertEqual(ev["stage"], "feat/test")
    self.assertEqual(ev["repos"], "core, ui")
    self.assertEqual(ev["event"], "triggered")


class TestPipelinePanelRefresh(unittest.TestCase):
  """Tests for refresh_data scan/trigger integration."""

  @mock.patch("tui.widgets.pipeline.launch_in_kitty")
  @mock.patch("tui.widgets.pipeline.build_trigger_prompt",
              return_value="prompt")
  @mock.patch("tui.widgets.pipeline.scan_markers")
  def test_skips_when_paused(self, mock_scan, mock_build,
                             mock_launch):
    """Does not scan when watching is off."""
    panel = PipelinePanel()
    panel._watching = False
    panel.refresh_data()
    mock_scan.assert_not_called()

  @mock.patch("tui.widgets.pipeline.launch_in_kitty")
  @mock.patch("tui.widgets.pipeline.build_trigger_prompt",
              return_value="prompt")
  @mock.patch("tui.widgets.pipeline.scan_markers",
              return_value={})
  def test_noop_no_markers(self, mock_scan, mock_build,
                           mock_launch):
    """No events logged when no markers found."""
    panel = PipelinePanel()
    panel._watching = True
    panel.refresh_data()
    mock_scan.assert_called_once()
    mock_launch.assert_not_called()
    self.assertEqual(len(panel._events), 0)

  @mock.patch("tui.widgets.pipeline.launch_in_kitty")
  @mock.patch("tui.widgets.pipeline.build_trigger_prompt",
              return_value="prompt")
  @mock.patch("tui.widgets.pipeline.scan_markers")
  def test_trigger_logs_event(self, mock_scan, mock_build,
                              mock_launch):
    """Successful trigger logs a 'triggered' event."""
    mock_scan.return_value = {
      ("ws", "test"): [("myrepo", ["line1"])],
    }
    panel = PipelinePanel()
    panel._watching = True
    panel.app = mock.Mock()
    with mock.patch(
      "tui.widgets.pipeline.STAGES_DIR",
      Path("/tmp/fake-stages"),
    ):
      panel.refresh_data()
    self.assertEqual(len(panel._events), 1)
    self.assertEqual(panel._events[0]["event"], "triggered")
    self.assertEqual(panel._events[0]["stage"], "ws/test")
    self.assertEqual(panel._events[0]["repos"], "myrepo")

  @mock.patch("tui.widgets.pipeline.launch_in_kitty",
              side_effect=Exception("kitty failed"))
  @mock.patch("tui.widgets.pipeline.build_trigger_prompt",
              return_value="prompt")
  @mock.patch("tui.widgets.pipeline.scan_markers")
  def test_error_event_on_launch_failure(
      self, mock_scan, mock_build, mock_launch):
    """Failed kitty launch logs an 'error' event."""
    mock_scan.return_value = {
      ("ws", "test"): [("myrepo", ["line1"])],
    }
    panel = PipelinePanel()
    panel._watching = True
    panel.app = mock.Mock()
    with mock.patch(
      "tui.widgets.pipeline.STAGES_DIR",
      Path("/tmp/fake-stages"),
    ):
      panel.refresh_data()
    self.assertEqual(len(panel._events), 1)
    self.assertEqual(panel._events[0]["event"], "error")

  @mock.patch("tui.widgets.pipeline.launch_in_kitty")
  @mock.patch("tui.widgets.pipeline.build_trigger_prompt",
              return_value="prompt")
  @mock.patch("tui.widgets.pipeline.scan_markers")
  def test_multiple_stages_multiple_events(
      self, mock_scan, mock_build, mock_launch):
    """Multiple marker groups produce multiple events."""
    mock_scan.return_value = {
      ("ws", "test"): [("repo-a", ["line1"])],
      ("ws", "review"): [("repo-b", ["line2"])],
    }
    panel = PipelinePanel()
    panel._watching = True
    panel.app = mock.Mock()
    with mock.patch(
      "tui.widgets.pipeline.STAGES_DIR",
      Path("/tmp/fake-stages"),
    ):
      panel.refresh_data()
    self.assertEqual(len(panel._events), 2)
    stages = {ev["stage"] for ev in panel._events}
    self.assertEqual(stages, {"ws/test", "ws/review"})

  @mock.patch("tui.widgets.pipeline.launch_in_kitty")
  @mock.patch("tui.widgets.pipeline.build_trigger_prompt",
              return_value="prompt")
  @mock.patch("tui.widgets.pipeline.scan_markers")
  def test_deletes_markers(self, mock_scan, mock_build,
                           mock_launch):
    """Marker files are deleted after processing."""
    import shutil
    import tempfile
    tmpdir = tempfile.mkdtemp()
    try:
      stages = Path(tmpdir) / "stages"
      repo = stages / "ws" / "test" / "myrepo"
      repo.mkdir(parents=True)
      marker = repo / ".pipeline-push"
      marker.write_text("line1\n")
      mock_scan.return_value = {
        ("ws", "test"): [("myrepo", ["line1"])],
      }
      with mock.patch(
        "tui.widgets.pipeline.STAGES_DIR", stages,
      ):
        panel = PipelinePanel()
        panel._watching = True
        panel.app = mock.Mock()
        panel.refresh_data()
      self.assertFalse(marker.exists())
    finally:
      shutil.rmtree(tmpdir)

  @mock.patch("tui.widgets.pipeline.launch_in_kitty")
  @mock.patch("tui.widgets.pipeline.build_trigger_prompt",
              return_value="prompt")
  @mock.patch("tui.widgets.pipeline.scan_markers")
  def test_build_prompt_called_with_args(
      self, mock_scan, mock_build, mock_launch):
    """build_trigger_prompt receives correct arguments."""
    mock_scan.return_value = {
      ("ws", "test"): [("myrepo", ["line1"])],
    }
    panel = PipelinePanel()
    panel._watching = True
    panel.app = mock.Mock()
    with mock.patch(
      "tui.widgets.pipeline.STAGES_DIR", Path("/tmp/fake"),
    ):
      panel.refresh_data()
    mock_build.assert_called_once_with(
      "ws", "test", [("myrepo", ["line1"])],
    )


if __name__ == "__main__":
  unittest.main()
