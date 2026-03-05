"""Workspaces tab — full workspace management.

DataTable of all workspaces with keybinding actions for
create and delete. Inline forms replace modals.
"""

import logging
import time

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import (
  DataTable, Input, Label, SelectionList,
  Static, TextArea,
)
from textual import work

from lib.config import WORKSPACES_DIR
from tui.mixins import TabBase
from tui.widgets.style_utils import (
  age_label, age_style, ws_bucket,
)

log = logging.getLogger("takt.workspaces_tab")


class WorkspacesTab(TabBase, Static):
  """Workspace management tab."""

  _status_id = "workspaces-tab-status"

  BINDINGS = [
    Binding("n", "new_workspace", "New"),
    Binding("d", "delete_workspace", "Delete"),
    Binding("a", "add_repo", "Add Repo"),
    Binding("e", "edit_claude_md", "Edit CLAUDE.md"),
    Binding("escape", "hide_forms", "Cancel",
            show=False),
  ]

  DEFAULT_CSS = """
  WorkspacesTab {
    height: 1fr;
    padding: 1 2;
  }

  WorkspacesTab #workspaces-tab-table {
    height: 1fr;
    background: #101010;
  }

  WorkspacesTab #workspaces-tab-status {
    height: auto;
    margin: 1 0 0 0;
    color: $warning;
  }

  WorkspacesTab #ws-create-form {
    height: 1fr;
    margin: 1 0;
    padding: 0 1;
    border: solid #2a2a2a;
  }

  WorkspacesTab #ws-create-form Label {
    margin: 0 1 0 0;
    padding: 1 0 0 0;
  }

  WorkspacesTab #ws-create-form Input {
    width: 40;
  }

  WorkspacesTab #ws-create-form SelectionList {
    height: 1fr;
  }

  WorkspacesTab #ws-add-repo-form {
    height: 1fr;
    margin: 1 0;
    padding: 0 1;
    border: solid #2a2a2a;
  }

  WorkspacesTab #ws-add-repo-form SelectionList {
    height: 1fr;
  }

  WorkspacesTab #ws-editor {
    height: 1fr;
    border: solid #2a2a2a;
    margin: 1 0;
  }

  WorkspacesTab #ws-editor-area {
    height: 1fr;
  }

  WorkspacesTab #ws-editor Horizontal {
    height: auto;
  }

  WorkspacesTab #ws-editor-title {
    text-style: bold;
  }
  """

  def compose(self) -> ComposeResult:
    yield Static("Workspaces", classes="panel-title")
    yield DataTable(id="workspaces-tab-table")
    with Vertical(id="ws-create-form"):
      yield Static(
        "New Workspace", classes="panel-title"
      )
      with Horizontal():
        yield Label("Name:")
        yield Input(
          placeholder="feature-name",
          id="ws-name-input",
        )
      yield Label("Repos:")
      yield SelectionList[str](id="ws-repo-select")
    with Vertical(id="ws-add-repo-form"):
      yield Static(
        "Add Repo", classes="panel-title"
      )
      yield SelectionList[str](
        id="ws-add-repo-select"
      )
    with Vertical(id="ws-editor"):
      with Horizontal():
        yield Static("CLAUDE.md", id="ws-editor-title")
        yield Static("", id="ws-editor-dirty")
      yield TextArea(id="ws-editor-area")
    yield Static("", id="workspaces-tab-status")

  def on_mount(self) -> None:
    """Set up table, hide forms, load data."""
    self._editor_ws = None
    self._editor_clean = ""
    self._add_repo_ws = None
    table = self.query_one(
      "#workspaces-tab-table", DataTable
    )
    table.cursor_type = "row"
    table.add_columns(
      "Name", "Repos", "Branch", "Activity",
    )
    self.query_one("#ws-create-form").display = False
    self.query_one("#ws-add-repo-form").display = False
    self.query_one("#ws-editor").display = False
    self.refresh_data()

  @work(thread=True)
  def refresh_data(self) -> None:
    """Load workspace data in a worker thread."""
    from lib.workspace_ops import list_workspaces
    workspaces = list_workspaces()
    self.app.call_from_thread(
      self._update_table, workspaces
    )

  def _update_table(self, workspaces) -> None:
    """Update the table with fresh data.

    Args:
      workspaces: List of workspace dicts.
    """
    table = self.query_one(
      "#workspaces-tab-table", DataTable
    )
    old_row = 0
    if table.row_count > 0:
      old_row = table.cursor_row
    table.clear()
    for ws in workspaces:
      last = ws.get("last_active", 0.0)
      if last > 0:
        age_min = (time.time() - last) / 60
      else:
        age_min = float("inf")
      bucket = ws_bucket(age_min)
      style = age_style(bucket)
      activity = (
        age_label(age_min) if last > 0 else "unknown"
      )
      repos = ", ".join(ws.get("repos", []))
      table.add_row(
        Text(ws["name"], style=style),
        Text(repos, style=style),
        Text(ws.get("branch", "?"), style=style),
        Text(activity, style=style),
        key=ws["name"],
      )
    if table.row_count > 0:
      row = min(old_row, table.row_count - 1)
      table.move_cursor(row=row)

  def _get_selected(self):
    """Return the name of the currently selected workspace.

    Returns:
      Workspace name string, or None.
    """
    table = self.query_one(
      "#workspaces-tab-table", DataTable
    )
    if table.row_count == 0:
      return None
    try:
      row_key, _ = table.coordinate_to_cell_key(
        table.cursor_coordinate
      )
      return str(row_key.value)
    except Exception:
      return None

  def action_hide_forms(self) -> None:
    """Hide inline forms and editor on Escape."""
    self.query_one("#ws-create-form").display = False
    self.query_one("#ws-add-repo-form").display = False
    self.query_one("#ws-editor").display = False
    self.query_one(
      "#workspaces-tab-table"
    ).display = True
    self._editor_ws = None

  # -- New Workspace --

  def action_new_workspace(self) -> None:
    """Show inline create form."""
    self.query_one(
      "#workspaces-tab-table"
    ).display = False
    self.query_one("#ws-create-form").display = True
    self._load_repos()
    name_input = self.query_one("#ws-name-input", Input)
    name_input.value = ""
    name_input.focus()

  @work(thread=True)
  def _load_repos(self) -> None:
    """Load repos for create form."""
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
      "#ws-repo-select", SelectionList
    )
    selection.clear_options()
    for name in sorted(repos.keys()):
      desc = repos[name].get("description", "")
      label = f"{name} — {desc}" if desc else name
      selection.add_option((label, name))

  def on_input_submitted(
    self, event: Input.Submitted
  ) -> None:
    """Handle Enter in create form name input."""
    if event.input.id == "ws-name-input":
      self._submit_create()

  def _submit_create(self) -> None:
    """Process the inline create form."""
    name_input = self.query_one("#ws-name-input", Input)
    selection = self.query_one(
      "#ws-repo-select", SelectionList
    )
    name = name_input.value.strip()
    repos = list(selection.selected)
    if not name:
      self._set_status("Name is required.")
      return
    if not repos:
      self._set_status("Select at least one repo.")
      return
    self.query_one("#ws-create-form").display = False
    self._set_status("Creating workspace...")
    self._do_create_workspace(name, repos)

  def _do_create_workspace(self, name, repos) -> None:
    """Route workspace creation to service or local.

    Args:
      name: Workspace name.
      repos: List of repo names.
    """
    client = getattr(self.app, 'service', None)
    if client:
      import asyncio as _aio
      _aio.run_coroutine_threadsafe(
        self._create_via_service(name, repos),
        self.app._loop,
      )
    else:
      self._create_local(name, repos)

  async def _create_via_service(self, name, repos):
    """Send create_workspace to takt-service.

    Args:
      name: Workspace name.
      repos: List of repo names.
    """
    try:
      reply = await self.app.service.send_cmd(
        "create_workspace",
        name=name, repos=repos,
      )
      if reply.get("status") != "ok":
        self._set_status(
          reply.get("message", "Create failed")
        )
    except Exception as e:
      self._set_status(f"Error: {e}")

  @work(thread=True)
  def _create_local(self, name, repos):
    """Create workspace locally (no service).

    Args:
      name: Workspace name.
      repos: List of repo names.
    """
    from lib.workspace_ops import create_workspace
    try:
      create_workspace(name, repos)
      msg = f"Created workspace '{name}'."
      self.app.call_from_thread(
        self._set_status, msg,
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

  # -- Add Repo --

  def action_add_repo(self) -> None:
    """Show add-repo form for the selected workspace."""
    name = self._get_selected()
    if not name:
      self._set_status("No workspace selected.")
      return
    self._add_repo_ws = name
    self.query_one(
      "#workspaces-tab-table"
    ).display = False
    self.query_one("#ws-add-repo-form").display = True
    self._load_add_repos()

  @work(thread=True)
  def _load_add_repos(self) -> None:
    """Load available repos for add-repo form."""
    from lib.config import load_repos_config
    config = load_repos_config()
    repos = config.get("repos", {})
    self.app.call_from_thread(
      self._populate_add_repos, repos
    )

  def _populate_add_repos(self, repos) -> None:
    """Populate add-repo selection list.

    Args:
      repos: Dict of repo name -> config.
    """
    selection = self.query_one(
      "#ws-add-repo-select", SelectionList
    )
    selection.clear_options()
    for name in sorted(repos.keys()):
      desc = repos[name].get("description", "")
      label = f"{name} — {desc}" if desc else name
      selection.add_option((label, name))
    selection.focus()

  def on_selection_list_selected(
    self, event: SelectionList.SelectedChanged
  ) -> None:
    """Handle Enter on add-repo selection list."""
    if event.selection_list.id != "ws-add-repo-select":
      return
    selected = list(
      self.query_one(
        "#ws-add-repo-select", SelectionList
      ).selected
    )
    if not selected:
      self._set_status("Select at least one repo.")
      return
    name = self._add_repo_ws
    self.query_one(
      "#ws-add-repo-form"
    ).display = False
    self.query_one(
      "#workspaces-tab-table"
    ).display = True
    for repo in selected:
      self._set_status(
        f"Adding {repo} to '{name}'..."
      )
      self._do_add_repo(name, repo)

  def _do_add_repo(self, name, repo):
    """Route add_repo to service or local.

    Args:
      name: Workspace name.
      repo: Repo name.
    """
    client = getattr(self.app, 'service', None)
    if client:
      import asyncio as _aio
      _aio.run_coroutine_threadsafe(
        self._add_repo_via_service(name, repo),
        self.app._loop,
      )
    else:
      self._add_repo_local(name, repo)

  async def _add_repo_via_service(self, name, repo):
    """Send add_repo to takt-service.

    Args:
      name: Workspace name.
      repo: Repo name.
    """
    try:
      reply = await self.app.service.send_cmd(
        "add_repo", name=name, repo=repo,
      )
      if reply.get("status") != "ok":
        self._set_status(
          reply.get("message", "Add repo failed")
        )
    except Exception as e:
      self._set_status(f"Error: {e}")

  @work(thread=True)
  def _add_repo_local(self, name, repo):
    """Add repo locally (no service).

    Args:
      name: Workspace name.
      repo: Repo name.
    """
    from lib.workspace_ops import add_repo_to_workspace
    try:
      add_repo_to_workspace(name, repo)
      self.app.call_from_thread(
        self._set_status,
        f"Added {repo} to '{name}'.",
      )
      self.app.call_from_thread(self.refresh_data)
    except Exception as e:
      self.app.call_from_thread(
        self._set_status, f"Error: {e}"
      )

  # -- Delete Workspace --

  def action_delete_workspace(self) -> None:
    """Delete the selected workspace after confirmation."""
    name = self._get_selected()
    if not name:
      self._set_status("No workspace selected.")
      return
    self._confirm(
      f"Delete workspace '{name}'?",
      self._on_delete_confirmed,
      name,
    )

  def _on_delete_confirmed(self, name) -> None:
    """Handle delete confirmation.

    Args:
      name: Workspace name.
    """
    if name:
      self._set_status(f"Deleting {name}...")
      self._do_delete_workspace(name)

  def _do_delete_workspace(self, name) -> None:
    """Route workspace deletion to service or local.

    Args:
      name: Workspace name.
    """
    client = getattr(self.app, 'service', None)
    if client:
      import asyncio as _aio
      _aio.run_coroutine_threadsafe(
        self._delete_via_service(name),
        self.app._loop,
      )
    else:
      self._delete_local(name)

  async def _delete_via_service(self, name):
    """Send delete_workspace to takt-service.

    Args:
      name: Workspace name.
    """
    try:
      reply = await self.app.service.send_cmd(
        "delete_workspace", name=name,
      )
      if reply.get("status") != "ok":
        self._set_status(
          reply.get("message", "Delete failed")
        )
    except Exception as e:
      self._set_status(f"Error: {e}")

  @work(thread=True)
  def _delete_local(self, name):
    """Delete workspace locally (no service).

    Args:
      name: Workspace name.
    """
    from lib.workspace_ops import delete_workspace
    try:
      delete_workspace(name)
      self.app.call_from_thread(
        self._set_status,
        f"Deleted workspace '{name}'.",
      )
      self.app.call_from_thread(self.refresh_data)
    except FileNotFoundError as e:
      self.app.call_from_thread(
        self._set_status, str(e)
      )
    except Exception as e:
      self.app.call_from_thread(
        self._set_status, f"Error: {e}"
      )

  # -- CLAUDE.md Editor --

  def _claude_md_path(self, name):
    """Return CLAUDE.md path for a workspace.

    Args:
      name: Workspace name.

    Returns:
      Path to CLAUDE.md.
    """
    return WORKSPACES_DIR / name / "CLAUDE.md"

  def action_edit_claude_md(self) -> None:
    """Open CLAUDE.md editor for the selected workspace."""
    name = self._get_selected()
    if not name:
      self._set_status("No workspace selected.")
      return
    path = self._claude_md_path(name)
    if not path.exists():
      self._set_status(
        f"No CLAUDE.md in workspace '{name}'."
      )
      return
    try:
      content = path.read_text()
    except OSError as e:
      self._set_status(f"Read error: {e}")
      return
    self._editor_ws = name
    self._editor_clean = content
    self.query_one("#ws-create-form").display = False
    self.query_one(
      "#workspaces-tab-table"
    ).display = False
    self.query_one("#ws-editor").display = True
    title = self.query_one("#ws-editor-title", Static)
    title.update(f"CLAUDE.md — {name}")
    dirty = self.query_one("#ws-editor-dirty", Static)
    dirty.update("")
    editor = self.query_one("#ws-editor-area", TextArea)
    editor.load_text(content)
    editor.focus()

  def on_text_area_changed(
    self, event: TextArea.Changed
  ) -> None:
    """Track dirty state when editor content changes."""
    if self._editor_ws is None:
      return
    editor = self.query_one("#ws-editor-area", TextArea)
    is_dirty = editor.text != self._editor_clean
    dirty = self.query_one("#ws-editor-dirty", Static)
    dirty.update("[modified]" if is_dirty else "")

  def on_key(self, event) -> None:
    """Handle Ctrl+S for CLAUDE.md save."""
    if self._confirm_active:
      super().on_key(event)
      return
    if event.key == "ctrl+s":
      event.stop()
      event.prevent_default()
      self._save_claude_md()

  def _save_claude_md(self) -> None:
    """Write editor content back to CLAUDE.md."""
    if self._editor_ws is None:
      return
    editor = self.query_one("#ws-editor-area", TextArea)
    content = editor.text
    if content == self._editor_clean:
      self.app.notify("No changes to save")
      return
    path = self._claude_md_path(self._editor_ws)
    try:
      path.write_text(content)
      self._editor_clean = content
      dirty = self.query_one("#ws-editor-dirty", Static)
      dirty.update("")
      self.app.notify(
        f"Saved CLAUDE.md for {self._editor_ws}"
      )
    except OSError as e:
      self.app.notify(
        f"Save failed: {e}", severity="error"
      )
