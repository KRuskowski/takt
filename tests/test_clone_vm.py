"""Tests for bin/clone_vm.py."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))


TEMPLATE_CFG = {
  "type": "vm",
  "template": True,
  "disk": "",  # Set per test to a temp file.
  "host": "10.101.0.20",
  "user": "worker",
  "ssh_key": "~/.ssh/id_ed25519_targets",
  "description": "Debian 12 template",
}

WINDOWS_TEMPLATE_CFG = {
  "type": "vm",
  "template": True,
  "disk": "",
  "host": "10.101.0.21",
  "user": "worker",
  "ssh_key": "~/.ssh/id_ed25519_targets",
  "os": "windows",
  "description": "Windows 11 template",
}

CLONE_CFG = {
  "type": "vm",
  "host": "10.101.0.100",
  "user": "worker",
  "ssh_key": "~/.ssh/id_ed25519_targets",
  "disk": f"{Path.home()}/libvirt/images/deb-02.qcow2",
  "cloned_from": "deb-01",
  "description": "Clone of deb-01",
}


class TestCreateCloneValidation(unittest.TestCase):
  """Tests for create_clone input validation."""

  @mock.patch("bin.clone_vm.get_target", return_value=None)
  def test_rejects_missing_template(self, _):
    """create_clone exits if template not found."""
    from bin.clone_vm import create_clone
    with self.assertRaises(SystemExit):
      create_clone("nonexistent", "clone-01", "10.101.0.100")

  @mock.patch("bin.clone_vm.get_target", return_value={
    "type": "vm", "disk": "/tmp/x.qcow2",
  })
  def test_rejects_non_template(self, _):
    """create_clone exits if target is not a template."""
    from bin.clone_vm import create_clone
    with self.assertRaises(SystemExit):
      create_clone("deb-01", "clone-01", "10.101.0.100")

  def test_rejects_missing_disk(self):
    """create_clone exits if template disk doesn't exist."""
    from bin.clone_vm import create_clone
    cfg = {**TEMPLATE_CFG, "disk": "/nonexistent/disk.qcow2"}
    with mock.patch("bin.clone_vm.get_target", return_value=cfg):
      with self.assertRaises(SystemExit):
        create_clone("deb-01", "clone-01", "10.101.0.100")

  def test_rejects_duplicate_target(self):
    """create_clone exits if clone name already exists."""
    from bin.clone_vm import create_clone
    with tempfile.NamedTemporaryFile(suffix=".qcow2") as f:
      cfg = {**TEMPLATE_CFG, "disk": f.name}
      # First call returns template, second returns existing.
      with mock.patch(
        "bin.clone_vm.get_target",
        side_effect=[cfg, {"type": "vm"}],
      ):
        with self.assertRaises(SystemExit):
          create_clone("deb-01", "clone-01", "10.101.0.100")


