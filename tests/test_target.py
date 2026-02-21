"""Tests for bin/target.py template guards."""

import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))


TEMPLATE_TARGET = {
  "type": "vm",
  "template": True,
  "disk": "/var/lib/libvirt/images/deb-01.qcow2",
  "host": "10.101.0.20",
  "user": "worker",
  "ssh_key": "~/.ssh/id_ed25519_targets",
  "description": "Debian 12 template",
}

NORMAL_TARGET = {
  "type": "vm",
  "host": "10.101.0.100",
  "user": "worker",
  "ssh_key": "~/.ssh/id_ed25519_targets",
  "description": "Clone of deb-01",
}


class TestTemplateGuardClaim(unittest.TestCase):
  """Template targets cannot be claimed."""

  @mock.patch("bin.target.get_target", return_value=TEMPLATE_TARGET)
  def test_claim_template_rejected(self, _):
    """Claiming a template exits with error."""
    from bin.target import cmd_claim
    args = mock.Mock(name="deb-01", workspace="ws")
    args.name = "deb-01"
    args.workspace = "ws"
    with self.assertRaises(SystemExit):
      cmd_claim(args)

  @mock.patch("bin.target.write_lock")
  @mock.patch("bin.target.read_lock", return_value=None)
  @mock.patch("bin.target.get_target", return_value=NORMAL_TARGET)
  def test_claim_normal_allowed(self, _, __, mock_write):
    """Claiming a normal target succeeds."""
    from bin.target import cmd_claim
    args = mock.Mock()
    args.name = "deb-02"
    args.workspace = "ws"
    cmd_claim(args)
    mock_write.assert_called_once_with("deb-02", "ws")


class TestTemplateGuardUp(unittest.TestCase):
  """Template targets cannot be started."""

  @mock.patch("bin.target.get_target", return_value=TEMPLATE_TARGET)
  def test_up_template_rejected(self, _):
    """Starting a template exits with error."""
    from bin.target import cmd_up
    args = mock.Mock()
    args.name = "deb-01"
    with self.assertRaises(SystemExit):
      cmd_up(args)


class TestTemplateGuardRun(unittest.TestCase):
  """Template targets cannot have commands run on them."""

  @mock.patch("bin.target.get_target", return_value=TEMPLATE_TARGET)
  def test_run_template_rejected(self, _):
    """Running on a template exits with error."""
    from bin.target import cmd_run
    args = mock.Mock()
    args.name = "deb-01"
    args.command = "hostname"
    with self.assertRaises(SystemExit):
      cmd_run(args)


class TestTemplateListDisplay(unittest.TestCase):
  """Template status is shown in list output."""

  @mock.patch("bin.target.get_vm_state", return_value="shut off")
  @mock.patch("bin.target.get_all_targets", return_value=[
    {
      "name": "deb-01",
      "type": "vm",
      "host": "10.101.0.20",
      "user": "worker",
      "port": None,
      "description": "Debian 12 template",
      "template": True,
      "lock": None,
    },
    {
      "name": "deb-02",
      "type": "vm",
      "host": "10.101.0.100",
      "user": "worker",
      "port": None,
      "description": "Clone of deb-01",
      "template": False,
      "lock": None,
    },
  ])
  def test_list_shows_template_tag(self, _, __):
    """List output includes [template] tag."""
    from bin.target import cmd_list
    captured = StringIO()
    with mock.patch("sys.stdout", captured):
      cmd_list(mock.Mock())
    output = captured.getvalue()
    self.assertIn("[template]", output)
    # The clone line should NOT have [template].
    for line in output.splitlines():
      if "deb-02" in line:
        self.assertNotIn("[template]", line)

  @mock.patch("bin.target.get_vm_state", return_value="running")
  @mock.patch("bin.target.get_all_targets", return_value=[
    {
      "name": "deb-02",
      "type": "vm",
      "host": "10.101.0.100",
      "user": "worker",
      "port": None,
      "description": "Clone of deb-01",
      "template": False,
      "lock": None,
    },
  ])
  def test_list_shows_vm_state(self, _, __):
    """List output includes VM state."""
    from bin.target import cmd_list
    captured = StringIO()
    with mock.patch("sys.stdout", captured):
      cmd_list(mock.Mock())
    output = captured.getvalue()
    self.assertIn("running", output)


class TestTemplateStatusDisplay(unittest.TestCase):
  """Template status is shown in status output."""

  @mock.patch("bin.target.check_connectivity", return_value=False)
  @mock.patch("bin.target.get_vm_state", return_value="shut off")
  @mock.patch("bin.target.read_lock", return_value=None)
  @mock.patch("bin.target.get_target", return_value=TEMPLATE_TARGET)
  def test_status_shows_template(self, _, __, ___, ____):
    """Status output includes 'Template: yes'."""
    from bin.target import cmd_status
    args = mock.Mock()
    args.name = "deb-01"
    captured = StringIO()
    with mock.patch("sys.stdout", captured):
      cmd_status(args)
    output = captured.getvalue()
    self.assertIn("Template: yes", output)

  @mock.patch("bin.target.check_connectivity", return_value=True)
  @mock.patch("bin.target.get_vm_state", return_value="running")
  @mock.patch("bin.target.read_lock", return_value=None)
  @mock.patch("bin.target.get_target", return_value=NORMAL_TARGET)
  def test_status_shows_vm_state(self, _, __, ___, ____):
    """Status output includes VM state."""
    from bin.target import cmd_status
    args = mock.Mock()
    args.name = "deb-02"
    captured = StringIO()
    with mock.patch("sys.stdout", captured):
      cmd_status(args)
    output = captured.getvalue()
    self.assertIn("State: running", output)
    self.assertNotIn("Template:", output)


if __name__ == "__main__":
  unittest.main()
