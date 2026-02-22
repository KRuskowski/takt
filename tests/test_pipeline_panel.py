"""Tests for PipelinePanel widget."""

import sys
import unittest
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
_vertical_cls = type(
  "Vertical", (), {"__init__": lambda s, **kw: None},
)
sys.modules["textual.containers"].Vertical = _vertical_cls

# Provide a passthrough @work decorator.
sys.modules["textual"].work = lambda **kw: (lambda fn: fn)

from tui.widgets.pipeline import MAX_EVENTS, PipelinePanel


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
  """Tests for refresh_data with mocked scan functions."""

  @mock.patch("tui.widgets.pipeline._scan_and_sync",
              return_value=[])
  @mock.patch("tui.widgets.pipeline._scan_and_trigger",
              return_value=[])
  @mock.patch("tui.widgets.pipeline._prune_finished_tabs",
              return_value=[])
  def test_noop_no_events(self, mock_prune, mock_trigger,
                          mock_sync):
    """No events logged when nothing happens."""
    panel = PipelinePanel()
    panel.refresh_data()
    mock_prune.assert_called_once()
    mock_trigger.assert_called_once()
    mock_sync.assert_called_once()
    self.assertEqual(len(panel._events), 0)

  @mock.patch("tui.widgets.pipeline._scan_and_sync",
              return_value=[])
  @mock.patch("tui.widgets.pipeline._scan_and_trigger",
              return_value=[{
                "stage": "ws/test",
                "repos": "myrepo",
                "event": "triggered",
              }])
  @mock.patch("tui.widgets.pipeline._prune_finished_tabs",
              return_value=[])
  def test_trigger_logs_event(self, mock_prune, mock_trigger,
                              mock_sync):
    """Trigger events are logged."""
    panel = PipelinePanel()
    panel.app = mock.Mock()
    panel.refresh_data()
    self.assertEqual(len(panel._events), 1)
    self.assertEqual(
      panel._events[0]["event"], "triggered",
    )
    self.assertEqual(
      panel._events[0]["stage"], "ws/test",
    )

  @mock.patch("tui.widgets.pipeline._scan_and_sync",
              return_value=[])
  @mock.patch("tui.widgets.pipeline._scan_and_trigger",
              return_value=[
                {"stage": "ws/test", "repos": "a",
                 "event": "triggered"},
                {"stage": "ws/review", "repos": "b",
                 "event": "triggered"},
              ])
  @mock.patch("tui.widgets.pipeline._prune_finished_tabs",
              return_value=[])
  def test_multiple_events(self, mock_prune, mock_trigger,
                           mock_sync):
    """Multiple trigger events produce multiple log entries."""
    panel = PipelinePanel()
    panel.app = mock.Mock()
    panel.refresh_data()
    self.assertEqual(len(panel._events), 2)
    stages = {ev["stage"] for ev in panel._events}
    self.assertEqual(stages, {"ws/test", "ws/review"})

  @mock.patch("tui.widgets.pipeline._scan_and_sync",
              return_value=[])
  @mock.patch("tui.widgets.pipeline._scan_and_trigger",
              return_value=[])
  @mock.patch("tui.widgets.pipeline._prune_finished_tabs",
              return_value=["ws/test"])
  def test_prune_logs_event(self, mock_prune, mock_trigger,
                            mock_sync):
    """Pruned tabs are logged."""
    panel = PipelinePanel()
    panel.app = mock.Mock()
    panel.refresh_data()
    self.assertEqual(len(panel._events), 1)
    self.assertEqual(panel._events[0]["event"], "pruned")
    self.assertEqual(panel._events[0]["stage"], "ws/test")

  @mock.patch("tui.widgets.pipeline._scan_and_sync",
              return_value=[{
                "stage": "ws/sync",
                "repos": "myrepo",
                "event": "triggered",
              }])
  @mock.patch("tui.widgets.pipeline._scan_and_trigger",
              return_value=[])
  @mock.patch("tui.widgets.pipeline._prune_finished_tabs",
              return_value=[])
  def test_sync_logs_event(self, mock_prune, mock_trigger,
                           mock_sync):
    """Sync events are logged."""
    panel = PipelinePanel()
    panel.app = mock.Mock()
    panel.refresh_data()
    self.assertEqual(len(panel._events), 1)
    self.assertEqual(
      panel._events[0]["event"], "triggered",
    )
    self.assertEqual(
      panel._events[0]["stage"], "ws/sync",
    )


class TestPipelinePanelErrors(unittest.TestCase):
  """Tests for error visibility in refresh_data."""

  @mock.patch("tui.widgets.pipeline._scan_and_sync",
              return_value=[])
  @mock.patch("tui.widgets.pipeline._scan_and_trigger",
              return_value=[])
  @mock.patch("tui.widgets.pipeline._prune_finished_tabs",
              side_effect=RuntimeError("socket gone"))
  def test_prune_error_logged(self, mock_prune, mock_trigger,
                              mock_sync):
    """Prune exception becomes a system error event."""
    panel = PipelinePanel()
    panel.app = mock.Mock()
    panel.refresh_data()
    self.assertEqual(len(panel._events), 1)
    self.assertEqual(panel._events[0]["stage"], "system")
    self.assertIn("socket gone", panel._events[0]["event"])

  @mock.patch("tui.widgets.pipeline._scan_and_sync",
              return_value=[])
  @mock.patch("tui.widgets.pipeline._scan_and_trigger",
              side_effect=RuntimeError("trigger fail"))
  @mock.patch("tui.widgets.pipeline._prune_finished_tabs",
              return_value=[])
  def test_trigger_error_logged(self, mock_prune,
                                mock_trigger, mock_sync):
    """Trigger exception becomes a system error event."""
    panel = PipelinePanel()
    panel.app = mock.Mock()
    panel.refresh_data()
    self.assertEqual(len(panel._events), 1)
    self.assertEqual(panel._events[0]["stage"], "system")
    self.assertIn("trigger fail", panel._events[0]["event"])

  @mock.patch("tui.widgets.pipeline._scan_and_sync",
              side_effect=RuntimeError("sync fail"))
  @mock.patch("tui.widgets.pipeline._scan_and_trigger",
              return_value=[])
  @mock.patch("tui.widgets.pipeline._prune_finished_tabs",
              return_value=[])
  def test_sync_error_logged(self, mock_prune, mock_trigger,
                             mock_sync):
    """Sync exception becomes a system error event."""
    panel = PipelinePanel()
    panel.app = mock.Mock()
    panel.refresh_data()
    self.assertEqual(len(panel._events), 1)
    self.assertEqual(panel._events[0]["stage"], "system")
    self.assertIn("sync fail", panel._events[0]["event"])


if __name__ == "__main__":
  unittest.main()
