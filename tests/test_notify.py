"""Tests for lib/notify.py."""

import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib.notify import notify


class TestNotify(unittest.TestCase):
  """Tests for notify()."""

  @mock.patch("lib.notify.subprocess.run")
  @mock.patch("lib.notify.shutil.which", return_value="/usr/bin/notify-send")
  def test_sends_notification(self, mock_which, mock_run):
    """Calls notify-send with correct arguments."""
    mock_run.return_value = mock.Mock(returncode=0)
    result = notify("Title", "Body")
    self.assertTrue(result)
    mock_run.assert_called_once_with(
      [
        "notify-send",
        "--urgency", "normal",
        "Title",
        "Body",
      ],
      timeout=5,
      capture_output=True,
    )

  @mock.patch("lib.notify.subprocess.run")
  @mock.patch("lib.notify.shutil.which", return_value="/usr/bin/notify-send")
  def test_passes_urgency(self, mock_which, mock_run):
    """Urgency parameter is forwarded."""
    mock_run.return_value = mock.Mock(returncode=0)
    notify("T", "B", urgency="critical")
    args = mock_run.call_args[0][0]
    self.assertIn("critical", args)

  @mock.patch("lib.notify.shutil.which", return_value=None)
  def test_returns_false_when_not_installed(
    self, mock_which,
  ):
    """Returns False if notify-send not found."""
    result = notify("T", "B")
    self.assertFalse(result)

  @mock.patch("lib.notify.subprocess.run")
  @mock.patch("lib.notify.shutil.which", return_value="/usr/bin/notify-send")
  def test_handles_timeout(self, mock_which, mock_run):
    """Returns False on subprocess timeout."""
    import subprocess
    mock_run.side_effect = subprocess.TimeoutExpired(
      "notify-send", 5,
    )
    result = notify("T", "B")
    self.assertFalse(result)

  @mock.patch("lib.notify.subprocess.run")
  @mock.patch("lib.notify.shutil.which", return_value="/usr/bin/notify-send")
  def test_handles_oserror(self, mock_which, mock_run):
    """Returns False on OSError."""
    mock_run.side_effect = OSError("no such file")
    result = notify("T", "B")
    self.assertFalse(result)


if __name__ == "__main__":
  unittest.main()
