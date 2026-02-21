"""Tests for bin/provision_vm.py."""

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "bin"))

import provision_vm


class TestSshCmd(unittest.TestCase):
  """Tests for _ssh_cmd()."""

  def test_builds_correct_command(self):
    """Returns expected SSH command list."""
    result = provision_vm._ssh_cmd("worker", "10.0.0.1", "/k")
    self.assertEqual(result[0], "ssh")
    self.assertIn("-i", result)
    self.assertIn("/k", result)
    self.assertIn("worker@10.0.0.1", result)


class TestRunRemote(unittest.TestCase):
  """Tests for _run_remote()."""

  @mock.patch("provision_vm.subprocess.run")
  def test_success(self, mock_run):
    """Returns result on success."""
    mock_run.return_value = mock.Mock(
      returncode=0, stdout="ok\n", stderr="",
    )
    result = provision_vm._run_remote(
      "worker", "10.0.0.1", "/k", "echo ok",
    )
    self.assertEqual(result.stdout, "ok\n")

  @mock.patch("provision_vm.subprocess.run")
  def test_failure_exits(self, mock_run):
    """Exits on failure."""
    mock_run.return_value = mock.Mock(
      returncode=1, stdout="", stderr="error",
    )
    with self.assertRaises(SystemExit):
      provision_vm._run_remote(
        "worker", "10.0.0.1", "/k", "false",
      )


class TestRsync(unittest.TestCase):
  """Tests for _rsync()."""

  @mock.patch("provision_vm.subprocess.run")
  def test_basic_rsync(self, mock_run):
    """Calls rsync with correct args."""
    mock_run.return_value = mock.Mock(
      returncode=0, stderr="",
    )
    provision_vm._rsync(
      "/src/", "~/dst/", "worker", "10.0.0.1", "/k",
    )
    args = mock_run.call_args[0][0]
    self.assertEqual(args[0], "rsync")
    self.assertIn("-a", args)
    self.assertIn("worker@10.0.0.1:~/dst/", args)

  @mock.patch("provision_vm.subprocess.run")
  def test_rsync_with_exclude(self, mock_run):
    """Passes --exclude flags."""
    mock_run.return_value = mock.Mock(
      returncode=0, stderr="",
    )
    provision_vm._rsync(
      "/src/", "~/dst/", "worker", "10.0.0.1", "/k",
      exclude=["pac/"],
    )
    args = mock_run.call_args[0][0]
    idx = args.index("--exclude")
    self.assertEqual(args[idx + 1], "pac/")

  @mock.patch("provision_vm.subprocess.run")
  def test_rsync_failure_exits(self, mock_run):
    """Exits on rsync failure."""
    mock_run.return_value = mock.Mock(
      returncode=1, stderr="rsync error",
    )
    with self.assertRaises(SystemExit):
      provision_vm._rsync(
        "/src/", "~/dst/", "worker", "10.0.0.1", "/k",
      )


class TestInstallPackages(unittest.TestCase):
  """Tests for install_packages()."""

  @mock.patch("provision_vm._run_remote")
  @mock.patch("provision_vm.subprocess.run")
  def test_skips_when_installed(self, mock_run, mock_remote):
    """Skips install when all packages present."""
    # Both apt and pip checks succeed.
    mock_run.return_value = mock.Mock(
      returncode=0, stderr="",
    )
    provision_vm.install_packages(
      "worker", "10.0.0.1", "/k",
    )
    mock_remote.assert_not_called()

  @mock.patch("provision_vm._run_remote")
  @mock.patch("provision_vm.subprocess.run")
  def test_installs_when_missing(self, mock_run, mock_remote):
    """Runs apt and pip install when checks fail."""
    mock_run.return_value = mock.Mock(
      returncode=1, stderr="",
    )
    mock_remote.return_value = mock.Mock(
      returncode=0, stdout="", stderr="",
    )
    provision_vm.install_packages(
      "worker", "10.0.0.1", "/k",
    )
    # Three _run_remote calls: apt, locale-gen, pip.
    self.assertEqual(mock_remote.call_count, 3)


class TestCopyZshConfig(unittest.TestCase):
  """Tests for copy_zsh_config()."""

  @mock.patch("provision_vm._rsync")
  @mock.patch.object(Path, "is_dir", return_value=True)
  @mock.patch.object(Path, "exists", return_value=True)
  def test_syncs_files(self, mock_exists, mock_isdir, mock_rsync):
    """Rsyncs .zshrc and .oh-my-zsh."""
    provision_vm.copy_zsh_config(
      "worker", "10.0.0.1", "/k",
    )
    self.assertEqual(mock_rsync.call_count, 2)

  @mock.patch("provision_vm._rsync")
  @mock.patch.object(Path, "exists", return_value=False)
  def test_skips_when_no_zshrc(self, mock_exists, mock_rsync):
    """Skips when no local .zshrc."""
    provision_vm.copy_zsh_config(
      "worker", "10.0.0.1", "/k",
    )
    mock_rsync.assert_not_called()


