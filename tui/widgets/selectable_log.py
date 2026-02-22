"""RichLog subclass with text selection and copy support.

Textual's RichLog does not implement get_selection(), so
Ctrl+C / Cmd+C silently copies nothing.  This subclass adds
selection extraction from the stored Strip objects and
visual highlighting of the selected range.
"""

from rich.segment import Segment
from rich.style import Style

from textual.selection import Selection
from textual.strip import Strip
from textual.widgets import RichLog


class SelectableLog(RichLog):
  """RichLog with working text selection and copy."""

  def get_selection(
    self, selection: Selection
  ) -> tuple[str, str] | None:
    """Extract selected text from the log.

    Args:
      selection: Current selection state.

    Returns:
      Tuple of (selected_text, line_ending) or None.
    """
    text = "\n".join(
      strip.text for strip in self.lines
    )
    return selection.extract(text), "\n"

  def selection_updated(
    self, selection: Selection | None
  ) -> None:
    """Clear render cache when selection changes.

    Args:
      selection: New selection state, or None.
    """
    self._line_cache.clear()
    self.refresh()

  def _render_line(
    self, y: int, scroll_x: int, width: int
  ) -> Strip:
    """Render a line with offset metadata and selection.

    The parent RichLog._render_line never calls
    Strip.apply_offsets(), so the compositor can't map
    mouse clicks to text positions. This override adds
    that call, enabling click-drag selection, plus
    visual highlighting of the selected range.

    Args:
      y: Line index in the virtual list.
      scroll_x: Current horizontal scroll offset.
      width: Available render width.

    Returns:
      Rendered Strip for the line.
    """
    if y >= len(self.lines):
      return Strip.blank(width, self.rich_style)
    selection = self.text_selection
    key = (
      y + self._start_line,
      scroll_x, width,
      self._widest_line_width,
    )
    if key in self._line_cache and selection is None:
      return self._line_cache[key]
    line = self.lines[y]
    if selection is not None:
      span = selection.get_span(y)
      if span is not None:
        start, end = span
        if end == -1:
          end = line.cell_length
        sel_style = (
          self.screen.get_component_rich_style(
            "screen--selection"
          )
        )
        line = _apply_selection(
          line, start, end, sel_style
        )
    line = line.crop_extend(
      scroll_x, scroll_x + width, self.rich_style
    )
    # Inject offset metadata so the compositor can
    # map mouse coordinates to text positions.
    line = line.apply_offsets(scroll_x, y)
    if selection is None:
      self._line_cache[key] = line
    return line


def _apply_selection(
  strip: Strip, start: int, end: int, style: Style
) -> Strip:
  """Apply selection style to a character range.

  Args:
    strip: Source strip.
    start: Selection start (cell offset).
    end: Selection end (cell offset).
    style: Selection highlight style.

  Returns:
    New Strip with selection styling applied.
  """
  if start >= end or start >= strip.cell_length:
    return strip
  end = min(end, strip.cell_length)
  # Build cut points to split into up to 3 parts:
  # before (if start > 0), selected, after (if end < length).
  cuts = []
  if start > 0:
    cuts.append(start)
  cuts.append(end)
  if end < strip.cell_length:
    cuts.append(strip.cell_length)
  parts = strip.divide(cuts)
  segments = []
  idx = 0
  if start > 0:
    segments.extend(list(parts[idx]))
    idx += 1
  segments.extend(
    Segment.apply_style(list(parts[idx]), style)
  )
  idx += 1
  if idx < len(parts):
    segments.extend(list(parts[idx]))
  return Strip(segments, strip.cell_length)
