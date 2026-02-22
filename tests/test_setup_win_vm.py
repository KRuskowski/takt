"""Tests for bin/setup_win_vm.py."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "bin"))

import setup_win_vm


class TestIsRoot(unittest.TestCase):
  """Tests for is_root()."""

  @mock.patch("os.geteuid", return_value=0)
  def test_root(self, mock_euid):
    """Returns True when root."""
    self.assertTrue(setup_win_vm.is_root())

  @mock.patch("os.geteuid", return_value=1000)
  def test_not_root(self, mock_euid):
    """Returns False when not root."""
    self.assertFalse(setup_win_vm.is_root())


class TestVmExists(unittest.TestCase):
  """Tests for vm_exists()."""

  @mock.patch("setup_win_vm.run")
  def test_returns_true_when_exists(self, mock_run):
    """Returns True when virsh dominfo succeeds."""
    mock_run.return_value = mock.Mock(returncode=0)
    self.assertTrue(setup_win_vm.vm_exists("win-01"))

  @mock.patch("setup_win_vm.run")
  def test_returns_false_when_missing(self, mock_run):
    """Returns False when virsh dominfo fails."""
    mock_run.return_value = mock.Mock(returncode=1)
    self.assertFalse(setup_win_vm.vm_exists("win-01"))


class TestEnsurePackages(unittest.TestCase):
  """Tests for ensure_packages()."""

  @mock.patch("setup_win_vm.run")
  def test_skips_when_installed(self, mock_run):
    """Skips install when all packages present."""
    mock_run.return_value = mock.Mock(returncode=0)
    setup_win_vm.ensure_packages()
    self.assertEqual(mock_run.call_count, 1)
    args = mock_run.call_args[0][0]
    self.assertEqual(args[0], "dpkg")

  @mock.patch("setup_win_vm.run")
  def test_installs_when_missing(self, mock_run):
    """Installs packages when dpkg check fails."""
    mock_run.side_effect = [
      mock.Mock(returncode=1),  # dpkg -s fails
      mock.Mock(returncode=0),  # apt-get update
      mock.Mock(returncode=0),  # apt-get install
    ]
    setup_win_vm.ensure_packages()
    self.assertEqual(mock_run.call_count, 3)
    args = mock_run.call_args_list[1][0][0]
    self.assertIn("update", args)


class TestCreateStorageDir(unittest.TestCase):
  """Tests for create_storage_dir()."""

  @mock.patch("setup_win_vm.run")
  @mock.patch.object(Path, "read_text")
  @mock.patch.object(Path, "exists")
  def test_skips_when_exists_and_apparmor_present(
    self, mock_exists, mock_read, mock_run,
  ):
    """No-op when dir and AppArmor rule exist."""
    mock_exists.return_value = True
    mock_read.return_value = str(setup_win_vm.IMAGES_DIR)
    setup_win_vm.create_storage_dir()
    mock_run.assert_not_called()

  @mock.patch("setup_win_vm.run")
  @mock.patch("builtins.open", mock.mock_open())
  @mock.patch.object(Path, "read_text", return_value="")
  @mock.patch.object(Path, "mkdir")
  @mock.patch.object(Path, "exists", return_value=True)
  def test_adds_apparmor_rule(
    self, mock_exists, mock_mkdir, mock_read, mock_run,
  ):
    """Adds AppArmor rule when not present."""
    setup_win_vm.create_storage_dir()
    # Should call apparmor_parser to reload.
    apparmor_calls = [
      c for c in mock_run.call_args_list
      if c[0][0][0] == "apparmor_parser"
    ]
    self.assertGreater(len(apparmor_calls), 0)


class TestDownloadVirtioIso(unittest.TestCase):
  """Tests for download_virtio_iso()."""

  @mock.patch.object(Path, "exists", return_value=True)
  def test_skips_when_exists(self, mock_exists):
    """Does not re-download existing ISO."""
    setup_win_vm.download_virtio_iso()

  @mock.patch("setup_win_vm.run")
  @mock.patch.object(Path, "exists", return_value=False)
  def test_downloads_when_missing(self, mock_exists, mock_run):
    """Downloads ISO via wget."""
    mock_run.return_value = mock.Mock(returncode=0)
    setup_win_vm.download_virtio_iso()
    args = mock_run.call_args[0][0]
    self.assertEqual(args[0], "wget")


class TestCreateVmDisk(unittest.TestCase):
  """Tests for create_vm_disk()."""

  @mock.patch.object(Path, "exists", return_value=True)
  def test_skips_when_exists(self, mock_exists):
    """Does not recreate existing disk."""
    setup_win_vm.create_vm_disk()

  @mock.patch("setup_win_vm.run")
  @mock.patch.object(Path, "exists", return_value=False)
  def test_creates_disk(self, mock_exists, mock_run):
    """Creates qcow2 disk with correct size."""
    mock_run.return_value = mock.Mock(returncode=0)
    setup_win_vm.create_vm_disk()
    args = mock_run.call_args[0][0]
    self.assertEqual(args[0], "qemu-img")
    self.assertIn("120G", args)


class TestGenerateAutounattendXml(unittest.TestCase):
  """Tests for _generate_autounattend_xml()."""

  @mock.patch.object(
    Path, "read_text",
    return_value="ssh-ed25519 AAAA fake",
  )
  def test_contains_expected_values(self, mock_read):
    """XML contains VM name, user, product key, and IP."""
    xml = setup_win_vm._generate_autounattend_xml()
    self.assertIn(setup_win_vm.VM_NAME, xml)
    self.assertIn(setup_win_vm.VM_USER, xml)
    self.assertIn(setup_win_vm.VM_IP, xml)
    self.assertIn("8WRPJ-JNGPC-68MHF-T87DR-JHV3B", xml)
    self.assertIn("ssh-ed25519 AAAA fake", xml)
    self.assertIn("Windows 11 Pro", xml)

  @mock.patch.object(
    Path, "read_text",
    return_value="ssh-ed25519 AAAA fake",
  )
  def test_contains_virtio_driver_paths(self, mock_read):
    """XML contains VirtIO driver search paths."""
    xml = setup_win_vm._generate_autounattend_xml()
    self.assertIn("D:\\", xml)
    self.assertIn("E:\\", xml)
    self.assertIn("F:\\", xml)

  @mock.patch.object(
    Path, "read_text",
    return_value="ssh-ed25519 AAAA fake",
  )
  def test_has_openssh_commands(self, mock_read):
    """XML contains OpenSSH setup commands."""
    xml = setup_win_vm._generate_autounattend_xml()
    self.assertIn("OpenSSH.Server", xml)
    self.assertIn("administrators_authorized_keys", xml)
    self.assertIn("DefaultShell", xml)


class TestGenerateAutounattendIso(unittest.TestCase):
  """Tests for generate_autounattend_iso()."""

  @mock.patch.object(Path, "exists", return_value=True)
  def test_skips_when_exists(self, mock_exists):
    """Does not regenerate existing ISO."""
    setup_win_vm.generate_autounattend_iso()

  @mock.patch("setup_win_vm.run")
  @mock.patch.object(
    Path, "read_text",
    return_value="ssh-ed25519 AAAA fake",
  )
  @mock.patch.object(Path, "exists", return_value=False)
  def test_creates_iso(self, mock_exists, mock_read, mock_run):
    """Creates ISO with OEMDRV volume label."""
    mock_run.return_value = mock.Mock(returncode=0)
    setup_win_vm.generate_autounattend_iso()
    args = mock_run.call_args[0][0]
    self.assertEqual(args[0], "genisoimage")
    self.assertIn("OEMDRV", args)


class TestCreateVm(unittest.TestCase):
  """Tests for create_vm()."""

  @mock.patch("setup_win_vm.vm_exists", return_value=True)
  def test_skips_when_exists(self, mock_exists):
    """Does not re-create existing VM."""
    setup_win_vm.create_vm()

  @mock.patch("setup_win_vm.time.sleep")
  @mock.patch("setup_win_vm.run")
  @mock.patch.object(Path, "exists", return_value=True)
  @mock.patch("setup_win_vm.vm_exists", return_value=False)
  def test_calls_virt_install(
    self, mock_vm_exists, mock_path_exists, mock_run,
    mock_sleep,
  ):
    """Calls virt-install with expected arguments."""
    mock_run.return_value = mock.Mock(returncode=0)
    setup_win_vm.create_vm()
    # First call is virt-install, second is virsh send-key.
    args = mock_run.call_args_list[0][0][0]
    self.assertEqual(args[0], "virt-install")
    self.assertIn("--name", args)
    self.assertIn("win-01", args)
    self.assertIn("--tpm", args)
    # Verify send-key was called for CD boot.
    key_args = mock_run.call_args_list[1][0][0]
    self.assertEqual(key_args[0], "virsh")
    self.assertIn("send-key", key_args)

  @mock.patch.object(Path, "exists", return_value=False)
  @mock.patch("setup_win_vm.vm_exists", return_value=False)
  def test_exits_when_iso_missing(
    self, mock_vm_exists, mock_path_exists,
  ):
    """Exits if Windows ISO not found."""
    with self.assertRaises(SystemExit):
      setup_win_vm.create_vm()


class TestWaitForSsh(unittest.TestCase):
  """Tests for wait_for_ssh()."""

  @mock.patch("setup_win_vm.subprocess.run")
  def test_returns_true_on_success(self, mock_run):
    """Returns True when SSH connects."""
    mock_run.return_value = mock.Mock(returncode=0)
    result = setup_win_vm.wait_for_ssh(timeout=5)
    self.assertTrue(result)

  @mock.patch("setup_win_vm.time.sleep")
  @mock.patch("setup_win_vm.subprocess.run")
  def test_returns_false_on_timeout(self, mock_run, mock_sleep):
    """Returns False when SSH never connects."""
    mock_run.return_value = mock.Mock(returncode=1)
    result = setup_win_vm.wait_for_ssh(timeout=1)
    self.assertFalse(result)


class TestConfigureSshConfig(unittest.TestCase):
  """Tests for configure_ssh_config()."""

  @mock.patch.dict(os.environ, {"SUDO_USER": ""}, clear=False)
  @mock.patch("setup_win_vm.run")
  def test_adds_entry(self, mock_run):
    """Adds SSH config entry when not present."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
      ssh_dir = Path(tmpdir) / ".ssh"
      ssh_dir.mkdir()
      with mock.patch("setup_win_vm._REAL_HOME", Path(tmpdir)):
        setup_win_vm.configure_ssh_config()
        content = (ssh_dir / "config").read_text()
    self.assertIn("win-01", content)
    self.assertIn("10.101.0.21", content)

  @mock.patch.dict(os.environ, {"SUDO_USER": ""}, clear=False)
  @mock.patch("setup_win_vm.run")
  def test_skips_when_present(self, mock_run):
    """Does not duplicate entry."""
    import tempfile
    marker = "# takt: win-01"
    with tempfile.TemporaryDirectory() as tmpdir:
      ssh_dir = Path(tmpdir) / ".ssh"
      ssh_dir.mkdir()
      config_path = ssh_dir / "config"
      config_path.write_text(marker + "\nHost win-01\n")
      with mock.patch("setup_win_vm._REAL_HOME", Path(tmpdir)):
        setup_win_vm.configure_ssh_config()
        content = config_path.read_text()
    self.assertEqual(content.count(marker), 1)


