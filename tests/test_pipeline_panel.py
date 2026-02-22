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
        "entity": "step 1",
        "transition": "queued -> running",
      })
    self.assertEqual(len(panel._events), MAX_EVENTS)

  def test_events_ordered_newest_first(self):
    """Events are prepended (newest first)."""
    panel = PipelinePanel()
    panel._events.appendleft({
      "time": "10:00:00",
      "entity": "step 1",
      "transition": "pending -> queued",
    })
    panel._events.appendleft({
      "time": "10:01:00",
      "entity": "step 2",
      "transition": "queued -> running",
    })
    self.assertEqual(
      panel._events[0]["time"], "10:01:00"
    )
    self.assertEqual(
      panel._events[1]["time"], "10:00:00"
    )

  def test_event_fields(self):
    """Events contain expected fields."""
    panel = PipelinePanel()
    panel._events.appendleft({
      "time": "12:00:00",
      "entity": "run 1",
      "transition": "queued -> running",
    })
    ev = panel._events[0]
    self.assertEqual(ev["time"], "12:00:00")
    self.assertEqual(ev["entity"], "run 1")
    self.assertEqual(
      ev["transition"], "queued -> running"
    )


class TestPipelinePanelServiceEvents(unittest.TestCase):
  """Tests for on_service_event handling."""

  def _make_panel(self):
    panel = PipelinePanel()
    panel.query_one = mock.Mock()
    panel.query_one.return_value = mock.Mock()
    return panel

  def test_service_event_prepended(self):
    """Service events are prepended to the deque."""
    panel = self._make_panel()
    panel._events.appendleft({
      "time": "10:00:00",
      "entity": "ws1",
      "transition": "old",
    })
    panel.on_service_event({
      "time": "10:01:00",
      "workspace": "ws2",
      "event": "run_created",
    })
    self.assertEqual(len(panel._events), 2)
    self.assertEqual(
      panel._events[0]["entity"], "ws2"
    )

  def test_service_event_adds_time(self):
    """Events without time get one added."""
    panel = self._make_panel()
    panel.on_service_event({
      "workspace": "ws1",
      "event": "triggered",
    })
    self.assertIn("time", panel._events[0])

  def test_service_event_preserves_time(self):
    """Events with time keep their timestamp."""
    panel = self._make_panel()
    panel.on_service_event({
      "time": "12:34:56",
      "workspace": "ws1",
      "event": "triggered",
    })
    self.assertEqual(
      panel._events[0]["time"], "12:34:56"
    )

  def test_multiple_service_events(self):
    """Multiple events accumulate correctly."""
    panel = self._make_panel()
    for i in range(5):
      panel.on_service_event({
        "time": f"10:{i:02d}:00",
        "workspace": f"ws-{i}",
        "event": "triggered",
      })
    self.assertEqual(len(panel._events), 5)
    self.assertEqual(
      panel._events[0]["entity"], "ws-4"
    )

  def test_service_events_respect_max(self):
    """Service events respect MAX_EVENTS limit."""
    panel = self._make_panel()
    for i in range(MAX_EVENTS + 5):
      panel.on_service_event({
        "time": f"{i:02d}:00:00",
        "workspace": f"ws-{i}",
        "event": "triggered",
      })
    self.assertEqual(len(panel._events), MAX_EVENTS)


if __name__ == "__main__":
  unittest.main()
