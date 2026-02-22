"""Modal screens for the dashboard."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import (
  Button,
  DataTable,
  Input,
  Label,
  Select,
  SelectionList,
  Static,
  TextArea,
)
from textual import work


class CreateWorkspaceScreen(ModalScreen[str | None]):
  """Modal to create a new workspace."""

  DEFAULT_CSS = """
  CreateWorkspaceScreen {
    align: center middle;
  }

  #create-ws-dialog {
    width: 60;
    height: auto;
    max-height: 80%;
    border: thick $accent;
    background: $surface;
    padding: 1 2;
  }

  #create-ws-dialog Label {
    margin: 1 0 0 0;
  }

  #create-ws-dialog Input {
    margin: 0 0 1 0;
  }

  #create-ws-dialog SelectionList {
    height: 10;
    margin: 0 0 1 0;
  }

  #create-ws-buttons {
    height: auto;
    align: right middle;
  }

  #create-ws-buttons Button {
    margin: 0 1;
  }

  #create-ws-status {
    height: auto;
    margin: 1 0 0 0;
    color: $warning;
  }
  """

  BINDINGS = [
    Binding("escape", "cancel", "Cancel"),
  ]

  def compose(self) -> ComposeResult:
    with Vertical(id="create-ws-dialog"):
      yield Static(
        "Create Workspace", classes="panel-title"
      )
      yield Label("Workspace name (= branch name):")
      yield Input(
        placeholder="feature-name",
        id="ws-name-input",
      )
      yield Label("Select repos:")
      yield SelectionList[str](id="repo-selection")
      yield Static("", id="create-ws-status")
      with Horizontal(id="create-ws-buttons"):
        yield Button(
          "Cancel", variant="default", id="btn-cancel"
        )
        yield Button(
          "Create", variant="primary", id="btn-create"
        )

  def on_mount(self) -> None:
    """Load repos from config."""
    from lib.config import load_repos_config
    config = load_repos_config()
    repos = config.get("repos", {})
    selection = self.query_one(
      "#repo-selection", SelectionList
    )
    for name in sorted(repos.keys()):
      desc = repos[name].get("description", "")
      label = f"{name} — {desc}" if desc else name
      selection.add_option((label, name))
    self.query_one("#ws-name-input", Input).focus()

  def on_button_pressed(
    self, event: Button.Pressed
  ) -> None:
    if event.button.id == "btn-cancel":
      self.dismiss(None)
    elif event.button.id == "btn-create":
      self._do_create()

  def action_cancel(self) -> None:
    self.dismiss(None)

  @work(thread=True)
  def _do_create(self) -> None:
    """Create workspace in a worker thread."""
    name_input = self.query_one("#ws-name-input", Input)
    selection = self.query_one(
      "#repo-selection", SelectionList
    )
    name = name_input.value.strip()
    repos = list(selection.selected)

    if not name:
      self.app.call_from_thread(
        self._set_status, "Name is required."
      )
      return
    if not repos:
      self.app.call_from_thread(
        self._set_status, "Select at least one repo."
      )
      return

    self.app.call_from_thread(
      self._set_status, "Creating workspace..."
    )

    from lib.workspace_ops import create_workspace
    try:
      create_workspace(name, repos)
      self.app.call_from_thread(self.dismiss, name)
    except (FileExistsError, ValueError) as e:
      self.app.call_from_thread(
        self._set_status, str(e)
      )
    except Exception as e:
      self.app.call_from_thread(
        self._set_status, f"Error: {e}"
      )

  def _set_status(self, text: str) -> None:
    status = self.query_one("#create-ws-status", Static)
    status.update(text)


class ClaimTargetScreen(ModalScreen[str | None]):
  """Modal to claim a target for a workspace."""

  DEFAULT_CSS = """
  ClaimTargetScreen {
    align: center middle;
  }

  #claim-dialog {
    width: 50;
    height: auto;
    border: thick $accent;
    background: $surface;
    padding: 1 2;
  }

  #claim-dialog Label {
    margin: 1 0 0 0;
  }

  #claim-dialog Input {
    margin: 0 0 1 0;
  }

  #claim-buttons {
    height: auto;
    align: right middle;
  }

  #claim-buttons Button {
    margin: 0 1;
  }

  #claim-status {
    height: auto;
    margin: 1 0 0 0;
    color: $warning;
  }
  """

  BINDINGS = [
    Binding("escape", "cancel", "Cancel"),
  ]

  def __init__(self, target_name: str) -> None:
    super().__init__()
    self.target_name = target_name

  def compose(self) -> ComposeResult:
    with Vertical(id="claim-dialog"):
      yield Static(
        f"Claim Target: {self.target_name}",
        classes="panel-title",
      )
      yield Label("Workspace name:")
      yield Input(
        placeholder="workspace-name",
        id="claim-ws-input",
      )
      yield Static("", id="claim-status")
      with Horizontal(id="claim-buttons"):
        yield Button(
          "Cancel", variant="default", id="btn-cancel"
        )
        yield Button(
          "Claim", variant="primary", id="btn-claim"
        )

  def on_mount(self) -> None:
    self.query_one("#claim-ws-input", Input).focus()

  def on_button_pressed(
    self, event: Button.Pressed
  ) -> None:
    if event.button.id == "btn-cancel":
      self.dismiss(None)
    elif event.button.id == "btn-claim":
      self._do_claim()

  def action_cancel(self) -> None:
    self.dismiss(None)

  @work(thread=True)
  def _do_claim(self) -> None:
    """Claim target in a worker thread."""
    ws_input = self.query_one("#claim-ws-input", Input)
    workspace = ws_input.value.strip()

    if not workspace:
      self.app.call_from_thread(
        self._set_status, "Workspace name is required."
      )
      return

    from lib.target_ops import read_lock, write_lock
    lock = read_lock(self.target_name)
    if lock:
      self.app.call_from_thread(
        self._set_status,
        f"Already claimed by '{lock['workspace']}'.",
      )
      return

    try:
      write_lock(self.target_name, workspace)
      self.app.call_from_thread(
        self.dismiss, self.target_name
      )
    except Exception as e:
      self.app.call_from_thread(
        self._set_status, f"Error: {e}"
      )

  def _set_status(self, text: str) -> None:
    status = self.query_one("#claim-status", Static)
    status.update(text)


class ConfirmScreen(ModalScreen[str | None]):
  """Reusable yes/no confirmation dialog."""

  DEFAULT_CSS = """
  ConfirmScreen {
    align: center middle;
  }

  #confirm-dialog {
    width: 50;
    height: auto;
    border: thick $accent;
    background: $surface;
    padding: 1 2;
  }

  #confirm-buttons {
    height: auto;
    align: right middle;
    margin: 1 0 0 0;
  }

  #confirm-buttons Button {
    margin: 0 1;
  }
  """

  BINDINGS = [
    Binding("escape", "cancel", "Cancel"),
    Binding("y", "confirm", "Yes"),
    Binding("n", "cancel", "No"),
  ]

  def __init__(self, message: str, data: str = "") -> None:
    super().__init__()
    self.message = message
    self.data = data

  def compose(self) -> ComposeResult:
    with Vertical(id="confirm-dialog"):
      yield Static(self.message)
      with Horizontal(id="confirm-buttons"):
        yield Button(
          "No", variant="default", id="btn-no"
        )
        yield Button(
          "Yes", variant="warning", id="btn-yes"
        )

  def on_button_pressed(
    self, event: Button.Pressed
  ) -> None:
    if event.button.id == "btn-yes":
      self.dismiss(self.data)
    else:
      self.dismiss(None)

  def action_confirm(self) -> None:
    self.dismiss(self.data)

  def action_cancel(self) -> None:
    self.dismiss(None)


class TriggerRunScreen(ModalScreen[dict | None]):
  """Modal to trigger a pipeline run for a workspace."""

  DEFAULT_CSS = """
  TriggerRunScreen {
    align: center middle;
  }

  #trigger-dialog {
    width: 60;
    height: auto;
    max-height: 80%;
    border: thick $accent;
    background: $surface;
    padding: 1 2;
  }

  #trigger-dialog Label {
    margin: 1 0 0 0;
  }

  #trigger-dialog Select {
    margin: 0 0 1 0;
  }

  #trigger-buttons {
    height: auto;
    align: right middle;
  }

  #trigger-buttons Button {
    margin: 0 1;
  }

  #trigger-status {
    height: auto;
    margin: 1 0 0 0;
    color: $warning;
  }
  """

  BINDINGS = [
    Binding("escape", "cancel", "Cancel"),
  ]

  def __init__(self, workspace=None) -> None:
    """Initialize with optional workspace preset.

    Args:
      workspace: Workspace name to preselect, or None.
    """
    super().__init__()
    self._preset_ws = workspace

  def compose(self) -> ComposeResult:
    with Vertical(id="trigger-dialog"):
      yield Static(
        "Trigger Pipeline Run", classes="panel-title"
      )
      yield Label("Workspace:")
      yield Select([], id="trigger-ws-select")
      yield Static("", id="trigger-status")
      with Horizontal(id="trigger-buttons"):
        yield Button(
          "Cancel", variant="default", id="btn-cancel"
        )
        yield Button(
          "Trigger", variant="primary",
          id="btn-trigger",
        )

  def on_mount(self) -> None:
    """Load workspaces with pipelines."""
    self._load_options()

  @work(thread=True)
  def _load_options(self) -> None:
    """Load workspace options in worker."""
    from lib import db
    from lib.workspace_ops import list_workspaces
    workspaces = list_workspaces()
    # Only show workspaces that have pipelines.
    ws_with_pipeline = []
    for ws in workspaces:
      pipeline = db.get_pipeline(ws["name"])
      if pipeline:
        ws_with_pipeline.append(ws["name"])
    self.app.call_from_thread(
      self._populate, sorted(ws_with_pipeline)
    )

  def _populate(self, ws_names) -> None:
    """Populate the workspace select."""
    ws_select = self.query_one(
      "#trigger-ws-select", Select
    )
    ws_select.set_options(
      [(w, w) for w in ws_names]
    )
    if self._preset_ws and self._preset_ws in ws_names:
      ws_select.value = self._preset_ws

  def on_button_pressed(
    self, event: Button.Pressed
  ) -> None:
    if event.button.id == "btn-cancel":
      self.dismiss(None)
    elif event.button.id == "btn-trigger":
      self._do_trigger()

  def action_cancel(self) -> None:
    self.dismiss(None)

  @work(thread=True)
  def _do_trigger(self) -> None:
    """Trigger a pipeline run."""
    ws_select = self.query_one(
      "#trigger-ws-select", Select
    )
    ws = ws_select.value
    if ws is Select.BLANK:
      self.app.call_from_thread(
        self._set_status, "Select a workspace."
      )
      return

    self.app.call_from_thread(
      self._set_status, "Triggering run..."
    )

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
    run_id = db.create_run(
      ws, "manual", repos, {},
    )
    if run_id is None:
      self.app.call_from_thread(
        self._set_status, "Duplicate run (already queued)."
      )
      return

    self.app.call_from_thread(
      self.dismiss,
      {"workspace": ws, "run_id": run_id},
    )

  def _set_status(self, text: str) -> None:
    status = self.query_one("#trigger-status", Static)
    status.update(text)


class PipelineSetupScreen(ModalScreen[dict | None]):
  """Pipeline editor with reordering and role editing."""

  DEFAULT_CSS = """
  PipelineSetupScreen {
    align: center middle;
  }

  #pipeline-setup-dialog {
    width: 70;
    height: auto;
    max-height: 85%;
    border: thick $accent;
    background: $surface;
    padding: 1 2;
  }

  #pipeline-setup-dialog Label {
    margin: 1 0 0 0;
  }

  #pipeline-setup-dialog Select {
    margin: 0 0 1 0;
  }

  #pipeline-steps-table {
    height: 12;
    margin: 0 0 1 0;
  }

  #pipeline-step-buttons {
    height: auto;
    align: left middle;
    margin: 0 0 1 0;
  }

  #pipeline-step-buttons Button {
    margin: 0 1 0 0;
  }

  #pipeline-setup-buttons {
    height: auto;
    align: right middle;
  }

  #pipeline-setup-buttons Button {
    margin: 0 1;
  }

  #pipeline-setup-status {
    height: auto;
    margin: 1 0 0 0;
    color: $warning;
  }
  """

  BINDINGS = [
    Binding("escape", "cancel", "Cancel"),
    Binding("j", "move_down", "Move Down", show=False),
    Binding("k", "move_up", "Move Up", show=False),
  ]

  def __init__(self, workspace=None) -> None:
    """Initialize with optional workspace preset.

    Args:
      workspace: Workspace name to preselect, or None.
    """
    super().__init__()
    self._preset_ws = workspace
    self._steps: list[dict] = []
    self._roles: list[dict] = []
    self._roles_dirty = False

  def compose(self) -> ComposeResult:
    with Vertical(id="pipeline-setup-dialog"):
      yield Static(
        "Setup Pipeline", classes="panel-title"
      )
      yield Label("Workspace:")
      yield Select([], id="pipeline-ws-select")
      yield Label("Steps (j/k to reorder):")
      yield DataTable(
        id="pipeline-steps-table",
        cursor_type="row",
      )
      with Horizontal(id="pipeline-step-buttons"):
        yield Button(
          "Add", variant="primary", id="btn-add"
        )
        yield Button(
          "Remove", variant="error", id="btn-remove"
        )
        yield Button(
          "Edit Role", variant="default",
          id="btn-edit-role",
        )
      yield Static("", id="pipeline-setup-status")
      with Horizontal(id="pipeline-setup-buttons"):
        yield Button(
          "Delete Pipeline", variant="error",
          id="btn-delete",
        )
        yield Button(
          "Cancel", variant="default", id="btn-cancel"
        )
        yield Button(
          "Save", variant="primary", id="btn-save"
        )

  def on_mount(self) -> None:
    """Load workspaces and roles."""
    table = self.query_one(
      "#pipeline-steps-table", DataTable
    )
    table.add_columns("#", "Name", "Type")
    self._load_data()

  @work(thread=True)
  def _load_data(self) -> None:
    """Load workspaces and roles in worker."""
    from lib.config import parse_pipeline_roles_full
    from lib.workspace_ops import list_workspaces

    workspaces = list_workspaces()
    ws_names = sorted(ws["name"] for ws in workspaces)
    roles = parse_pipeline_roles_full()

    self.app.call_from_thread(
      self._populate, ws_names, roles
    )

  def _populate(self, ws_names, roles) -> None:
    """Populate workspace select and store roles."""
    self._roles = roles
    ws_select = self.query_one(
      "#pipeline-ws-select", Select
    )
    ws_select.set_options(
      [(w, w) for w in ws_names]
    )
    if self._preset_ws and self._preset_ws in ws_names:
      ws_select.value = self._preset_ws
      self._load_pipeline(self._preset_ws)

  def on_select_changed(
    self, event: Select.Changed
  ) -> None:
    """Reload steps when workspace changes."""
    if event.select.id != "pipeline-ws-select":
      return
    if event.value is Select.BLANK:
      self._steps = []
      self._rebuild_table()
      return
    self._load_pipeline(str(event.value))

  @work(thread=True)
  def _load_pipeline(self, ws_name) -> None:
    """Load existing pipeline from DB."""
    from lib import db
    pipeline = db.get_pipeline(ws_name)
    steps = [
      {"name": s["name"], "step_type": s["step_type"]}
      for s in pipeline
    ] if pipeline else []
    self.app.call_from_thread(
      self._apply_steps, steps
    )

  def _apply_steps(self, steps) -> None:
    """Set steps and rebuild the table."""
    self._steps = steps
    self._rebuild_table()
    self._set_status("")

  def _rebuild_table(self) -> None:
    """Rebuild the DataTable from self._steps."""
    table = self.query_one(
      "#pipeline-steps-table", DataTable
    )
    table.clear()
    for i, step in enumerate(self._steps):
      table.add_row(
        str(i + 1), step["name"], step["step_type"],
      )

  def action_move_up(self) -> None:
    """Move the selected step up."""
    table = self.query_one(
      "#pipeline-steps-table", DataTable
    )
    if table.cursor_row < 1:
      return
    idx = table.cursor_row
    self._steps[idx - 1], self._steps[idx] = (
      self._steps[idx], self._steps[idx - 1]
    )
    self._rebuild_table()
    table.move_cursor(row=idx - 1)

  def action_move_down(self) -> None:
    """Move the selected step down."""
    table = self.query_one(
      "#pipeline-steps-table", DataTable
    )
    idx = table.cursor_row
    if idx >= len(self._steps) - 1:
      return
    self._steps[idx], self._steps[idx + 1] = (
      self._steps[idx + 1], self._steps[idx]
    )
    self._rebuild_table()
    table.move_cursor(row=idx + 1)

  def on_button_pressed(
    self, event: Button.Pressed
  ) -> None:
    """Handle button presses."""
    bid = event.button.id
    if bid == "btn-cancel":
      self.dismiss(None)
    elif bid == "btn-save":
      self._do_save()
    elif bid == "btn-delete":
      self._do_delete()
    elif bid == "btn-add":
      self._do_add()
    elif bid == "btn-remove":
      self._do_remove()
    elif bid == "btn-edit-role":
      self._do_edit_role()

  def action_cancel(self) -> None:
    self.dismiss(None)

  def _do_add(self) -> None:
    """Open add-step picker."""
    from lib.pipeline import SCRIPT_REGISTRY
    existing = {s["name"] for s in self._steps}
    available = []
    for role in self._roles:
      if role["slug"] not in existing:
        available.append({
          "name": role["slug"],
          "step_type": "agent",
        })
    for name in sorted(SCRIPT_REGISTRY):
      if name not in existing:
        available.append({
          "name": name,
          "step_type": "script",
        })
    if not available:
      self._set_status("No steps available to add.")
      return
    self.app.push_screen(
      AddStepScreen(available), self._on_step_added
    )

  def _on_step_added(self, result) -> None:
    """Callback when AddStepScreen returns."""
    if result is None:
      return
    self._steps.append(result)
    self._rebuild_table()
    self._set_status(f"Added {result['name']}.")

  def _do_remove(self) -> None:
    """Remove the selected step."""
    table = self.query_one(
      "#pipeline-steps-table", DataTable
    )
    if not self._steps:
      return
    idx = table.cursor_row
    if 0 <= idx < len(self._steps):
      removed = self._steps.pop(idx)
      self._rebuild_table()
      self._set_status(f"Removed {removed['name']}.")

  def _do_edit_role(self) -> None:
    """Open the role editor for the selected step."""
    table = self.query_one(
      "#pipeline-steps-table", DataTable
    )
    if not self._steps:
      return
    idx = table.cursor_row
    if idx < 0 or idx >= len(self._steps):
      return
    step = self._steps[idx]
    if step["step_type"] != "agent":
      self._set_status("Only agent roles can be edited.")
      return
    # Find the matching role.
    role = next(
      (r for r in self._roles if r["slug"] == step["name"]),
      None,
    )
    if role is None:
      self._set_status(
        f"Role '{step['name']}' not found."
      )
      return
    self.app.push_screen(
      RoleEditorScreen(role["heading"], role["text"]),
      lambda result, r=role: self._on_role_edited(
        r, result
      ),
    )

  def _on_role_edited(self, role, result) -> None:
    """Callback when RoleEditorScreen returns."""
    if result is None:
      return
    role["text"] = result
    self._roles_dirty = True
    self._set_status(
      f"Updated role '{role['heading']}'."
    )

  def _do_delete(self) -> None:
    """Delete the entire pipeline after confirmation."""
    ws_select = self.query_one(
      "#pipeline-ws-select", Select
    )
    ws = ws_select.value
    if ws is Select.BLANK:
      self._set_status("Select a workspace.")
      return
    self.app.push_screen(
      ConfirmScreen(
        f"Delete pipeline for '{ws}'?", str(ws)
      ),
      self._on_delete_confirmed,
    )

  def _on_delete_confirmed(self, result) -> None:
    """Callback after delete confirmation."""
    if result is None:
      return
    self._delete_pipeline(result)

  @work(thread=True)
  def _delete_pipeline(self, ws) -> None:
    """Delete pipeline in worker."""
    from lib import db
    try:
      db.define_pipeline(ws, [])
      self.app.call_from_thread(
        self.dismiss,
        {"workspace": ws, "steps": 0},
      )
    except Exception as e:
      self.app.call_from_thread(
        self._set_status, f"Error: {e}"
      )

  @work(thread=True)
  def _do_save(self) -> None:
    """Save pipeline definition and roles."""
    ws_select = self.query_one(
      "#pipeline-ws-select", Select
    )
    ws = ws_select.value
    if ws is Select.BLANK:
      self.app.call_from_thread(
        self._set_status, "Select a workspace."
      )
      return

    if not self._steps:
      self.app.call_from_thread(
        self._set_status,
        "Add at least one step.",
      )
      return

    from lib import db
    from lib.config import save_pipeline_roles
    try:
      db.define_pipeline(ws, self._steps)
      if self._roles_dirty:
        save_pipeline_roles(self._roles)
        self._roles_dirty = False
      self.app.call_from_thread(
        self.dismiss,
        {"workspace": ws, "steps": len(self._steps)},
      )
    except Exception as e:
      self.app.call_from_thread(
        self._set_status, f"Error: {e}"
      )

  def _set_status(self, text: str) -> None:
    status = self.query_one(
      "#pipeline-setup-status", Static
    )
    status.update(text)


class AddStepScreen(ModalScreen[dict | None]):
  """Picker to add a step to the pipeline."""

  DEFAULT_CSS = """
  AddStepScreen {
    align: center middle;
  }

  #add-step-dialog {
    width: 50;
    height: auto;
    max-height: 70%;
    border: thick $accent;
    background: $surface;
    padding: 1 2;
  }

  #add-step-table {
    height: 10;
    margin: 0 0 1 0;
  }

  #add-step-buttons {
    height: auto;
    align: right middle;
  }

  #add-step-buttons Button {
    margin: 0 1;
  }
  """

  BINDINGS = [
    Binding("escape", "cancel", "Cancel"),
  ]

  def __init__(self, available: list[dict]) -> None:
    """Initialize with available steps.

    Args:
      available: List of {"name", "step_type"} dicts.
    """
    super().__init__()
    self._available = available

  def compose(self) -> ComposeResult:
    with Vertical(id="add-step-dialog"):
      yield Static("Add Step", classes="panel-title")
      yield DataTable(
        id="add-step-table", cursor_type="row"
      )
      with Horizontal(id="add-step-buttons"):
        yield Button(
          "Cancel", variant="default", id="btn-cancel"
        )
        yield Button(
          "Add", variant="primary", id="btn-add"
        )

  def on_mount(self) -> None:
    table = self.query_one(
      "#add-step-table", DataTable
    )
    table.add_columns("Name", "Type")
    for step in self._available:
      table.add_row(step["name"], step["step_type"])

  def on_data_table_row_selected(
    self, event: DataTable.RowSelected
  ) -> None:
    """Select step on Enter/double-click."""
    idx = event.cursor_row
    if 0 <= idx < len(self._available):
      self.dismiss(self._available[idx])

  def on_button_pressed(
    self, event: Button.Pressed
  ) -> None:
    if event.button.id == "btn-cancel":
      self.dismiss(None)
    elif event.button.id == "btn-add":
      table = self.query_one(
        "#add-step-table", DataTable
      )
      idx = table.cursor_row
      if 0 <= idx < len(self._available):
        self.dismiss(self._available[idx])

  def action_cancel(self) -> None:
    self.dismiss(None)


class RoleEditorScreen(ModalScreen[str | None]):
  """Text editor for a pipeline role snippet."""

  DEFAULT_CSS = """
  RoleEditorScreen {
    align: center middle;
  }

  #role-editor-dialog {
    width: 70;
    height: 30;
    border: thick $accent;
    background: $surface;
    padding: 1 2;
  }

  #role-text-area {
    height: 1fr;
    margin: 0 0 1 0;
  }

  #role-editor-buttons {
    height: auto;
    align: right middle;
  }

  #role-editor-buttons Button {
    margin: 0 1;
  }
  """

  BINDINGS = [
    Binding("escape", "cancel", "Cancel"),
  ]

  def __init__(
    self, heading: str, text: str
  ) -> None:
    """Initialize with role heading and text.

    Args:
      heading: Role heading (e.g. "Test Agent").
      text: Role snippet text.
    """
    super().__init__()
    self._heading = heading
    self._text = text

  def compose(self) -> ComposeResult:
    with Vertical(id="role-editor-dialog"):
      yield Static(
        f"Edit: {self._heading}",
        classes="panel-title",
      )
      yield TextArea(
        self._text, id="role-text-area"
      )
      with Horizontal(id="role-editor-buttons"):
        yield Button(
          "Cancel", variant="default", id="btn-cancel"
        )
        yield Button(
          "Save", variant="primary", id="btn-save"
        )

  def on_button_pressed(
    self, event: Button.Pressed
  ) -> None:
    if event.button.id == "btn-cancel":
      self.dismiss(None)
    elif event.button.id == "btn-save":
      area = self.query_one(
        "#role-text-area", TextArea
      )
      self.dismiss(area.text)

  def action_cancel(self) -> None:
    self.dismiss(None)


class CloneTargetScreen(ModalScreen[tuple | None]):
  """Modal to clone a VM from a template."""

  DEFAULT_CSS = """
  CloneTargetScreen {
    align: center middle;
  }

  #clone-dialog {
    width: 55;
    height: auto;
    border: thick $accent;
    background: $surface;
    padding: 1 2;
  }

  #clone-dialog Label {
    margin: 1 0 0 0;
  }

  #clone-dialog Input {
    margin: 0 0 1 0;
  }

  #clone-dialog Select {
    margin: 0 0 1 0;
  }

  #clone-buttons {
    height: auto;
    align: right middle;
  }

  #clone-buttons Button {
    margin: 0 1;
  }

  #clone-status {
    height: auto;
    margin: 1 0 0 0;
    color: $warning;
  }
  """

  BINDINGS = [
    Binding("escape", "cancel", "Cancel"),
  ]

  def compose(self) -> ComposeResult:
    with Vertical(id="clone-dialog"):
      yield Static(
        "Clone VM", classes="panel-title"
      )
      yield Label("Template:")
      yield Select([], id="clone-template-select")
      yield Label("Clone name:")
      yield Input(
        placeholder="deb-02",
        id="clone-name-input",
      )
      yield Label("IP address:")
      yield Input(
        placeholder="10.101.0.100",
        id="clone-ip-input",
      )
      yield Static("", id="clone-status")
      with Horizontal(id="clone-buttons"):
        yield Button(
          "Cancel", variant="default",
          id="btn-cancel",
        )
        yield Button(
          "Create", variant="primary",
          id="btn-create",
        )

  def on_mount(self) -> None:
    """Load template targets."""
    self._load_templates()

  @work(thread=True)
  def _load_templates(self) -> None:
    """Load template targets in worker."""
    from lib.target_ops import get_all_targets
    templates = [
      t for t in get_all_targets()
      if t.get("template")
    ]
    self.app.call_from_thread(
      self._populate, templates
    )

  def _populate(self, templates) -> None:
    """Populate template select."""
    tpl_select = self.query_one(
      "#clone-template-select", Select
    )
    tpl_select.set_options(
      [(t["name"], t["name"]) for t in templates]
    )
    self.query_one("#clone-name-input", Input).focus()

  def on_button_pressed(
    self, event: Button.Pressed
  ) -> None:
    if event.button.id == "btn-cancel":
      self.dismiss(None)
    elif event.button.id == "btn-create":
      self._validate_and_dismiss()

  def action_cancel(self) -> None:
    self.dismiss(None)

  def _validate_and_dismiss(self) -> None:
    """Validate inputs and dismiss with result."""
    tpl_select = self.query_one(
      "#clone-template-select", Select
    )
    name_input = self.query_one(
      "#clone-name-input", Input
    )
    ip_input = self.query_one(
      "#clone-ip-input", Input
    )

    template = tpl_select.value
    if template is Select.BLANK:
      self._set_status("Select a template.")
      return

    name = name_input.value.strip()
    if not name:
      self._set_status("Clone name is required.")
      return

    ip = ip_input.value.strip()
    if not ip:
      self._set_status("IP address is required.")
      return

    self.dismiss((str(template), name, ip))

  def _set_status(self, text: str) -> None:
    status = self.query_one("#clone-status", Static)
    status.update(text)


class PushGithubScreen(ModalScreen[str | None]):
  """Modal to push a branch to GitHub."""

  DEFAULT_CSS = """
  PushGithubScreen {
    align: center middle;
  }

  #push-dialog {
    width: 60;
    height: auto;
    border: thick $accent;
    background: $surface;
    padding: 1 2;
  }

  #push-dialog Label {
    margin: 1 0 0 0;
  }

  #push-dialog Select {
    margin: 0 0 1 0;
  }

  #push-buttons {
    height: auto;
    align: right middle;
  }

  #push-buttons Button {
    margin: 0 1;
  }

  #push-status {
    height: auto;
    margin: 1 0 0 0;
    color: $warning;
  }
  """

  BINDINGS = [
    Binding("escape", "cancel", "Cancel"),
  ]

  def compose(self) -> ComposeResult:
    with Vertical(id="push-dialog"):
      yield Static(
        "Push to GitHub", classes="panel-title"
      )
      yield Label("Workspace (branch):")
      yield Select([], id="push-ws-select")
      yield Static("", id="push-status")
      with Horizontal(id="push-buttons"):
        yield Button(
          "Cancel", variant="default", id="btn-cancel"
        )
        yield Button(
          "Push", variant="warning", id="btn-push"
        )

  def on_mount(self) -> None:
    self._load_workspaces()

  @work(thread=True)
  def _load_workspaces(self) -> None:
    from lib.workspace_ops import list_workspaces
    workspaces = list_workspaces()
    names = sorted(ws["name"] for ws in workspaces)
    self.app.call_from_thread(
      self._populate, names
    )

  def _populate(self, names) -> None:
    ws_select = self.query_one(
      "#push-ws-select", Select
    )
    ws_select.set_options(
      [(n, n) for n in names]
    )

  def on_button_pressed(
    self, event: Button.Pressed
  ) -> None:
    if event.button.id == "btn-cancel":
      self.dismiss(None)
    elif event.button.id == "btn-push":
      self._do_push()

  def action_cancel(self) -> None:
    self.dismiss(None)

  @work(thread=True)
  def _do_push(self) -> None:
    """Run push_to_github.py in worker thread."""
    ws_select = self.query_one(
      "#push-ws-select", Select
    )
    ws = ws_select.value
    if ws is Select.BLANK:
      self.app.call_from_thread(
        self._set_status, "Select a workspace."
      )
      return

    self.app.call_from_thread(
      self._set_status, f"Pushing {ws}..."
    )

    import subprocess
    from lib.config import PROJECT_DIR
    script = PROJECT_DIR / "bin" / "push_to_github.py"
    result = subprocess.run(
      ["python3", str(script), ws, "--yes"],
      capture_output=True, text=True,
    )
    if result.returncode == 0:
      self.app.call_from_thread(self.dismiss, ws)
    else:
      msg = result.stderr.strip() or "Push failed."
      self.app.call_from_thread(
        self._set_status, msg
      )

  def _set_status(self, text: str) -> None:
    status = self.query_one("#push-status", Static)
    status.update(text)
