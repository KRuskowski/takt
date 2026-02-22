"""Targets tab — full target management.

DataTable of all non-template targets with action buttons
for claim, release, start, stop, clone, and delete. Polls
target state every 10s.
"""

import logging
import shutil
import subprocess

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Static
from textual import work

log = logging.getLogger("takt.targets_tab")


class TargetsTab(Static):
  """Full target management tab."""

  DEFAULT_CSS = """
  TargetsTab {
    height: 1fr;
    padding: 1 2;
  }

  TargetsTab #targets-tab-table {
    height: 1fr;
    background: #101010;
  }

  TargetsTab #targets-tab-buttons {
    height: auto;
    margin: 1 0;
  }

  TargetsTab #targets-tab-buttons Button {
    margin: 0 1 0 0;
  }

  TargetsTab #targets-tab-status {
    height: auto;
    margin: 1 0 0 0;
    color: #cccccc;
  }
  """

  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    self._targets = []

  def compose(self) -> ComposeResult:
    yield Static("Targets", classes="panel-title")
    yield DataTable(id="targets-tab-table")
    with Horizontal(id="targets-tab-buttons"):
      yield Button(
        "Claim", variant="primary",
        id="btn-claim",
      )
      yield Button(
        "Release", variant="default",
        id="btn-release",
      )
      yield Button(
        "Start", variant="success",
        id="btn-start",
      )
      yield Button(
        "Stop", variant="warning",
        id="btn-stop",
      )
      yield Button(
        "Clone", variant="default",
        id="btn-clone",
      )
      yield Button(
        "Delete", variant="error",
        id="btn-delete",
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
    # Preserve cursor position.
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

  def _set_status(self, text: str) -> None:
    """Update the status line.

    Args:
      text: Status message.
    """
    status = self.query_one(
      "#targets-tab-status", Static
    )
    status.update(text)

  def on_button_pressed(
    self, event: Button.Pressed
  ) -> None:
    """Handle action button presses."""
    bid = event.button.id
    if bid == "btn-claim":
      self._do_claim()
    elif bid == "btn-release":
      self._do_release()
    elif bid == "btn-start":
      self._do_start()
    elif bid == "btn-stop":
      self._do_stop()
    elif bid == "btn-clone":
      self._do_clone()
    elif bid == "btn-delete":
      self._do_delete()

  # -- Claim --

  def _do_claim(self) -> None:
    """Open claim modal for the selected target."""
    name = self._get_selected()
    if not name:
      self._set_status("No target selected.")
      return
    from tui.screens import ClaimTargetScreen
    self.app.push_screen(
      ClaimTargetScreen(name),
      callback=self._on_claimed,
    )

  def _on_claimed(self, result) -> None:
    """Handle claim callback."""
    if result:
      self._set_status(f"Claimed {result}.")
      self.refresh_data()

  # -- Release --

  def _do_release(self) -> None:
    """Release the selected target after confirmation."""
    name = self._get_selected()
    if not name:
      self._set_status("No target selected.")
      return
    info = self._get_target_info(name)
    if info and not info["lock"]:
      self._set_status(f"{name} is not claimed.")
      return
    from tui.screens import ConfirmScreen
    self.app.push_screen(
      ConfirmScreen(
        f"Release target '{name}'?", name,
      ),
      callback=self._on_release_confirmed,
    )

  def _on_release_confirmed(self, result) -> None:
    """Handle release confirmation."""
    if result:
      from lib.target_ops import release_lock
      release_lock(result)
      self._set_status(f"Released {result}.")
      self.refresh_data()

  # -- Start --

  def _do_start(self) -> None:
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

  def _do_stop(self) -> None:
    """Stop the selected target."""
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
    from tui.screens import ConfirmScreen
    self.app.push_screen(
      ConfirmScreen(
        f"Shut down VM '{name}'?", name,
      ),
      callback=self._on_stop_confirmed,
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

  def _do_clone(self) -> None:
    """Open clone modal."""
    from tui.screens import CloneTargetScreen
    self.app.push_screen(
      CloneTargetScreen(),
      callback=self._on_clone_result,
    )

  def _on_clone_result(self, result) -> None:
    """Handle clone modal result."""
    if not result:
      return
    template, name, ip = result
    self._set_status(
      f"Cloning {template} -> {name} ({ip})..."
    )
    self._run_clone(template, name, ip)

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

  def _do_delete(self) -> None:
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
    from tui.screens import ConfirmScreen
    self.app.push_screen(
      ConfirmScreen(
        f"Delete clone '{name}'? This is irreversible.",
        name,
      ),
      callback=self._on_delete_confirmed,
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
