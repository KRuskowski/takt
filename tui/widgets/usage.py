"""Usage panel widget."""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Sparkline, Static
from textual import work


def _format_tokens(n):
  """Format a token count to a human-readable string."""
  if n >= 1_000_000_000:
    return f"{n / 1_000_000_000:.1f}B"
  if n >= 1_000_000:
    return f"{n / 1_000_000:.1f}M"
  if n >= 1_000:
    return f"{n / 1_000:.0f}K"
  return str(n)


class UsagePanel(Vertical):
  """Panel showing aggregated usage stats and sparkline."""

  DEFAULT_CSS = """
  UsagePanel {
    border: solid $accent;
    padding: 0 1;
  }
  """

  def compose(self) -> ComposeResult:
    yield Static("Usage", classes="panel-title")
    yield Static(
      "Loading...", id="usage-summary"
    )
    yield Sparkline([], id="usage-sparkline")

  @work(thread=True)
  def refresh_data(self) -> None:
    """Load usage stats in a worker thread."""
    from lib.session_parser import load_stats_cache
    summary = load_stats_cache()
    self.app.call_from_thread(self._update_display, summary)

  def _update_display(self, summary) -> None:
    """Update the usage display with fresh data."""
    lines = []
    lines.append(
      f"Sessions: {summary.total_sessions}  "
      f"Messages: {summary.total_messages}"
    )

    for model_id, mu in summary.by_model.items():
      # Short model name.
      short = model_id.split("-")[1] if "-" in model_id else model_id
      short = short[:6]
      total = mu.input_tokens + mu.output_tokens
      lines.append(
        f"  {short}: {_format_tokens(total)} tok  "
        f"${mu.cost_usd:.2f}"
      )

    lines.append(f"Total est. cost: ${summary.total_cost_usd:.2f}")

    summary_widget = self.query_one(
      "#usage-summary", Static
    )
    summary_widget.update("\n".join(lines))

    # Update sparkline with daily message counts.
    sparkline = self.query_one(
      "#usage-sparkline", Sparkline
    )
    daily_counts = [
      float(d.get("messageCount", 0))
      for d in summary.daily_activity
    ]
    sparkline.data = daily_counts if daily_counts else [0.0]