class TestCreateCloneSteps(unittest.TestCase):
  """Tests for the create_clone orchestration steps."""

  def setUp(self):
    self.tmpdir = tempfile.mkdtemp()
    self.template_disk = Path(self.tmpdir) / "template.qcow2"
    self.template_disk.write_bytes(b"fake disk")
    self.clone_disk_dir = Path(self.tmpdir) / "clones"
    self.clone_disk_dir.mkdir()

    self.cfg = {
      **TEMPLATE_CFG,
      "disk": str(self.template_disk),
    }

  def tearDown(self):
    import shutil
    shutil.rmtree(self.tmpdir)

  @mock.patch("bin.clone_vm._wait_for_ssh")
  @mock.patch("bin.clone_vm._register_target")
  @mock.patch("bin.clone_vm._reconfigure_debian")
  @mock.patch("bin.clone_vm._clone_domain")
  @mock.patch("bin.clone_vm._create_backing_disk")
  @mock.patch("bin.clone_vm._shutdown_vm")
  @mock.patch("bin.clone_vm._run")
  @mock.patch("bin.clone_vm.get_target")
  @mock.patch("bin.clone_vm.CLONE_DISK_DIR")
  def test_debian_clone_steps(
    self, mock_dir, mock_get, mock_run, mock_shutdown,
    mock_backing, mock_clone_dom, mock_reconf, mock_register,
    mock_ssh,
  ):
    """Debian clone calls all steps in order."""
    from bin.clone_vm import create_clone
    mock_dir.__truediv__ = lambda s, n: self.clone_disk_dir / n
    # First call = template lookup, second = clone existence.
    mock_get.side_effect = [self.cfg, None]

    create_clone("deb-01", "deb-02", "10.101.0.100")

    mock_shutdown.assert_called_once_with("deb-01")
    mock_backing.assert_called_once()
    mock_clone_dom.assert_called_once_with(
      "deb-01", "deb-02", mock.ANY,
    )
    mock_reconf.assert_called_once_with(
      mock.ANY, "deb-02", "10.101.0.100",
    )
    mock_register.assert_called_once()
    mock_run.assert_called()  # virsh start
    mock_ssh.assert_called()

  @mock.patch("bin.clone_vm._wait_for_ssh")
  @mock.patch("bin.clone_vm._register_target")
  @mock.patch("bin.clone_vm._reconfigure_windows")
  @mock.patch("bin.clone_vm._clone_domain")
  @mock.patch("bin.clone_vm._create_backing_disk")
  @mock.patch("bin.clone_vm._shutdown_vm")
  @mock.patch("bin.clone_vm._run")
  @mock.patch("bin.clone_vm.get_target")
  @mock.patch("bin.clone_vm.CLONE_DISK_DIR")
  def test_windows_clone_uses_windows_reconfig(
    self, mock_dir, mock_get, mock_run, mock_shutdown,
    mock_backing, mock_clone_dom, mock_reconf, mock_register,
    mock_ssh,
  ):
    """Windows template uses _reconfigure_windows."""
    from bin.clone_vm import create_clone
    mock_dir.__truediv__ = lambda s, n: self.clone_disk_dir / n
    win_cfg = {
      **WINDOWS_TEMPLATE_CFG,
      "disk": str(self.template_disk),
    }
    mock_get.side_effect = [win_cfg, None]

    create_clone("win-01", "win-02", "10.101.0.101")

    mock_reconf.assert_called_once_with(
      "win-02", "10.101.0.101", win_cfg,
    )


class TestDeleteClone(unittest.TestCase):
  """Tests for delete_clone."""

  @mock.patch("bin.clone_vm.get_target", return_value=None)
  def test_rejects_missing_target(self, _):
    """delete_clone exits if target not found."""
    from bin.clone_vm import delete_clone
    with self.assertRaises(SystemExit):
      delete_clone("nonexistent")

  @mock.patch("bin.clone_vm.get_target", return_value={
    "type": "vm", "template": True, "disk": "/tmp/x.qcow2",
  })
  def test_rejects_template(self, _):
    """delete_clone exits if target is a template."""
    from bin.clone_vm import delete_clone
    with self.assertRaises(SystemExit):
      delete_clone("deb-01")

  @mock.patch("bin.clone_vm._unregister_target")
  @mock.patch("bin.clone_vm._run")
  @mock.patch("bin.clone_vm._shutdown_vm")
  @mock.patch("bin.clone_vm.get_target", return_value=CLONE_CFG)
  def test_delete_linux_clone(self, _, mock_shutdown, mock_run,
                              mock_unreg):
    """Deleting a Linux clone calls shutdown + undefine."""
    from bin.clone_vm import delete_clone
    with mock.patch("pathlib.Path.exists", return_value=False):
      delete_clone("deb-02")

    mock_shutdown.assert_called_once_with("deb-02")
    # undefine without --nvram --tpm for Linux.
    mock_run.assert_called_once_with(
      ["virsh", "undefine", "deb-02"], check=False,
    )
    mock_unreg.assert_called_once_with("deb-02")

  @mock.patch("bin.clone_vm._unregister_target")
  @mock.patch("bin.clone_vm._run")
  @mock.patch("bin.clone_vm._shutdown_vm")
  @mock.patch("bin.clone_vm.get_target", return_value={
    **CLONE_CFG, "os": "windows",
  })
  def test_delete_windows_clone_uses_nvram_tpm(
    self, _, mock_shutdown, mock_run, mock_unreg,
  ):
    """Deleting a Windows clone uses --nvram --tpm."""
    from bin.clone_vm import delete_clone
    with mock.patch("pathlib.Path.exists", return_value=False):
      delete_clone("win-02")

    mock_run.assert_called_once_with(
      ["virsh", "undefine", "win-02", "--nvram", "--tpm"],
      check=False,
    )


