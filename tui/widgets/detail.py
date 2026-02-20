"""Detail pane widget — contextual right column."""

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static


class DetailPane(VerticalScroll):
  """Scrollable detail pane that updates based on selection."""

  DEFAULT_CSS = """
  DetailPane {
    border: solid $accent;
    padding: 1;
  }
  """

  def compose(self) -> ComposeResult:
    yield Static(
      "Select an item to view details.",
      id="detail-content",
    )

  def update_content(self, text: str) -> None:
    """Replace the detail pane content."""
    widget = self.query_one("#detail-content", Static)
    widget.update(text)
