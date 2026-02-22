"""Tests for SelectableLog widget."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from rich.segment import Segment
from rich.style import Style

from textual.strip import Strip

from tui.widgets.selectable_log import (
  SelectableLog,
  _apply_selection,
)


class TestApplySelection(unittest.TestCase):
  """Tests for _apply_selection() helper."""

  def _make_strip(self, text):
    """Create a Strip from plain text."""
    segs = [Segment(text)]
    return Strip(segs, len(text))

  def test_no_overlap(self):
    """Selection past end returns original strip."""
    strip = self._make_strip("hello")
    result = _apply_selection(
      strip, 10, 15, Style(bold=True)
    )
    self.assertEqual(result.text, "hello")

  def test_full_selection(self):
    """Selecting entire strip applies style."""
    strip = self._make_strip("hello")
    style = Style(bold=True)
    result = _apply_selection(strip, 0, 5, style)
    self.assertEqual(result.text, "hello")
    self.assertEqual(result.cell_length, 5)

  def test_partial_selection(self):
    """Selecting middle of strip preserves text."""
    strip = self._make_strip("abcdef")
    result = _apply_selection(
      strip, 2, 4, Style(bold=True)
    )
    self.assertEqual(result.text, "abcdef")
    self.assertEqual(result.cell_length, 6)

  def test_empty_range(self):
    """Zero-width selection returns original."""
    strip = self._make_strip("hello")
    result = _apply_selection(
      strip, 2, 2, Style(bold=True)
    )
    self.assertEqual(result.text, "hello")

  def test_end_clamped(self):
    """Selection end beyond strip length is clamped."""
    strip = self._make_strip("hi")
    result = _apply_selection(
      strip, 0, 100, Style(bold=True)
    )
    self.assertEqual(result.text, "hi")


class TestSelectableLogMethods(unittest.TestCase):
  """Tests for SelectableLog method signatures."""

  def test_has_get_selection(self):
    """SelectableLog defines get_selection."""
    self.assertTrue(
      hasattr(SelectableLog, "get_selection")
    )

  def test_has_selection_updated(self):
    """SelectableLog defines selection_updated."""
    self.assertTrue(
      hasattr(SelectableLog, "selection_updated")
    )

  def test_get_selection_extracts_text(self):
    """get_selection returns text from strips."""
    log = SelectableLog.__new__(SelectableLog)
    log.lines = [
      Strip([Segment("line one")], 8),
      Strip([Segment("line two")], 8),
    ]
    mock_sel = MagicMock()
    mock_sel.extract.return_value = "line one\nline two"
    result = log.get_selection(mock_sel)
    self.assertIsNotNone(result)
    text, ending = result
    self.assertEqual(text, "line one\nline two")
    self.assertEqual(ending, "\n")
    mock_sel.extract.assert_called_once_with(
      "line one\nline two"
    )


if __name__ == "__main__":
  unittest.main()