class TestHelperFunctions(unittest.TestCase):
  """Tests for internal helper functions."""

  @mock.patch("bin.clone_vm._run")
  def test_create_backing_disk(self, mock_run):
    """_create_backing_disk calls qemu-img create."""
    from bin.clone_vm import _create_backing_disk
    with tempfile.TemporaryDirectory() as d:
      clone_disk = Path(d) / "clone.qcow2"
      _create_backing_disk(
        Path("/var/lib/libvirt/images/tpl.qcow2"), clone_disk,
      )
      mock_run.assert_called_once_with([
        "qemu-img", "create",
        "-f", "qcow2",
        "-F", "qcow2",
        "-b", "/var/lib/libvirt/images/tpl.qcow2",
        str(clone_disk),
      ])

  @mock.patch("bin.clone_vm._run")
  def test_clone_domain(self, mock_run):
    """_clone_domain calls virt-clone with correct args."""
    from bin.clone_vm import _clone_domain
    _clone_domain("deb-01", "deb-02", Path("/tmp/deb-02.qcow2"))
    mock_run.assert_called_once_with([
      "virt-clone",
      "--original", "deb-01",
      "--name", "deb-02",
      "--preserve-data",
      "--file", "/tmp/deb-02.qcow2",
    ])

  @mock.patch("bin.clone_vm.save_targets_config")
  @mock.patch("bin.clone_vm.load_targets_config", return_value={
    "targets": {"deb-01": TEMPLATE_CFG},
  })
  def test_register_target(self, mock_load, mock_save):
    """_register_target adds clone to config."""
    from bin.clone_vm import _register_target
    _register_target(
      "deb-02", "deb-01", "10.101.0.100", TEMPLATE_CFG,
    )
    saved = mock_save.call_args[0][0]
    self.assertIn("deb-02", saved["targets"])
    self.assertEqual(
      saved["targets"]["deb-02"]["host"], "10.101.0.100",
    )
    self.assertEqual(
      saved["targets"]["deb-02"]["cloned_from"], "deb-01",
    )

  @mock.patch("bin.clone_vm.save_targets_config")
  @mock.patch("bin.clone_vm.load_targets_config", return_value={
    "targets": {
      "deb-01": TEMPLATE_CFG,
      "deb-02": CLONE_CFG,
    },
  })
  def test_unregister_target(self, mock_load, mock_save):
    """_unregister_target removes clone from config."""
    from bin.clone_vm import _unregister_target
    _unregister_target("deb-02")
    saved = mock_save.call_args[0][0]
    self.assertNotIn("deb-02", saved["targets"])
    self.assertIn("deb-01", saved["targets"])

  @mock.patch("bin.clone_vm._run")
  def test_virsh_state_running(self, mock_run):
    """_virsh_state returns state string."""
    from bin.clone_vm import _virsh_state
    mock_run.return_value = mock.Mock(
      returncode=0, stdout="running\n",
    )
    self.assertEqual(_virsh_state("deb-01"), "running")

  @mock.patch("bin.clone_vm._run")
  def test_virsh_state_not_found(self, mock_run):
    """_virsh_state returns None for missing domain."""
    from bin.clone_vm import _virsh_state
    mock_run.return_value = mock.Mock(
      returncode=1, stdout="",
    )
    self.assertIsNone(_virsh_state("nonexistent"))


class TestReconfigureDebian(unittest.TestCase):
  """Tests for _reconfigure_debian."""

  @mock.patch("shutil.which", return_value=None)
  def test_raises_without_virt_customize(self, _):
    """Raises RuntimeError if virt-customize not installed."""
    from bin.clone_vm import _reconfigure_debian
    with self.assertRaises(RuntimeError) as ctx:
      _reconfigure_debian(
        Path("/tmp/disk.qcow2"), "deb-02", "10.101.0.100",
      )
    self.assertIn("virt-customize", str(ctx.exception))

  @mock.patch("bin.clone_vm._run")
  @mock.patch("shutil.which", return_value="/usr/bin/virt-customize")
  def test_calls_virt_customize(self, _, mock_run):
    """Calls virt-customize with hostname and IP sed."""
    from bin.clone_vm import _reconfigure_debian
    _reconfigure_debian(
      Path("/tmp/disk.qcow2"), "deb-02", "10.101.0.100",
    )
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    self.assertEqual(cmd[0], "virt-customize")
    self.assertIn("--hostname", cmd)
    self.assertIn("deb-02", cmd)


if __name__ == "__main__":
  unittest.main()
