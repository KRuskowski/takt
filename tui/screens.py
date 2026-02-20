"""Modal screens for the dashboard."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import (
  Button,
  Input,
  Label,
  SelectionList,
  Static,
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
      yield Static("Create Workspace", classes="panel-title")
      yield Label("Workspace name (= branch name):")
      yield Input(
        placeholder="feature-name", id="ws-name-input"
      )
      yield Label("Select repos:")
      yield SelectionList[str](id="repo-selection")
      yield Static("", id="create-ws-status")
      with Horizontal(id="create-ws-buttons"):
        yield Button("Cancel", variant="default", id="btn-cancel")
        yield Button("Create", variant="primary", id="btn-create")

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

  def on_button_pressed(self, event: Button.Pressed) -> None:
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
        placeholder="workspace-name", id="claim-ws-input"
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

  def on_button_pressed(self, event: Button.Pressed) -> None:
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
        yield Button("No", variant="default", id="btn-no")
        yield Button("Yes", variant="warning", id="btn-yes")

  def on_button_pressed(self, event: Button.Pressed) -> None:
    if event.button.id == "btn-yes":
      self.dismiss(self.data)
    else:
      self.dismiss(None)

  def action_confirm(self) -> None:
    self.dismiss(self.data)

  def action_cancel(self) -> None:
    self.dismiss(None)