class TestUpdateTargetsConfig(unittest.TestCase):
  """Tests for update_targets_config()."""

  @mock.patch("lib.config.load_targets_config")
  def test_skips_when_present(self, mock_config):
    """No-op when win-01 already in config."""
    mock_config.return_value = {
      "targets": {"win-01": {"host": "10.101.0.21"}},
    }
    setup_win_vm.update_targets_config()

  @mock.patch.dict(os.environ, {"SUDO_USER": ""}, clear=False)
  @mock.patch("setup_win_vm.run")
  @mock.patch("lib.config.load_targets_config")
  def test_adds_entry(self, mock_config, mock_run):
    """Adds win-01 to targets.yaml."""
    mock_config.return_value = {"targets": {}}
    import tempfile
    import yaml
    with tempfile.TemporaryDirectory() as tmpdir:
      config_dir = Path(tmpdir) / "config"
      config_dir.mkdir()
      target_file = config_dir / "targets.yaml"
      target_file.write_text(yaml.dump({"targets": {}}))
      with mock.patch(
        "setup_win_vm.PROJECT_DIR", Path(tmpdir),
      ):
        setup_win_vm.update_targets_config()
        content = target_file.read_text()
      self.assertIn("win-01", content)


if __name__ == "__main__":
  unittest.main()
