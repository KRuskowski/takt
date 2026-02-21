"""Tests for bin/provision_win_vm.py."""

import sys
import unittest
from pathlib import Path
from unittest import mock

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "bin"))

import provision_win_vm


class TestSshCmd(unittest.TestCase):
  """Tests for _ssh_cmd()."""

  def test_builds_correct_command(self):
    """Returns expected SSH command list."""
    result = provision_win_vm._ssh_cmd(
      "worker", "10.0.0.1", "/k",
    )
    self.assertEqual(result[0], "ssh")
    self.assertIn("-i", result)
    self.assertIn("/k", result)
    self.assertIn("worker@10.0.0.1", result)


class TestRunRemote(unittest.TestCase):
  """Tests for _run_remote()."""

  @mock.patch("provision_win_vm.subprocess.run")
  def test_success(self, mock_run):
    """Returns result on success."""
    mock_run.return_value = mock.Mock(
      returncode=0, stdout="ok\n", stderr="",
    )
    result = provision_win_vm._run_remote(
      "worker", "10.0.0.1", "/k", "echo ok",
    )
    self.assertEqual(result.stdout, "ok\n")

  @mock.patch("provision_win_vm.subprocess.run")
  def test_failure_exits(self, mock_run):
    """Exits on failure."""
    mock_run.return_value = mock.Mock(
      returncode=1, stdout="", stderr="error",
    )
    with self.assertRaises(SystemExit):
      provision_win_vm._run_remote(
        "worker", "10.0.0.1", "/k", "false",
      )


class TestCheckRemote(unittest.TestCase):
  """Tests for _check_remote()."""

  @mock.patch("provision_win_vm.subprocess.run")
  def test_returns_true_on_success(self, mock_run):
    """Returns True when command succeeds."""
    mock_run.return_value = mock.Mock(returncode=0)
    result = provision_win_vm._check_remote(
      "worker", "10.0.0.1", "/k", "Test-Path C:\\foo",
    )
    self.assertTrue(result)

  @mock.patch("provision_win_vm.subprocess.run")
  def test_returns_false_on_failure(self, mock_run):
    """Returns False when command fails."""
    mock_run.return_value = mock.Mock(returncode=1)
    result = provision_win_vm._check_remote(
      "worker", "10.0.0.1", "/k", "Test-Path C:\\foo",
    )
    self.assertFalse(result)


class TestResolveTarget(unittest.TestCase):
  """Tests for resolve_target()."""

  @mock.patch("provision_win_vm.load_targets_config")
  def test_returns_config(self, mock_config):
    """Returns user, host, key from config."""
    mock_config.return_value = {
      "targets": {
        "win-01": {
          "user": "worker",
          "host": "10.101.0.21",
          "ssh_key": "~/.ssh/id_ed25519_targets",
        },
      },
    }
    user, host, key = provision_win_vm.resolve_target("win-01")
    self.assertEqual(user, "worker")
    self.assertEqual(host, "10.101.0.21")
    self.assertTrue(str(key).endswith("id_ed25519_targets"))

  @mock.patch("provision_win_vm.load_targets_config")
  def test_exits_on_missing_target(self, mock_config):
    """Exits when target not found."""
    mock_config.return_value = {"targets": {}}
    with self.assertRaises(SystemExit):
      provision_win_vm.resolve_target("nonexistent")


class TestWaitForReady(unittest.TestCase):
  """Tests for wait_for_ready()."""

  @mock.patch("provision_win_vm._check_remote", return_value=True)
  def test_returns_true_when_marker_found(self, mock_check):
    """Returns True when completion marker exists."""
    result = provision_win_vm.wait_for_ready(
      "worker", "10.0.0.1", "/k", timeout=5,
    )
    self.assertTrue(result)

  @mock.patch("provision_win_vm.time.sleep")
  @mock.patch(
    "provision_win_vm._check_remote", return_value=False,
  )
  def test_returns_false_on_timeout(
    self, mock_check, mock_sleep,
  ):
    """Returns False when marker never appears."""
    result = provision_win_vm.wait_for_ready(
      "worker", "10.0.0.1", "/k", timeout=1,
    )
    self.assertFalse(result)


class TestInstallVsBuildtools(unittest.TestCase):
  """Tests for install_vs_buildtools()."""

  @mock.patch(
    "provision_win_vm._check_remote", return_value=True,
  )
  def test_skips_when_installed(self, mock_check):
    """No-op when VS Build Tools already installed."""
    provision_win_vm.install_vs_buildtools(
      "worker", "10.0.0.1", "/k",
    )
    mock_check.assert_called_once()

  @mock.patch("provision_win_vm._run_remote")
  @mock.patch(
    "provision_win_vm._check_remote", return_value=False,
  )
  def test_installs_when_missing(self, mock_check, mock_remote):
    """Downloads and installs when not present."""
    mock_remote.return_value = mock.Mock(
      returncode=0, stdout="", stderr="",
    )
    provision_win_vm.install_vs_buildtools(
      "worker", "10.0.0.1", "/k",
    )
    # Download, install, cleanup = 3 calls.
    self.assertEqual(mock_remote.call_count, 3)


