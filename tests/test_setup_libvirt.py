"""Tests for bin/setup_libvirt.py."""

import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from unittest import mock

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "bin"))

import setup_libvirt


class TestCheckKvm(unittest.TestCase):
  """Tests for check_kvm()."""

  @mock.patch.object(Path, "exists", return_value=True)
  def test_kvm_present(self, mock_exists):
    """No error when /dev/kvm exists."""
    setup_libvirt.check_kvm()

  @mock.patch.object(Path, "exists", return_value=False)
  def test_kvm_missing(self, mock_exists):
    """Exits with error when /dev/kvm is missing."""
    with self.assertRaises(SystemExit):
      setup_libvirt.check_kvm()


class TestInstallPackages(unittest.TestCase):
  """Tests for install_packages()."""

  @mock.patch("setup_libvirt.run")
  def test_skips_when_installed(self, mock_run):
    """Skips install when all packages present."""
    mock_run.return_value = mock.Mock(returncode=0)
    setup_libvirt.install_packages()
    # Only dpkg check, no apt-get calls.
    self.assertEqual(mock_run.call_count, 1)
    args = mock_run.call_args[0][0]
    self.assertEqual(args[0], "dpkg")

  @mock.patch("setup_libvirt.run")
  def test_installs_when_missing(self, mock_run):
    """Installs packages when dpkg check fails."""
    mock_run.side_effect = [
      mock.Mock(returncode=1),  # dpkg -s fails
      mock.Mock(returncode=0),  # apt-get update
      mock.Mock(returncode=0),  # apt-get install
    ]
    setup_libvirt.install_packages()
    self.assertEqual(mock_run.call_count, 3)
    # Second call is apt-get update.
    args = mock_run.call_args_list[1][0][0]
    self.assertEqual(args[0], "apt-get")
    self.assertIn("update", args)


class TestAddUserToGroups(unittest.TestCase):
  """Tests for add_user_to_groups()."""

  @mock.patch.dict(os.environ, {"SUDO_USER": "testuser"})
  @mock.patch("setup_libvirt.run")
  def test_skips_when_already_member(self, mock_run):
    """Skips usermod when user already in groups."""
    mock_run.return_value = mock.Mock(
      returncode=0, stdout="testuser libvirt kvm",
    )
    setup_libvirt.add_user_to_groups()
    # Two id calls, no usermod.
    for call in mock_run.call_args_list:
      args = call[0][0]
      self.assertEqual(args[0], "id")

  @mock.patch.dict(os.environ, {"SUDO_USER": "testuser"})
  @mock.patch("setup_libvirt.run")
  def test_adds_missing_groups(self, mock_run):
    """Calls usermod for missing groups."""
    mock_run.return_value = mock.Mock(
      returncode=0, stdout="testuser",
    )
    setup_libvirt.add_user_to_groups()
    usermod_calls = [
      c for c in mock_run.call_args_list
      if c[0][0][0] == "usermod"
    ]
    self.assertEqual(len(usermod_calls), 2)


class TestStartLibvirtd(unittest.TestCase):
  """Tests for start_libvirtd()."""

  @mock.patch("setup_libvirt.run")
  def test_skips_when_active(self, mock_run):
    """Skips enable when libvirtd already active."""
    mock_run.return_value = mock.Mock(
      returncode=0, stdout="active",
    )
    setup_libvirt.start_libvirtd()
    self.assertEqual(mock_run.call_count, 1)

  @mock.patch("setup_libvirt.run")
  def test_starts_when_inactive(self, mock_run):
    """Starts and enables when inactive."""
    mock_run.side_effect = [
      mock.Mock(returncode=0, stdout="inactive"),
      mock.Mock(returncode=0),
    ]
    setup_libvirt.start_libvirtd()
    self.assertEqual(mock_run.call_count, 2)
    args = mock_run.call_args_list[1][0][0]
    self.assertIn("enable", args)


class TestSetupNetwork(unittest.TestCase):
  """Tests for setup_network()."""

  @mock.patch("setup_libvirt.run")
  def test_skips_when_active(self, mock_run):
    """No-op when network already active with autostart."""
    mock_run.return_value = mock.Mock(
      returncode=0,
      stdout="Active:         yes\nAutostart:      yes",
    )
    setup_libvirt.setup_network()
    self.assertEqual(mock_run.call_count, 1)

  @mock.patch("setup_libvirt.run")
  def test_starts_when_inactive(self, mock_run):
    """Starts network when present but inactive."""
    mock_run.side_effect = [
      mock.Mock(
        returncode=0,
        stdout="Active:         no\nAutostart:      no",
      ),
      mock.Mock(returncode=0),  # net-start
      mock.Mock(returncode=0),  # net-autostart
    ]
    setup_libvirt.setup_network()
    self.assertEqual(mock_run.call_count, 3)

  @mock.patch("os.unlink")
  @mock.patch("setup_libvirt.run")
  def test_creates_when_missing(self, mock_run, mock_unlink):
    """Defines, starts, and autostarts new network."""
    mock_run.side_effect = [
      mock.Mock(returncode=1),  # net-info fails
      mock.Mock(returncode=0),  # net-define
      mock.Mock(returncode=0),  # net-start
      mock.Mock(returncode=0),  # net-autostart
    ]
    setup_libvirt.setup_network()
    self.assertEqual(mock_run.call_count, 4)
    define_args = mock_run.call_args_list[1][0][0]
    self.assertEqual(define_args[0], "virsh")
    self.assertEqual(define_args[1], "net-define")


