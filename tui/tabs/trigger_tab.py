"""Trigger tab — manual workflow actions.

Reads pipeline definitions and run history from SQLite.
Sends trigger_run commands to takt-service when connected.
Inline forms replace modal dialogs.
"""

import asyncio
import logging
import subprocess

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widgets import (
  DataTable, Input, Label, Select, SelectionList,
  Static,
)
from textual import work

from tui.mixins import TabBase

log = logging.getLogger("takt.trigger_tab")


class TriggerTab(TabBase, Static):
  """Workflow action triggers with inline forms."""

  _status_id = "trigger-status"

  BINDINGS = [
    Binding("t", "trigger_run", "Trigger"),
    Binding("p", "push_github", "Push GH"),
    Binding("n", "new_workspace", "New WS"),
    Binding("s", "goto_pipeline", "Pipeline"),
    Binding("escape", "hide_forms", "Cancel",
            show=False),
  ]

  DEFAULT_CSS = """
  TriggerTab {
    height: 1fr;
    padding: 1 2;
  }

  TriggerTab .trigger-section {
    margin: 1 0;
  }

  TriggerTab .trigger-label {
    text-style: bold;
    color: #cccccc;
    margin: 0 0 1 0;
  }

  TriggerTab DataTable {
    height: auto;
    max-height: 15;
    background: #101010;
  }

  TriggerTab #trigger-form {
    height: auto;
    margin: 1 0;
    padding: 0 1;
    border: solid #2a2a2a;
  }

  TriggerTab #trigger-form Label {
    margin: 0 1 0 0;
    padding: 1 0 0 0;
  }

  TriggerTab #trigger-form Select {
    width: 40;
  }

  TriggerTab #push-form {
    height: auto;
    margin: 1 0;
    padding: 0 1;
    border: solid #2a2a2a;
  }

  TriggerTab #push-form Label {
    margin: 0 1 0 0;
    padding: 1 0 0 0;
  }

  TriggerTab #push-form Select {
    width: 40;
  }

  TriggerTab #new-ws-form {
    height: auto;
    margin: 1 0;
    padding: 0 1;
    border: solid #2a2a2a;
  }

  TriggerTab #new-ws-form Label {
    margin: 0 1 0 0;
    padding: 1 0 0 0;
  }

  TriggerTab #new-ws-form Input {
    width: 40;
  }

  TriggerTab #new-ws-form SelectionList {
    height: 10;
  }

  TriggerTab #trigger-status {
    height: auto;
    margin: 1 0 0 0;
    color: $warning;
  }
  """

  def compose(self) -> ComposeResult:
    # Inline trigger form.
    with Vertical(id="trigger-form"):
      yield Static("Trigger Run", classes="trigger-label")
      with Horizontal():
        yield Label("Workspace:")
        yield Select([], id="trigger-ws-select")
    # Inline push form.
    with Vertical(id="push-form"):
      yield Static("Push to GitHub", classes="trigger-label")
      with Horizontal():
        yield Label("Workspace:")
        yield Select([], id="push-ws-select")
    # Inline new workspace form.
    with Vertical(id="new-ws-form"):
      yield Static(
        "New Workspace", classes="trigger-label"
      )
      with Horizontal():
        yield Label("Name:")
        yield Input(
          placeholder="feature-name",
          id="new-ws-name-input",
        )
      yield Label("Repos:")
      yield SelectionList[str](id="new-ws-repo-select")
    # Data tables.
    with Vertical(classes="trigger-section"):
      yield Static("Pipelines", classes="trigger-label")
      yield DataTable(id="trigger-pipelines-table")
    with Vertical(classes="trigger-section"):
      yield Static("Recent Runs", classes="trigger-label")
      yield DataTable(id="trigger-runs-table")
    yield Static("", id="trigger-status")

  def on_mount(self) -> None:
    """Set up tables, hide forms, load data."""
    pipelines_t = self.query_one(
      "#trigger-pipelines-table", DataTable
    )
    pipelines_t.cursor_type = "row"
    pipelines_t.add_columns(
      "Workspace", "Steps", "Type"
    )

    runs_t = self.query_one(
      "#trigger-runs-table", DataTable
    )
    runs_t.cursor_type = "row"
    runs_t.add_columns(
      "ID", "Workspace", "Status", "Trigger", "Created"
    )
    self.query_one("#trigger-form").display = False
    self.query_one("#push-form").display = False
    self.query_one("#new-ws-form").display = False
    self.refresh_data()

  @work(thread=True)
  def refresh_data(self) -> None:
    """Load pipelines and runs from SQLite."""
    from lib import db
    from lib.workspace_ops import list_workspaces
    workspaces = list_workspaces()
    pipelines = []
    for ws in workspaces:
      steps = db.get_pipeline(ws["name"])
      if steps:
        pipelines.append({
          "workspace": ws["name"],
          "steps": ", ".join(s["name"] for s in steps),
          "types": ", ".join(
            s["step_type"] for s in steps
          ),
        })
    runs = db.list_runs(limit=20)
    self.app.call_from_thread(
      self._populate, pipelines, runs
    )

  def _populate(self, pipelines, runs) -> None:
    """Populate tables."""
    pipelines_t = self.query_one(
      "#trigger-pipelines-table", DataTable
    )
    pipelines_t.clear()
    for p in pipelines:
      pipelines_t.add_row(
        p["workspace"],
        p["steps"],
        p["types"],
      )

    runs_t = self.query_one(
      "#trigger-runs-table", DataTable
    )
    runs_t.clear()
    for r in runs:
      created = r.get("created_at", "")[:19]
      runs_t.add_row(
        str(r.get("id", "")),
        r.get("workspace", ""),
        r.get("status", "?"),
        r.get("trigger", ""),
        created,
      )

  def action_hide_forms(self) -> None:
    """Hide all inline forms on Escape."""
    self.query_one("#trigger-form").display = False
    self.query_one("#push-form").display = False
    self.query_one("#new-ws-form").display = False

  # -- Trigger Run --

  def action_trigger_run(self) -> None:
    """Show inline trigger form."""
    self._hide_all_forms()
    self.query_one("#trigger-form").display = True
    self._load_trigger_workspaces()

  def trigger_for_workspace(self, ws_name) -> None:
    """Show trigger form with a workspace preselected.

    Args:
      ws_name: Workspace name to preselect.
    """
    self._hide_all_forms()
    self.query_one("#trigger-form").display = True
    self._load_trigger_workspaces(preset=ws_name)

  @work(thread=True)
  def _load_trigger_workspaces(self, preset=None):
    """Load workspaces with pipelines for trigger form."""
    from lib import db
    from lib.workspace_ops import list_workspaces
    workspaces = list_workspaces()
    ws_with_pipeline = []
    for ws in workspaces:
      pipeline = db.get_pipeline(ws["name"])
      if pipeline:
        ws_with_pipeline.append(ws["name"])
    self.app.call_from_thread(
      self._populate_trigger_ws,
      sorted(ws_with_pipeline),
      preset,
    )

  def _populate_trigger_ws(self, ws_names, preset):
    """Populate the trigger workspace select.

    Args:
      ws_names: List of workspace names.
      preset: Workspace to preselect, or None.
    """
    ws_select = self.query_one(
      "#trigger-ws-select", Select
    )
    ws_select.set_options(
      [(w, w) for w in ws_names]
    )
    if preset and preset in ws_names:
      ws_select.value = preset

  def on_select_changed(
    self, event: Select.Changed
  ) -> None:
    """Handle select changes — auto-submit forms."""
    if event.select.id == "trigger-ws-select":
      if event.value is not Select.BLANK:
        self._submit_trigger(str(event.value))
    elif event.select.id == "push-ws-select":
      if event.value is not Select.BLANK:
        self._submit_push(str(event.value))

  def _submit_trigger(self, ws) -> None:
    """Trigger a pipeline run for a workspace.

    Args:
      ws: Workspace name.
    """
    self.query_one("#trigger-form").display = False
    self._set_status(f"Triggering run for {ws}...")
    self._do_trigger(ws)

  @work(thread=True)
  def _do_trigger(self, ws) -> None:
    """Create a run in DB and optionally via service.

    Args:
      ws: Workspace name.
    """
    from lib import db
    from lib.workspace_ops import list_workspaces
    workspaces = list_workspaces()
    ws_info = next(
      (w for w in workspaces if w["name"] == ws),
      None,
    )
    if not ws_info:
      self.app.call_from_thread(
        self._set_status,
        f"Workspace '{ws}' not found.",
      )
      return
    repos = ws_info.get("repos", [])
    run_id = db.create_run(ws, "manual", repos, {})
    if run_id is None:
      self.app.call_from_thread(
        self._set_status,
        "Duplicate run (already queued).",
      )
      return
    self.app.call_from_thread(
      self._set_status,
      f"Created run {run_id} for {ws}.",
    )
    self.app.call_from_thread(self.refresh_data)
    # Also trigger via service if connected.
    client = getattr(self.app, 'service', None)
    if client:
      import asyncio as _asyncio
      _asyncio.run_coroutine_threadsafe(
        self._trigger_via_service(ws),
        self.app._loop,
      )

  async def _trigger_via_service(self, ws):
    """Send trigger_run command to service.

    Args:
      ws: Workspace name.
    """
    try:
      reply = await self.app.service.send_cmd(
        "trigger_run", workspace=ws,
      )
      if reply.get("status") == "ok":
        self.app.notify(f"Triggered run for {ws}")
      else:
        self.app.notify(
          reply.get("message", "Trigger failed"),
          severity="error",
        )
    except Exception as e:
      log.error(
        "trigger_run failed: %s", e, exc_info=True
      )
      self.app.notify(
        f"Trigger failed: {e}", severity="error"
      )

  # -- Push to GitHub --

  def action_push_github(self) -> None:
    """Show inline push form."""
    self._hide_all_forms()
    self.query_one("#push-form").display = True
    self._load_push_workspaces()

  @work(thread=True)
  def _load_push_workspaces(self) -> None:
    """Load all workspaces for push form."""
    from lib.workspace_ops import list_workspaces
    workspaces = list_workspaces()
    names = sorted(ws["name"] for ws in workspaces)
    self.app.call_from_thread(
      self._populate_push_ws, names
    )

  def _populate_push_ws(self, names) -> None:
    """Populate push workspace select.

    Args:
      names: List of workspace names.
    """
    ws_select = self.query_one(
      "#push-ws-select", Select
    )
    ws_select.set_options(
      [(n, n) for n in names]
    )

  def _submit_push(self, ws) -> None:
    """Push a workspace branch to GitHub.

    Args:
      ws: Workspace name.
    """
    self.query_one("#push-form").display = False
    self._set_status(f"Pushing {ws}...")
    self._do_push(ws)

  @work(thread=True)
  def _do_push(self, ws) -> None:
    """Run push_to_github.py in worker thread.

    Args:
      ws: Workspace name.
    """
    from lib.config import PROJECT_DIR
    script = PROJECT_DIR / "bin" / "push_to_github.py"
    result = subprocess.run(
      ["python3", str(script), ws, "--yes"],
      capture_output=True, text=True,
    )
    if result.returncode == 0:
      self.app.call_from_thread(
        self._set_status,
        f"Pushed {ws} to GitHub.",
      )
    else:
      msg = result.stderr.strip() or "Push failed."
      self.app.call_from_thread(
        self._set_status, msg,
      )

  # -- New Workspace --

  def action_new_workspace(self) -> None:
    """Show inline new workspace form."""
    self._hide_all_forms()
    self.query_one("#new-ws-form").display = True
    self._load_repos()
    name_input = self.query_one(
      "#new-ws-name-input", Input
    )
    name_input.value = ""
    name_input.focus()

  @work(thread=True)
  def _load_repos(self) -> None:
    """Load repos for new workspace form."""
    from lib.config import load_repos_config
    config = load_repos_config()
    repos = config.get("repos", {})
    self.app.call_from_thread(
      self._populate_repos, repos
    )

  def _populate_repos(self, repos) -> None:
    """Populate repo selection list.

    Args:
      repos: Dict of repo name -> config.
    """
    selection = self.query_one(
      "#new-ws-repo-select", SelectionList
    )
    selection.clear_options()
    for name in sorted(repos.keys()):
      desc = repos[name].get("description", "")
      label = f"{name} — {desc}" if desc else name
      selection.add_option((label, name))

  def on_input_submitted(
    self, event: Input.Submitted
  ) -> None:
    """Handle Enter in new workspace name input."""
    if event.input.id == "new-ws-name-input":
      self._submit_new_workspace()

  def _submit_new_workspace(self) -> None:
    """Process the inline new workspace form."""
    name_input = self.query_one(
      "#new-ws-name-input", Input
    )
    selection = self.query_one(
      "#new-ws-repo-select", SelectionList
    )
    name = name_input.value.strip()
    repos = list(selection.selected)
    if not name:
      self._set_status("Name is required.")
      return
    if not repos:
      self._set_status("Select at least one repo.")
      return
    self.query_one("#new-ws-form").display = False
    self._set_status("Creating workspace...")
    self._do_create_workspace(name, repos)

  @work(thread=True)
  def _do_create_workspace(self, name, repos) -> None:
    """Create workspace in a worker thread.

    Args:
      name: Workspace name.
      repos: List of repo names.
    """
    from lib.workspace_ops import create_workspace
    try:
      create_workspace(name, repos)
      self.app.call_from_thread(
        self._set_status,
        f"Created workspace '{name}'.",
      )
      self.app.call_from_thread(self.refresh_data)
    except (FileExistsError, ValueError) as e:
      self.app.call_from_thread(
        self._set_status, str(e)
      )
    except Exception as e:
      self.app.call_from_thread(
        self._set_status, f"Error: {e}"
      )

  # -- Go to Pipeline --

  def action_goto_pipeline(self) -> None:
    """Switch to the Pipeline tab."""
    try:
      from textual.widgets import TabbedContent
      tabs = self.app.query_one(
        "#tabs", TabbedContent
      )
      tabs.active = "tab-pipeline"
    except NoMatches:
      pass

  # -- Helpers --

  def _hide_all_forms(self) -> None:
    """Hide all inline forms."""
    self.query_one("#trigger-form").display = False
    self.query_one("#push-form").display = False
    self.query_one("#new-ws-form").display = False
