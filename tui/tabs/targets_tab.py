"""Targets tab — full target management.

DataTable of all non-template targets with keybinding
actions for claim, release, start, stop, clone, and
delete. Inline forms replace modals. Polls target state
every 10s.
"""

import logging
import shutil
import subprocess

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import (
  DataTable, Input, Label, Select, Static,
)
from textual import work

from tui.mixins import TabBase

log = logging.getLogger("takt.targets_tab")


class TargetsTab(TabBase, Static):
  """Full target management tab."""

  _status_id = "targets-tab-status"

  BINDINGS = [
    Binding("c", "claim_target", "Claim"),
    Binding("x", "release_target", "Release"),
    Binding("u", "start_target", "Up"),
    Binding("o", "stop_target", "Down"),
    Binding("l", "clone_target", "Clone"),
    Binding("d", "delete_target", "Delete"),
    Binding("escape", "hide_forms", "Cancel",
            show=False),
  ]

  DEFAULT_CSS = """
  TargetsTab {
    height: 1fr;
    padding: 1 2;
  }

  TargetsTab #targets-tab-table {
    height: 1fr;
    background: #101010;
  }

  TargetsTab #targets-tab-status {
    height: auto;
    margin: 1 0 0 0;
    color: #cccccc;
  }

  TargetsTab #claim-form {
    height: auto;
    margin: 1 0;
    padding: 0 1;
    border: solid #2a2a2a;
  }

  TargetsTab #claim-form Label {
    margin: 0 1 0 0;
    padding: 1 0 0 0;
  }

  TargetsTab #claim-form Input {
    width: 40;
  }

  TargetsTab #clone-form {
    height: auto;
    margin: 1 0;
    padding: 0 1;
    border: solid #2a2a2a;
  }

  TargetsTab #clone-form Label {
    margin: 0 1 0 0;
    padding: 1 0 0 0;
  }

  TargetsTab #clone-form Select {
    width: 30;
  }

  TargetsTab #clone-form Input {
    width: 30;
  }
  """

  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    self._targets = []

  def compose(self) -> ComposeResult:
    yield Static("Targets", classes="panel-title")
    yield DataTable(id="targets-tab-table")
    with Vertical(id="claim-form"):
      with Horizontal():
        yield Label("Workspace:")
        yield Input(
          placeholder="workspace-name",
          id="claim-ws-input",
        )
    with Vertical(id="clone-form"):
      with Horizontal():
        yield Label("Template:")
        yield Select([], id="clone-tpl-select")
      with Horizontal():
        yield Label("Name:")
        yield Input(
          placeholder="deb-02",
          id="clone-name-input",
        )
      with Horizontal():
        yield Label("IP:")
        yield Input(
          placeholder="10.101.0.100",
          id="clone-ip-input",
        )
    yield Static("", id="targets-tab-status")

  def on_mount(self) -> None:
    """Set up table and start polling."""
    table = self.query_one(
      "#targets-tab-table", DataTable
    )
    table.cursor_type = "row"
    table.add_columns(
      "Name", "Type", "Host", "State", "Claimed By",
    )
    self.query_one("#claim-form").display = False
    self.query_one("#clone-form").display = False
    self.refresh_data()
    self.set_interval(10, self.refresh_data)

  @work(thread=True)
  def refresh_data(self) -> None:
    """Load target data in a worker thread."""
    from lib.target_ops import (
      get_all_targets, get_vm_state,
    )
    targets = [
      t for t in get_all_targets()
      if not t.get("template")
    ]
    for t in targets:
      if t["type"] == "vm":
        t["state"] = get_vm_state(t["name"]) or "?"
      else:
        t["state"] = "on"
    self.app.call_from_thread(
      self._update_table, targets
    )

  def _update_table(self, targets) -> None:
    """Update the table with fresh target data.

    Args:
      targets: List of target dicts.
    """
    self._targets = targets
    table = self.query_one(
      "#targets-tab-table", DataTable
    )
    old_row = 0
    if table.row_count > 0:
      old_row = table.cursor_row
    table.clear()
    for t in targets:
      lock = t["lock"]
      claimed = lock["workspace"] if lock else "-"
      table.add_row(
        t["name"], t["type"], t["host"],
        t["state"], claimed,
        key=t["name"],
      )
    if table.row_count > 0:
      row = min(old_row, table.row_count - 1)
      table.move_cursor(row=row)

  def _get_selected(self):
    """Return the name of the currently selected target.

    Returns:
      Target name string, or None.
    """
    table = self.query_one(
      "#targets-tab-table", DataTable
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

  def _get_target_info(self, name):
    """Return cached target dict by name.

    Args:
      name: Target name.

    Returns:
      Target dict, or None.
    """
    for t in self._targets:
      if t["name"] == name:
        return t
    return None

  def action_hide_forms(self) -> None:
    """Hide inline forms on Escape."""
    self.query_one("#claim-form").display = False
    self.query_one("#clone-form").display = False

  # -- Claim --

  def action_claim_target(self) -> None:
    """Show inline claim form for the selected target."""
    name = self._get_selected()
    if not name:
      self._set_status("No target selected.")
      return
    self.query_one("#clone-form").display = False
    self.query_one("#claim-form").display = True
    ws_input = self.query_one("#claim-ws-input", Input)
    ws_input.value = ""
    ws_input.focus()

  def on_input_submitted(
    self, event: Input.Submitted
  ) -> None:
    """Handle Enter in inline form inputs."""
    if event.input.id == "claim-ws-input":
      self._submit_claim()
    elif event.input.id == "clone-ip-input":
      self._submit_clone()

  def _submit_claim(self) -> None:
    """Process the inline claim form."""
    name = self._get_selected()
    if not name:
      self._set_status("No target selected.")
      return
    ws_input = self.query_one("#claim-ws-input", Input)
    workspace = ws_input.value.strip()
    if not workspace:
      self._set_status("Workspace name is required.")
      return
    from lib.target_ops import read_lock, write_lock
    lock = read_lock(name)
    if lock:
      self._set_status(
        f"Already claimed by '{lock['workspace']}'."
      )
      return
    try:
      write_lock(name, workspace)
      self.query_one("#claim-form").display = False
      self._set_status(f"Claimed {name}.")
      self.refresh_data()
    except Exception as e:
      self._set_status(f"Error: {e}")

  # -- Release --

  def action_release_target(self) -> None:
    """Release the selected target after confirmation."""
    name = self._get_selected()
    if not name:
      self._set_status("No target selected.")
      return
    info = self._get_target_info(name)
    if info and not info["lock"]:
      self._set_status(f"{name} is not claimed.")
      return
    self._confirm(
      f"Release target '{name}'?",
      self._on_release_confirmed,
      name,
    )

  def _on_release_confirmed(self, result) -> None:
    """Handle release confirmation."""
    if result:
      from lib.target_ops import release_lock
      release_lock(result)
      self._set_status(f"Released {result}.")
      self.refresh_data()

  # -- Start --

  def action_start_target(self) -> None:
    """Start the selected target."""
    name = self._get_selected()
    if not name:
      self._set_status("No target selected.")
      return
    info = self._get_target_info(name)
    if not info:
      return
    if info["type"] != "vm":
      self._set_status(
        f"{name} is hardware — start manually."
      )
      return
    self._set_status(f"Starting {name}...")
    self._run_virsh_start(name)

  @work(thread=True)
  def _run_virsh_start(self, name) -> None:
    """Run virsh start in a worker thread.

    Args:
      name: VM domain name.
    """
    if not shutil.which("virsh"):
      self.app.call_from_thread(
        self._set_status,
        "virsh not installed.",
      )
      return
    try:
      result = subprocess.run(
        ["virsh", "start", name],
        capture_output=True, text=True, timeout=30,
      )
      if result.returncode == 0:
        self.app.call_from_thread(
          self._set_status,
          f"{name} started.",
        )
      else:
        msg = result.stderr.strip() or "Start failed."
        self.app.call_from_thread(
          self._set_status, msg,
        )
    except subprocess.TimeoutExpired:
      self.app.call_from_thread(
        self._set_status,
        f"Timeout starting {name}.",
      )
    except Exception as e:
      self.app.call_from_thread(
        self._set_status, f"Error: {e}",
      )
    self.app.call_from_thread(self.refresh_data)

  # -- Stop --

  def action_stop_target(self) -> None:
    """Stop the selected target after confirmation."""
    name = self._get_selected()
    if not name:
      self._set_status("No target selected.")
      return
    info = self._get_target_info(name)
    if not info:
      return
    if info["type"] != "vm":
      self._set_status(
        f"{name} is hardware — stop manually."
      )
      return
    self._confirm(
      f"Shut down VM '{name}'?",
      self._on_stop_confirmed,
      name,
    )

  def _on_stop_confirmed(self, result) -> None:
    """Handle stop confirmation."""
    if result:
      self._set_status(f"Shutting down {result}...")
      self._run_virsh_shutdown(result)

  @work(thread=True)
  def _run_virsh_shutdown(self, name) -> None:
    """Run virsh shutdown in a worker thread.

    Args:
      name: VM domain name.
    """
    if not shutil.which("virsh"):
      self.app.call_from_thread(
        self._set_status,
        "virsh not installed.",
      )
      return
    try:
      result = subprocess.run(
        ["virsh", "shutdown", name],
        capture_output=True, text=True, timeout=30,
      )
      if result.returncode == 0:
        self.app.call_from_thread(
          self._set_status,
          f"{name} shutdown initiated.",
        )
      else:
        msg = result.stderr.strip() or "Shutdown failed."
        self.app.call_from_thread(
          self._set_status, msg,
        )
    except subprocess.TimeoutExpired:
      self.app.call_from_thread(
        self._set_status,
        f"Timeout shutting down {name}.",
      )
    except Exception as e:
      self.app.call_from_thread(
        self._set_status, f"Error: {e}",
      )
    self.app.call_from_thread(self.refresh_data)

  # -- Clone --

  def action_clone_target(self) -> None:
    """Show inline clone form."""
    self.query_one("#claim-form").display = False
    clone_form = self.query_one("#clone-form")
    clone_form.display = True
    self._load_templates()
    name_input = self.query_one(
      "#clone-name-input", Input
    )
    name_input.value = ""
    self.query_one("#clone-ip-input", Input).value = ""
    name_input.focus()

  @work(thread=True)
  def _load_templates(self) -> None:
    """Load template targets in worker."""
    from lib.target_ops import get_all_targets
    templates = [
      t for t in get_all_targets()
      if t.get("template")
    ]
    self.app.call_from_thread(
      self._populate_templates, templates
    )

  def _populate_templates(self, templates) -> None:
    """Populate template select.

    Args:
      templates: List of template target dicts.
    """
    tpl_select = self.query_one(
      "#clone-tpl-select", Select
    )
    tpl_select.set_options(
      [(t["name"], t["name"]) for t in templates]
    )

  def _submit_clone(self) -> None:
    """Process the inline clone form."""
    tpl_select = self.query_one(
      "#clone-tpl-select", Select
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
    self.query_one("#clone-form").display = False
    self._set_status(
      f"Cloning {template} -> {name} ({ip})..."
    )
    self._run_clone(str(template), name, ip)

  @work(thread=True)
  def _run_clone(self, template, name, ip) -> None:
    """Run clone_vm.create_clone in a worker.

    Args:
      template: Template target name.
      name: Clone name.
      ip: Static IP address.
    """
    from lib.config import PROJECT_DIR
    script = PROJECT_DIR / "bin" / "clone_vm.py"
    try:
      result = subprocess.run(
        [
          "sudo",
          "python3", str(script),
          "create", template,
          name, "--ip", ip,
        ],
        capture_output=True, text=True,
        timeout=300,
      )
      if result.returncode == 0:
        self.app.call_from_thread(
          self._set_status,
          f"Clone {name} created.",
        )
      else:
        msg = (
          result.stderr.strip()
          or result.stdout.strip()
          or "Clone failed."
        )
        self.app.call_from_thread(
          self._set_status, msg,
        )
    except subprocess.TimeoutExpired:
      self.app.call_from_thread(
        self._set_status,
        f"Timeout cloning {name}.",
      )
    except Exception as e:
      self.app.call_from_thread(
        self._set_status, f"Error: {e}",
      )
    self.app.call_from_thread(self.refresh_data)

  # -- Delete --

  def action_delete_target(self) -> None:
    """Delete the selected target after confirmation."""
    name = self._get_selected()
    if not name:
      self._set_status("No target selected.")
      return
    from lib.target_ops import is_template
    if is_template(name):
      self._set_status(
        f"{name} is a template — cannot delete."
      )
      return
    self._confirm(
      f"Delete clone '{name}'? This is irreversible.",
      self._on_delete_confirmed,
      name,
    )

  def _on_delete_confirmed(self, result) -> None:
    """Handle delete confirmation."""
    if result:
      self._set_status(f"Deleting {result}...")
      self._run_delete(result)

  @work(thread=True)
  def _run_delete(self, name) -> None:
    """Run clone_vm.delete_clone in a worker.

    Args:
      name: Clone target name.
    """
    from lib.config import PROJECT_DIR
    script = PROJECT_DIR / "bin" / "clone_vm.py"
    try:
      result = subprocess.run(
        [
          "sudo",
          "python3", str(script),
          "delete", name,
        ],
        capture_output=True, text=True,
        timeout=120,
      )
      if result.returncode == 0:
        self.app.call_from_thread(
          self._set_status,
          f"Clone {name} deleted.",
        )
      else:
        msg = (
          result.stderr.strip()
          or result.stdout.strip()
          or "Delete failed."
        )
        self.app.call_from_thread(
          self._set_status, msg,
        )
    except subprocess.TimeoutExpired:
      self.app.call_from_thread(
        self._set_status,
        f"Timeout deleting {name}.",
      )
    except Exception as e:
      self.app.call_from_thread(
        self._set_status, f"Error: {e}",
      )
    self.app.call_from_thread(self.refresh_data)