class TestGenerateSshKey(unittest.TestCase):
  """Tests for generate_ssh_key()."""

  @mock.patch.object(Path, "exists", return_value=True)
  def test_skips_when_exists(self, mock_exists):
    """Does not regenerate existing key."""
    setup_libvirt.generate_ssh_key()
    # No subprocess calls.

  @mock.patch.dict(os.environ, {"SUDO_USER": "testuser"})
  @mock.patch("setup_libvirt.run")
  @mock.patch.object(Path, "mkdir")
  @mock.patch.object(Path, "exists", return_value=False)
  def test_generates_key(self, mock_exists, mock_mkdir, mock_run):
    """Generates key and fixes ownership."""
    mock_run.return_value = mock.Mock(returncode=0)
    setup_libvirt.generate_ssh_key()
    keygen_calls = [
      c for c in mock_run.call_args_list
      if c[0][0][0] == "ssh-keygen"
    ]
    self.assertEqual(len(keygen_calls), 1)
    chown_calls = [
      c for c in mock_run.call_args_list
      if c[0][0][0] == "chown"
    ]
    self.assertEqual(len(chown_calls), 2)


class TestVmExists(unittest.TestCase):
  """Tests for vm_exists()."""

  @mock.patch("setup_libvirt.run")
  def test_returns_true_when_exists(self, mock_run):
    """Returns True when virsh dominfo succeeds."""
    mock_run.return_value = mock.Mock(returncode=0)
    self.assertTrue(setup_libvirt.vm_exists("deb-01"))

  @mock.patch("setup_libvirt.run")
  def test_returns_false_when_missing(self, mock_run):
    """Returns False when virsh dominfo fails."""
    mock_run.return_value = mock.Mock(returncode=1)
    self.assertFalse(setup_libvirt.vm_exists("deb-01"))


class TestWriteCloudInitConfigs(unittest.TestCase):
  """Tests for write_cloud_init_configs()."""

  def test_generates_valid_configs(self):
    """Cloud-init configs contain expected values."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
      # Create a fake public key.
      pub_key_path = setup_libvirt.SSH_KEY_PATH.with_suffix(
        ".pub",
      )
      with mock.patch.object(
        Path, "read_text", return_value="ssh-ed25519 AAAA fake",
      ):
        paths = setup_libvirt.write_cloud_init_configs(tmpdir)

      user_data = Path(paths[0]).read_text()
      network_config = Path(paths[1]).read_text()
      meta_data = Path(paths[2]).read_text()

    self.assertIn(f"hostname: {setup_libvirt.VM_NAME}", user_data)
    self.assertIn(f"name: {setup_libvirt.VM_USER}", user_data)
    self.assertIn("build-essential", user_data)
    self.assertIn(setup_libvirt.VM_IP, network_config)
    self.assertIn(setup_libvirt.VM_NAME, meta_data)


class TestConfigureSshConfig(unittest.TestCase):
  """Tests for configure_ssh_config()."""

  @mock.patch.dict(os.environ, {"SUDO_USER": ""}, clear=False)
  @mock.patch("setup_libvirt.run")
  def test_adds_entry(self, mock_run):
    """Adds SSH config entry when not present."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
      ssh_dir = Path(tmpdir) / ".ssh"
      ssh_dir.mkdir()
      with mock.patch(
        "setup_libvirt._REAL_HOME", Path(tmpdir),
      ):
        setup_libvirt.configure_ssh_config()
        content = (ssh_dir / "config").read_text()
    self.assertIn(setup_libvirt.VM_NAME, content)
    self.assertIn(setup_libvirt.VM_IP, content)

  @mock.patch.dict(os.environ, {"SUDO_USER": ""}, clear=False)
  @mock.patch("setup_libvirt.run")
  def test_skips_when_present(self, mock_run):
    """Does not duplicate entry."""
    import tempfile
    marker = f"# takt: {setup_libvirt.VM_NAME}"
    with tempfile.TemporaryDirectory() as tmpdir:
      ssh_dir = Path(tmpdir) / ".ssh"
      ssh_dir.mkdir()
      config_path = ssh_dir / "config"
      config_path.write_text(marker + "\nHost deb-01\n")
      with mock.patch(
        "setup_libvirt._REAL_HOME", Path(tmpdir),
      ):
        setup_libvirt.configure_ssh_config()
        content = config_path.read_text()
    # Marker appears only once.
    self.assertEqual(content.count(marker), 1)


class TestCreateVm(unittest.TestCase):
  """Tests for create_vm()."""

  @mock.patch("setup_libvirt.vm_exists", return_value=True)
  def test_skips_when_exists(self, mock_exists):
    """Does not re-create existing VM."""
    setup_libvirt.create_vm()
    # No download or virt-install calls.


class TestIsRoot(unittest.TestCase):
  """Tests for is_root()."""

  @mock.patch("os.geteuid", return_value=0)
  def test_root(self, mock_euid):
    """Returns True when root."""
    self.assertTrue(setup_libvirt.is_root())

  @mock.patch("os.geteuid", return_value=1000)
  def test_not_root(self, mock_euid):
    """Returns False when not root."""
    self.assertFalse(setup_libvirt.is_root())


if __name__ == "__main__":
  unittest.main()
