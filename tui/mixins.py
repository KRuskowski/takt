"""Mixins for TUI tabs."""

from textual.widgets import Static


class TabBase:
  """Inline y/n confirmation mixin for tab widgets.

  Provides a confirmation prompt that intercepts keys,
  replacing modal ConfirmScreen dialogs. Subclasses must
  set _status_id to the DOM id of their status Static.
  """

  _confirm_active = False
  _confirm_callback = None
  _confirm_data = None
  _status_id = None

  def _confirm(self, msg, callback, data=None):
    """Show an inline confirmation prompt.

    Args:
      msg: Message to display (y/n appended).
      callback: Callable to invoke on 'y', receives data.
      data: Arbitrary data passed to callback.
    """
    self._confirm_active = True
    self._confirm_callback = callback
    self._confirm_data = data
    self._set_status(f"{msg} (y/n)")

  def on_key(self, event):
    """Intercept keys during active confirmation."""
    if not self._confirm_active:
      return
    event.stop()
    event.prevent_default()
    if event.key == "y":
      cb, data = self._confirm_callback, self._confirm_data
      self._confirm_active = False
      self._set_status("")
      cb(data)
    elif event.key in ("n", "escape"):
      self._confirm_active = False
      self._set_status("")

  def _set_status(self, text):
    """Update the inline status label.

    Args:
      text: Status message.
    """
    status = self.query_one(f"#{self._status_id}", Static)
    status.update(text)