class TestCopyNvimConfig(unittest.TestCase):
  """Tests for copy_nvim_config()."""

  @mock.patch("provision_vm.subprocess.run")
  @mock.patch("provision_vm._rsync")
  @mock.patch("provision_vm._run_remote")
  @mock.patch.object(Path, "is_dir", return_value=True)
  def test_syncs_nvim_and_packer(
    self, mock_isdir, mock_remote, mock_rsync, mock_run,
  ):
    """Rsyncs nvim config and packer plugins."""
    mock_remote.return_value = mock.Mock(
      returncode=0, stdout="", stderr="",
    )
    mock_run.return_value = mock.Mock(
      returncode=0, stderr="",
    )
    provision_vm.copy_nvim_config(
      "worker", "10.0.0.1", "/k",
    )
    # Two rsyncs: nvim config, packer dir.
    self.assertEqual(mock_rsync.call_count, 2)

  @mock.patch("provision_vm._rsync")
  @mock.patch.object(Path, "is_dir", return_value=False)
  def test_skips_when_no_nvim(self, mock_isdir, mock_rsync):
    """Skips when no local nvim config."""
    provision_vm.copy_nvim_config(
      "worker", "10.0.0.1", "/k",
    )
    mock_rsync.assert_not_called()


class TestConfigureZshenv(unittest.TestCase):
  """Tests for configure_zshenv()."""

  @mock.patch("provision_vm.subprocess.run")
  def test_skips_when_present(self, mock_run):
    """No-op when marker already in .zshenv."""
    mock_run.return_value = mock.Mock(
      returncode=0, stderr="",
    )
    provision_vm.configure_zshenv(
      "worker", "10.0.0.1", "/k",
    )
    # Only the grep check, no _run_remote.
    self.assertEqual(mock_run.call_count, 1)

  @mock.patch("provision_vm._run_remote")
  @mock.patch("provision_vm.subprocess.run")
  def test_writes_when_missing(self, mock_run, mock_remote):
    """Writes .zshenv when marker not found."""
    mock_run.return_value = mock.Mock(
      returncode=1, stderr="",
    )
    mock_remote.return_value = mock.Mock(
      returncode=0, stdout="", stderr="",
    )
    provision_vm.configure_zshenv(
      "worker", "10.0.0.1", "/k",
    )
    mock_remote.assert_called_once()
    cmd = mock_remote.call_args[0][3]
    self.assertIn(".zshenv", cmd)


class TestSetDefaultShell(unittest.TestCase):
  """Tests for set_default_shell()."""

  @mock.patch("provision_vm._run_remote")
  def test_skips_when_already_zsh(self, mock_remote):
    """No chsh when already using zsh."""
    mock_remote.return_value = mock.Mock(
      returncode=0, stdout="/usr/bin/zsh",
    )
    provision_vm.set_default_shell(
      "worker", "10.0.0.1", "/k",
    )
    # Only the getent call, no chsh.
    self.assertEqual(mock_remote.call_count, 1)

  @mock.patch("provision_vm._run_remote")
  def test_sets_zsh(self, mock_remote):
    """Runs chsh when not using zsh."""
    mock_remote.side_effect = [
      mock.Mock(returncode=0, stdout="/bin/bash"),
      mock.Mock(returncode=0, stdout=""),
    ]
    provision_vm.set_default_shell(
      "worker", "10.0.0.1", "/k",
    )
    self.assertEqual(mock_remote.call_count, 2)
    chsh_call = mock_remote.call_args_list[1]
    self.assertIn("chsh", chsh_call[0][3])


class TestResolveTarget(unittest.TestCase):
  """Tests for resolve_target()."""

  @mock.patch("provision_vm.load_targets_config")
  def test_returns_config(self, mock_config):
    """Returns user, host, key from config."""
    mock_config.return_value = {
      "targets": {
        "deb-01": {
          "user": "worker",
          "host": "10.101.0.20",
          "ssh_key": "~/.ssh/id_ed25519_targets",
        },
      },
    }
    user, host, key = provision_vm.resolve_target("deb-01")
    self.assertEqual(user, "worker")
    self.assertEqual(host, "10.101.0.20")
    self.assertTrue(str(key).endswith("id_ed25519_targets"))

  @mock.patch("provision_vm.load_targets_config")
  def test_exits_on_missing_target(self, mock_config):
    """Exits when target not found."""
    mock_config.return_value = {"targets": {}}
    with self.assertRaises(SystemExit):
      provision_vm.resolve_target("nonexistent")


if __name__ == "__main__":
  unittest.main()