class TestInstallGit(unittest.TestCase):
  """Tests for install_git()."""

  @mock.patch(
    "provision_win_vm._check_remote", return_value=True,
  )
  def test_skips_when_installed(self, mock_check):
    """No-op when Git already installed."""
    provision_win_vm.install_git(
      "worker", "10.0.0.1", "/k",
    )
    mock_check.assert_called_once()

  @mock.patch("provision_win_vm._run_remote")
  @mock.patch(
    "provision_win_vm._check_remote", return_value=False,
  )
  def test_installs_when_missing(self, mock_check, mock_remote):
    """Downloads and installs when not present."""
    mock_remote.return_value = mock.Mock(
      returncode=0, stdout="", stderr="",
    )
    provision_win_vm.install_git(
      "worker", "10.0.0.1", "/k",
    )
    # Download, install, cleanup = 3 calls.
    self.assertEqual(mock_remote.call_count, 3)


class TestConfigureSambaShare(unittest.TestCase):
  """Tests for configure_samba_share()."""

  @mock.patch("provision_win_vm._run_remote")
  @mock.patch(
    "provision_win_vm._check_remote", return_value=True,
  )
  @mock.patch("provision_win_vm._run_local")
  @mock.patch.object(Path, "read_text")
  @mock.patch.object(Path, "exists", return_value=True)
  def test_skips_when_fully_configured(
    self, mock_exists, mock_read, mock_local,
    mock_check, mock_remote,
  ):
    """No-op when Samba and drive mapping both present."""
    mock_read.return_value = provision_win_vm.SAMBA_MARKER
    mock_local.return_value = mock.Mock(returncode=0)
    provision_win_vm.configure_samba_share(
      "worker", "10.0.0.1", "/k",
    )
    # No _run_remote calls for drive mapping.
    mock_remote.assert_not_called()


class TestConfigureVsPath(unittest.TestCase):
  """Tests for configure_vs_path()."""

  @mock.patch(
    "provision_win_vm._check_remote", return_value=True,
  )
  def test_skips_when_present(self, mock_check):
    """No-op when profile already configured."""
    provision_win_vm.configure_vs_path(
      "worker", "10.0.0.1", "/k",
    )
    mock_check.assert_called_once()

  @mock.patch("provision_win_vm._run_remote")
  @mock.patch(
    "provision_win_vm._check_remote", return_value=False,
  )
  def test_writes_profile(self, mock_check, mock_remote):
    """Writes PowerShell profile script."""
    mock_remote.return_value = mock.Mock(
      returncode=0, stdout="", stderr="",
    )
    provision_win_vm.configure_vs_path(
      "worker", "10.0.0.1", "/k",
    )
    mock_remote.assert_called_once()
    cmd = mock_remote.call_args[0][3]
    self.assertIn("vcvars64.bat", cmd)


class TestDisableAutologon(unittest.TestCase):
  """Tests for disable_autologon()."""

  @mock.patch(
    "provision_win_vm._check_remote", return_value=False,
  )
  def test_skips_when_already_disabled(self, mock_check):
    """No-op when AutoLogon already off."""
    provision_win_vm.disable_autologon(
      "worker", "10.0.0.1", "/k",
    )
    mock_check.assert_called_once()

  @mock.patch("provision_win_vm._run_remote")
  @mock.patch(
    "provision_win_vm._check_remote", return_value=True,
  )
  def test_disables_autologon(self, mock_check, mock_remote):
    """Removes AutoLogon registry keys."""
    mock_remote.return_value = mock.Mock(
      returncode=0, stdout="", stderr="",
    )
    provision_win_vm.disable_autologon(
      "worker", "10.0.0.1", "/k",
    )
    mock_remote.assert_called_once()
    cmd = mock_remote.call_args[0][3]
    self.assertIn("AutoAdminLogon", cmd)


class TestEjectCdroms(unittest.TestCase):
  """Tests for eject_cdroms()."""

  @mock.patch("provision_win_vm._run_local")
  def test_ejects_sata_devices(self, mock_local):
    """Ejects CD-ROM devices from domblklist output."""
    mock_local.side_effect = [
      # domblklist output
      mock.Mock(
        returncode=0,
        stdout=(
          " Target   Source\n"
          "----------------------------\n"
          " vda      /path/to/disk.qcow2\n"
          " sda      /path/to/win.iso\n"
          " sdb      /path/to/autounattend.iso\n"
          " sdc      /path/to/virtio.iso\n"
        ),
      ),
      # Three change-media --eject calls
      mock.Mock(returncode=0),
      mock.Mock(returncode=0),
      mock.Mock(returncode=0),
    ]
    provision_win_vm.eject_cdroms("win-01")
    # 1 domblklist + 3 eject calls.
    self.assertEqual(mock_local.call_count, 4)

  @mock.patch("provision_win_vm._run_local")
  def test_handles_no_cdroms(self, mock_local):
    """Handles case with no CD-ROMs gracefully."""
    mock_local.return_value = mock.Mock(
      returncode=0,
      stdout=(
        " Target   Source\n"
        "----------------------------\n"
        " vda      /path/to/disk.qcow2\n"
      ),
    )
    provision_win_vm.eject_cdroms("win-01")
    self.assertEqual(mock_local.call_count, 1)

  @mock.patch("provision_win_vm._run_local")
  def test_handles_domblklist_failure(self, mock_local):
    """Skips when domblklist fails."""
    mock_local.return_value = mock.Mock(returncode=1)
    provision_win_vm.eject_cdroms("win-01")


if __name__ == "__main__":
  unittest.main()
