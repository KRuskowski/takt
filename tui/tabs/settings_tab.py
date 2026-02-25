"""Settings tab — configuration and service controls."""

import yaml
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import (
  DataTable,
  Select,
  Static,
)
from textual import work

from lib.config import CONFIG_DIR

SETTINGS_PATH = CONFIG_DIR / "tui_settings.yaml"
MODELS = [
  ("sonnet", "sonnet"),
  ("opus", "opus"),
  ("haiku", "haiku"),
]


def load_settings():
  """Load TUI settings from config/tui_settings.yaml.

  Returns:
    Dict with settings. Defaults applied for missing keys.
  """
  defaults = {
    "model": "sonnet",
    "poll_interval": 30,
  }
  if not SETTINGS_PATH.exists():
    return defaults
  try:
    with open(SETTINGS_PATH) as f:
      data = yaml.safe_load(f) or {}
    defaults.update(data)
  except (yaml.YAMLError, OSError):
    pass
  return defaults


def save_settings(settings):
  """Persist TUI settings to config/tui_settings.yaml.

  Args:
    settings: Dict of settings to save.
  """
  CONFIG_DIR.mkdir(exist_ok=True)
  with open(SETTINGS_PATH, "w") as f:
    yaml.dump(settings, f, default_flow_style=False,
              sort_keys=False)


class SettingsTab(Static):
  """Configuration display and service controls."""

  BINDINGS = [
    Binding("s", "service_start", "Start"),
    Binding("x", "service_stop", "Stop"),
    Binding("r", "service_restart", "Restart"),
  ]

  DEFAULT_CSS = """
  SettingsTab {
    height: 1fr;
    padding: 1 2;
  }

  SettingsTab .settings-section {
    margin: 1 0;
  }

  SettingsTab .settings-label {
    text-style: bold;
    color: #cccccc;
    margin: 0 0 1 0;
  }

  SettingsTab #model-select {
    width: 30;
    margin: 0 0 1 0;
  }

  SettingsTab DataTable {
    height: auto;
    max-height: 15;
    background: #101010;
  }
  """

  def compose(self) -> ComposeResult:
    with VerticalScroll():
      with Vertical(classes="settings-section"):
        yield Static(
          "takt-service", classes="settings-label"
        )
        yield Static("", id="service-status-label")
      yield Static("Default Model", classes="settings-label")
      yield Select(
        MODELS, id="model-select", allow_blank=False,
      )
      with Vertical(classes="settings-section"):
        yield Static("Repos", classes="settings-label")
        yield DataTable(id="repos-table")
      with Vertical(classes="settings-section"):
        yield Static("Targets", classes="settings-label")
        yield DataTable(id="targets-table")
      with Vertical(classes="settings-section"):
        yield Static(
          "Pipeline Watcher", classes="settings-label"
        )
        yield Static("", id="poll-interval-label")

  def on_mount(self) -> None:
    """Load settings and populate tables."""
    settings = load_settings()
    select = self.query_one("#model-select", Select)
    select.value = settings.get("model", "sonnet")

    interval = settings.get("poll_interval", 30)
    self.query_one(
      "#poll-interval-label", Static
    ).update(f"Poll interval: {interval}s")

    repos_table = self.query_one("#repos-table", DataTable)
    repos_table.cursor_type = "row"
    repos_table.add_columns("Repo", "Path", "Description")

    targets_table = self.query_one(
      "#targets-table", DataTable
    )
    targets_table.cursor_type = "row"
    targets_table.add_columns("Name", "Type", "Host")

    self._load_data()
    self._update_service_status()

  def _update_service_status(self) -> None:
    """Update the service status label."""
    label = self.query_one(
      "#service-status-label", Static
    )
    client = getattr(self.app, 'service', None)
    if client:
      label.update("Status: connected")
    else:
      label.update("Status: not connected")

  @work(thread=True)
  def _load_data(self) -> None:
    """Load repos and targets config in worker thread."""
    from lib.config import (
      load_repos_config,
      load_targets_config,
    )
    repos = load_repos_config().get("repos", {})
    targets = load_targets_config().get("targets", {})
    self.app.call_from_thread(
      self._populate, repos, targets
    )

  def _populate(self, repos, targets) -> None:
    """Populate tables with loaded data."""
    repos_table = self.query_one("#repos-table", DataTable)
    for name, cfg in sorted(repos.items()):
      repos_table.add_row(
        name,
        cfg.get("path", name),
        cfg.get("description", ""),
      )

    targets_table = self.query_one(
      "#targets-table", DataTable
    )
    for name, cfg in sorted(targets.items()):
      targets_table.add_row(
        name,
        cfg.get("type", "?"),
        cfg.get("host", "?"),
      )

  def on_select_changed(
    self, event: Select.Changed
  ) -> None:
    """Save model selection to settings."""
    if event.select.id == "model-select":
      settings = load_settings()
      settings["model"] = event.value
      save_settings(settings)

  def action_service_start(self) -> None:
    """Start takt-service."""
    self.app.action_service_start()
    self.set_timer(2, self._update_service_status)

  def action_service_stop(self) -> None:
    """Stop takt-service."""
    self.app.action_service_stop()
    self.set_timer(2, self._update_service_status)

  def action_service_restart(self) -> None:
    """Restart takt-service."""
    self.app.action_service_restart()
    self.set_timer(2, self._update_service_status)
