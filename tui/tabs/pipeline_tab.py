"""Pipeline tab — inline pipeline editor.

Create, reorder, and edit pipeline steps per workspace.
Role text is edited in-place via a TextArea below the
steps table. No modal dialogs — everything is inline.
"""

import json
import logging

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import (
  DataTable,
  Label,
  Select,
  Static,
  TextArea,
)
from textual import work

from tui.mixins import TabBase

log = logging.getLogger("takt.pipeline_tab")


class PipelineTab(TabBase, Static):
  """Inline pipeline editor with step table and role area."""

  _status_id = "pl-status"

  DEFAULT_CSS = """
  PipelineTab {
    height: 1fr;
    padding: 1 2;
  }

  PipelineTab #pl-ws-row {
    height: auto;
    margin: 0 0 1 0;
  }

  PipelineTab #pl-ws-row Label {
    margin: 0 1 0 0;
    padding: 1 0 0 0;
  }

  PipelineTab #pl-ws-row Select {
    width: 40;
  }

  PipelineTab #pl-steps-table {
    height: 10;
    background: #101010;
    margin: 0 0 1 0;
  }

  PipelineTab #pl-step-row {
    height: auto;
    margin: 0 0 1 0;
  }

  PipelineTab #pl-step-row Select {
    width: 40;
  }

  PipelineTab #pl-model-row {
    height: auto;
    margin: 0 0 1 0;
  }

  PipelineTab #pl-model-row Label {
    margin: 0 1 0 0;
    padding: 1 0 0 0;
  }

  PipelineTab #pl-model-row Select {
    width: 30;
  }

  PipelineTab #pl-role-heading {
    text-style: bold;
    color: #cccccc;
    margin: 1 0 0 0;
  }

  PipelineTab #pl-role-area {
    height: 10;
    margin: 0 0 1 0;
  }

  PipelineTab #pl-status {
    height: auto;
    margin: 1 0 0 0;
    color: $warning;
  }
  """

  BINDINGS = [
    Binding("a", "add_step", "Add"),
    Binding("x", "remove_step", "Remove"),
    Binding("j", "move_down", "Move Down", show=False),
    Binding("k", "move_up", "Move Up", show=False),
    Binding(
      "ctrl+s", "save_pipeline", "Save", show=True
    ),
    Binding("d", "delete_pipeline", "Delete"),
  ]

  def __init__(self, **kwargs) -> None:
    super().__init__(**kwargs)
    self._steps: list[dict] = []
    self._roles: list[dict] = []
    self._roles_dirty = False
    self._editing_role: dict | None = None

  def compose(self) -> ComposeResult:
    with Horizontal(id="pl-ws-row"):
      yield Label("Workspace:")
      yield Select([], id="pl-ws-select")
    yield DataTable(
      id="pl-steps-table", cursor_type="row"
    )
    with Horizontal(id="pl-step-row"):
      yield Select(
        [], id="pl-add-select", prompt="step..."
      )
    with Horizontal(id="pl-model-row"):
      yield Label("Model:")
      yield Select(
        [("sonnet", "sonnet"), ("opus", "opus"),
         ("haiku", "haiku")],
        id="pl-model-select",
        value="sonnet",
      )
    yield Static("", id="pl-role-heading")
    yield TextArea("", id="pl-role-area")
    yield Static("", id="pl-status")

  def on_mount(self) -> None:
    """Set up table columns and load data."""
    table = self.query_one(
      "#pl-steps-table", DataTable
    )
    table.add_columns("#", "Name", "Type")
    area = self.query_one("#pl-role-area", TextArea)
    area.read_only = True
    area.display = False
    model_row = self.query_one("#pl-model-row")
    model_row.display = False
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
    ws_select = self.query_one("#pl-ws-select", Select)
    ws_select.set_options(
      [(w, w) for w in ws_names]
    )
    self._refresh_add_select()

  def refresh_data(self) -> None:
    """Reload workspace list and roles."""
    self._load_data()

  def select_workspace(self, ws_name) -> None:
    """Programmatically select a workspace.

    Args:
      ws_name: Workspace name to select.
    """
    ws_select = self.query_one("#pl-ws-select", Select)
    ws_select.value = ws_name

  # -- Add-step Select --

  def _refresh_add_select(self) -> None:
    """Repopulate the add-step Select with available steps."""
    from lib.pipeline import SCRIPT_REGISTRY
    existing = {s["name"] for s in self._steps}
    options = []
    for role in self._roles:
      if role["slug"] not in existing:
        options.append((role["slug"], role["slug"]))
    for name in sorted(SCRIPT_REGISTRY):
      if name not in existing:
        options.append((name, name))
    add_select = self.query_one(
      "#pl-add-select", Select
    )
    add_select.set_options(options)

  # -- Workspace change --

  def on_select_changed(
    self, event: Select.Changed
  ) -> None:
    """Handle Select changes for workspace and model."""
    if event.select.id == "pl-add-select":
      return
    if event.select.id == "pl-model-select":
      self._on_model_changed(event.value)
      return
    if event.select.id != "pl-ws-select":
      return
    self._hide_role_editor()
    self._hide_model_select()
    if event.value is Select.BLANK:
      self._steps = []
      self._rebuild_table()
      self._refresh_add_select()
      return
    self._load_pipeline(str(event.value))

  def _on_model_changed(self, value) -> None:
    """Write model selection back to current step config."""
    if value is Select.BLANK:
      return
    table = self.query_one(
      "#pl-steps-table", DataTable
    )
    idx = table.cursor_row
    if idx < 0 or idx >= len(self._steps):
      return
    step = self._steps[idx]
    if step["step_type"] != "agent":
      return
    step.setdefault("config", {})["model"] = str(value)

  @work(thread=True)
  def _load_pipeline(self, ws_name) -> None:
    """Load existing pipeline from DB."""
    from lib import db
    pipeline = db.get_pipeline(ws_name)
    steps = [
      {
        "name": s["name"],
        "step_type": s["step_type"],
        "config": json.loads(s["config_json"]),
      }
      for s in pipeline
    ] if pipeline else []
    self.app.call_from_thread(
      self._apply_steps, steps
    )

  def _apply_steps(self, steps) -> None:
    """Set steps and rebuild the table."""
    self._steps = steps
    self._rebuild_table()
    self._refresh_add_select()
    self._set_status("")

  def _rebuild_table(self) -> None:
    """Rebuild the DataTable from self._steps."""
    table = self.query_one(
      "#pl-steps-table", DataTable
    )
    table.clear()
    for i, step in enumerate(self._steps):
      table.add_row(
        str(i + 1), step["name"], step["step_type"],
      )

  # -- Row highlight => auto-load role --

  def on_data_table_row_highlighted(
    self, event: DataTable.RowHighlighted
  ) -> None:
    """Auto-show role text and model selector on cursor move."""
    if event.data_table.id != "pl-steps-table":
      return
    idx = event.cursor_row
    if idx < 0 or idx >= len(self._steps):
      self._hide_role_editor()
      self._hide_model_select()
      return
    step = self._steps[idx]
    if step["step_type"] != "agent":
      self._hide_role_editor()
      self._hide_model_select()
      return
    self._show_model_select(step)
    role = next(
      (r for r in self._roles
       if r["slug"] == step["name"]),
      None,
    )
    if role is None:
      self._hide_role_editor()
      return
    self._show_role_editor(role)

  # -- Model select --

  def _show_model_select(self, step) -> None:
    """Show model Select and load the step's model."""
    model_row = self.query_one("#pl-model-row")
    model_row.display = True
    model_sel = self.query_one(
      "#pl-model-select", Select
    )
    config = step.get("config", {})
    model = config.get("model", "sonnet")
    model_sel.value = model

  def _hide_model_select(self) -> None:
    """Hide the model Select row."""
    model_row = self.query_one("#pl-model-row")
    model_row.display = False

  # -- Step reordering --

  def action_move_up(self) -> None:
    """Move the selected step up."""
    area = self.query_one("#pl-role-area", TextArea)
    if area.has_focus:
      return
    table = self.query_one(
      "#pl-steps-table", DataTable
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
    area = self.query_one("#pl-role-area", TextArea)
    if area.has_focus:
      return
    table = self.query_one(
      "#pl-steps-table", DataTable
    )
    idx = table.cursor_row
    if idx >= len(self._steps) - 1:
      return
    self._steps[idx], self._steps[idx + 1] = (
      self._steps[idx + 1], self._steps[idx]
    )
    self._rebuild_table()
    table.move_cursor(row=idx + 1)

  # -- Keybinding actions --

  def action_add_step(self) -> None:
    """Add the step selected in the inline dropdown."""
    from lib.pipeline import SCRIPT_REGISTRY
    add_select = self.query_one(
      "#pl-add-select", Select
    )
    val = add_select.value
    if val is Select.BLANK:
      self._set_status("Select a step to add.")
      return
    name = str(val)
    role = next(
      (r for r in self._roles
       if r["slug"] == name),
      None,
    )
    if role:
      step_type = "agent"
    elif name in SCRIPT_REGISTRY:
      step_type = "script"
    else:
      self._set_status(f"Unknown step: {name}")
      return
    if step_type == "agent":
      config = {"model": "sonnet"}
    else:
      config = {}
    step = {
      "name": name,
      "step_type": step_type,
      "config": config,
    }
    self._steps.append(step)
    self._rebuild_table()
    self._refresh_add_select()
    self._set_status(f"Added {name}.")

  def action_remove_step(self) -> None:
    """Remove the selected step."""
    table = self.query_one(
      "#pl-steps-table", DataTable
    )
    if not self._steps:
      return
    idx = table.cursor_row
    if 0 <= idx < len(self._steps):
      removed = self._steps.pop(idx)
      self._rebuild_table()
      self._refresh_add_select()
      editing = self._editing_role
      if editing and editing["slug"] == removed["name"]:
        self._hide_role_editor()
      self._set_status(f"Removed {removed['name']}.")

  # -- Role editor --

  def _show_role_editor(self, role) -> None:
    """Display the role text in the inline TextArea."""
    self._save_role_edits()
    if self._editing_role is role:
      return
    self._editing_role = role
    heading = self.query_one(
      "#pl-role-heading", Static
    )
    heading.update(f"Role: {role['heading']}")
    area = self.query_one("#pl-role-area", TextArea)
    area.read_only = False
    area.display = True
    area.load_text(role["text"])

  def _hide_role_editor(self) -> None:
    """Hide the role editor area."""
    self._save_role_edits()
    self._editing_role = None
    heading = self.query_one(
      "#pl-role-heading", Static
    )
    heading.update("")
    area = self.query_one("#pl-role-area", TextArea)
    area.read_only = True
    area.display = False
    area.load_text("")

  def _save_role_edits(self) -> None:
    """Persist TextArea content back to the role dict."""
    if self._editing_role is None:
      return
    area = self.query_one("#pl-role-area", TextArea)
    new_text = area.text
    if new_text != self._editing_role["text"]:
      self._editing_role["text"] = new_text
      self._roles_dirty = True

  # -- Save / Delete --

  def action_save_pipeline(self) -> None:
    """Save pipeline definition and roles."""
    self._do_save()

  @work(thread=True)
  def _do_save(self) -> None:
    """Save pipeline definition and roles."""
    ws_select = self.query_one("#pl-ws-select", Select)
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

    self.app.call_from_thread(self._save_role_edits)

    from lib import db
    from lib.config import save_pipeline_roles
    try:
      db.define_pipeline(ws, self._steps)
      if self._roles_dirty:
        save_pipeline_roles(self._roles)
        self._roles_dirty = False
      self.app.call_from_thread(
        self._set_status,
        f"Saved pipeline for {ws} "
        f"({len(self._steps)} steps).",
      )
    except Exception as e:
      self.app.call_from_thread(
        self._set_status, f"Error: {e}"
      )

  def action_delete_pipeline(self) -> None:
    """Delete pipeline after inline confirmation."""
    ws_select = self.query_one("#pl-ws-select", Select)
    ws = ws_select.value
    if ws is Select.BLANK:
      self._set_status("Select a workspace.")
      return
    self._confirm(
      f"Delete pipeline for '{ws}'?",
      self._on_delete_confirmed,
      str(ws),
    )

  def _on_delete_confirmed(self, result) -> None:
    """Handle delete confirmation.

    Args:
      result: Workspace name string.
    """
    if result is None:
      return
    self._hide_role_editor()
    self._delete_pipeline(result)

  @work(thread=True)
  def _delete_pipeline(self, ws) -> None:
    """Delete pipeline in worker."""
    from lib import db
    try:
      db.define_pipeline(ws, [])
      self.app.call_from_thread(self._apply_steps, [])
      self.app.call_from_thread(
        self._set_status,
        f"Deleted pipeline for {ws}.",
      )
    except Exception as e:
      self.app.call_from_thread(
        self._set_status, f"Error: {e}"
      )
