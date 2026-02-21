#!/usr/bin/env python3
"""Provision a VM target as a build environment.

SSHes into a target VM and installs build tooling, copies the
operator's zsh/nvim configs, and sets the default shell to zsh.
Idempotent — each step checks whether it's already done.

Usage:
  python3 bin/provision_vm.py [target_name]

Default target: deb-01
"""

import argparse
import subprocess
import sys
from pathlib import Path

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib.config import load_targets_config

HOME = Path.home()

APT_PACKAGES = [
  "zsh",
  "neovim",
  "rsync",
  "curl",
  "wget",
  "build-essential",
  "cmake",
  "clang",
  "clang-format",
  "clang-tidy",
  "python3",
  "python3-pip",
  "python3-venv",
  "locales",
]

PIP_PACKAGES = [
  "pytest",
  "cpplint",
  "flake8",
]


def _ssh_cmd(user, host, key):
  """Build the base SSH command list.

  Args:
    user: Remote username.
    host: Remote hostname or IP.
    key: Path to SSH private key.

  Returns:
    List of SSH command arguments.
  """
  return [
    "ssh",
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "BatchMode=yes",
    "-i", str(key),
    f"{user}@{host}",
  ]


def _run_remote(user, host, key, command):
  """Run a command on the remote host via SSH.

  Args:
    user: Remote username.
    host: Remote hostname or IP.
    key: Path to SSH private key.
    command: Shell command string to execute.

  Returns:
    CompletedProcess instance.

  Raises:
    SystemExit: If the command fails.
  """
  cmd = _ssh_cmd(user, host, key) + [command]
  result = subprocess.run(cmd, capture_output=True, text=True)
  if result.returncode != 0:
    print(f"  Error: {result.stderr.strip()}")
    sys.exit(1)
  return result


def _rsync(src, dst_path, user, host, key, exclude=None):
  """Rsync a local path to the remote host.

  Args:
    src: Local source path (str or Path).
    dst_path: Remote destination path.
    user: Remote username.
    host: Remote hostname or IP.
    key: Path to SSH private key.
    exclude: Optional list of rsync --exclude patterns.
  """
  ssh_cmd = (
    f"ssh -o ConnectTimeout=10"
    f" -o StrictHostKeyChecking=accept-new"
    f" -o BatchMode=yes"
    f" -i {key}"
  )
  cmd = [
    "rsync", "-a", "--delete",
    "-e", ssh_cmd,
  ]
  if exclude:
    for pat in exclude:
      cmd += ["--exclude", pat]
  cmd += [str(src), f"{user}@{host}:{dst_path}"]
  result = subprocess.run(cmd, capture_output=True, text=True)
  if result.returncode != 0:
    print(f"  Rsync error: {result.stderr.strip()}")
    sys.exit(1)


def install_packages(user, host, key):
  """Install apt and pip packages on the remote host.

  Args:
    user: Remote username.
    host: Remote hostname or IP.
    key: Path to SSH private key.
  """
  # Check if all apt packages are installed.
  check = " && ".join(
    f"dpkg -s {pkg} >/dev/null 2>&1" for pkg in APT_PACKAGES
  )
  cmd = _ssh_cmd(user, host, key) + [check]
  result = subprocess.run(cmd, capture_output=True, text=True)

  if result.returncode == 0:
    print("[ok] apt packages already installed")
  else:
    print("[..] Installing apt packages...")
    pkg_list = " ".join(APT_PACKAGES)
    _run_remote(
      user, host, key,
      f"sudo DEBIAN_FRONTEND=noninteractive"
      f" apt-get update -qq"
      f" && sudo DEBIAN_FRONTEND=noninteractive"
      f" apt-get install -y -qq {pkg_list}",
    )
    # Generate en_US.UTF-8 locale.
    _run_remote(
      user, host, key,
      "sudo sed -i 's/# en_US.UTF-8/en_US.UTF-8/'"
      " /etc/locale.gen && sudo locale-gen",
    )
    print("[ok] apt packages installed")

  # Check if all pip packages are installed.
  check = " && ".join(
    f"python3 -m pip show {pkg} >/dev/null 2>&1"
    for pkg in PIP_PACKAGES
  )
  cmd = _ssh_cmd(user, host, key) + [check]
  result = subprocess.run(cmd, capture_output=True, text=True)

  if result.returncode == 0:
    print("[ok] pip packages already installed")
  else:
    print("[..] Installing pip packages...")
    pip_list = " ".join(PIP_PACKAGES)
    _run_remote(
      user, host, key,
      f"pip install --break-system-packages {pip_list}",
    )
    print("[ok] pip packages installed")


def copy_zsh_config(user, host, key):
  """Copy zsh config and oh-my-zsh to the remote host.

  Args:
    user: Remote username.
    host: Remote hostname or IP.
    key: Path to SSH private key.
  """
  zshrc = HOME / ".zshrc"
  omz_dir = HOME / ".oh-my-zsh"

  if not zshrc.exists():
    print("[skip] No ~/.zshrc found locally")
    return
  if not omz_dir.is_dir():
    print("[skip] No ~/.oh-my-zsh found locally")
    return

  print("[..] Syncing zsh config...")
  _rsync(str(zshrc), "~/.zshrc", user, host, key)
  # Trailing slash on source = sync contents into dest.
  _rsync(str(omz_dir) + "/", "~/.oh-my-zsh/", user, host, key)
  print("[ok] zsh config synced")


def copy_nvim_config(user, host, key):
  """Copy nvim config and packer plugins to the remote host.

  Args:
    user: Remote username.
    host: Remote hostname or IP.
    key: Path to SSH private key.
  """
  nvim_config = HOME / ".config" / "nvim"
  packer_dir = (
    HOME / ".local" / "share" / "nvim"
    / "site" / "pack" / "packer"
  )

  if not nvim_config.is_dir():
    print("[skip] No ~/.config/nvim found locally")
    return

  print("[..] Syncing nvim config...")
  # Ensure remote directory exists.
  _run_remote(user, host, key, "mkdir -p ~/.config/nvim")
  _rsync(
    str(nvim_config) + "/", "~/.config/nvim/",
    user, host, key, exclude=["pac/"],
  )

  if packer_dir.is_dir():
    remote_packer = (
      "~/.local/share/nvim/site/pack/packer/"
    )
    _run_remote(
      user, host, key,
      "mkdir -p ~/.local/share/nvim/site/pack/packer",
    )
    _rsync(
      str(packer_dir) + "/", remote_packer,
      user, host, key,
    )
  else:
    print("  [skip] No packer dir found locally")

  # Run treesitter update.
  print("[..] Updating treesitter parsers...")
  cmd = _ssh_cmd(user, host, key) + [
    "nvim --headless -c 'TSUpdateSync' -c 'qa'"
  ]
  result = subprocess.run(cmd, capture_output=True, text=True)
  if result.returncode != 0:
    print("  [warn] TSUpdateSync returned non-zero"
          " (may be ok on first run)")
  else:
    print("[ok] nvim config synced")


def configure_zshenv(user, host, key):
  """Write ~/.zshenv so ~/.local/bin is on PATH for SSH commands.

  Args:
    user: Remote username.
    host: Remote hostname or IP.
    key: Path to SSH private key.
  """
  marker = "# provision_vm: local-bin-path"
  check = _ssh_cmd(user, host, key) + [
    f"grep -q '{marker}' ~/.zshenv 2>/dev/null"
  ]
  result = subprocess.run(
    check, capture_output=True, text=True,
  )
  if result.returncode == 0:
    print("[ok] ~/.zshenv already configured")
    return

  print("[..] Configuring ~/.zshenv...")
  _run_remote(
    user, host, key,
    f'echo "{marker}\n'
    f'export PATH=\\"\\$HOME/.local/bin:\\$PATH\\"" '
    f'>> ~/.zshenv',
  )
  print("[ok] ~/.zshenv configured")


def set_default_shell(user, host, key):
  """Set the default shell to zsh on the remote host.

  Args:
    user: Remote username.
    host: Remote hostname or IP.
    key: Path to SSH private key.
  """
  result = _run_remote(
    user, host, key,
    f"getent passwd {user} | cut -d: -f7",
  )
  current_shell = result.stdout.strip()
  if current_shell == "/usr/bin/zsh":
    print("[ok] Default shell already zsh")
    return

  print("[..] Setting default shell to zsh...")
  _run_remote(
    user, host, key,
    f"sudo chsh -s /usr/bin/zsh {user}",
  )
  print("[ok] Default shell set to zsh")


def resolve_target(name):
  """Load target config from targets.yaml.

  Args:
    name: Target name.

  Returns:
    Tuple of (user, host, key_path).
  """
  config = load_targets_config()
  targets = config.get("targets", {})
  if name not in targets:
    print(f"Error: target '{name}' not found in"
          " config/targets.yaml")
    sys.exit(1)

  t = targets[name]
  user = t["user"]
  host = t["host"]
  key = Path(t["ssh_key"]).expanduser()
  return user, host, key


def provision(name):
  """Provision a target VM as a build environment.

  Args:
    name: Target name from config/targets.yaml.
  """
  print(f"Provisioning target: {name}")
  user, host, key = resolve_target(name)
  print(f"  Host: {host}")
  print(f"  User: {user}")
  print()

  install_packages(user, host, key)
  copy_zsh_config(user, host, key)
  copy_nvim_config(user, host, key)
  configure_zshenv(user, host, key)
  set_default_shell(user, host, key)

  print()
  print(f"Provisioning complete for {name}.")


def main():
  """Parse arguments and run provisioning."""
  parser = argparse.ArgumentParser(
    description="Provision a VM target as a build environment.",
  )
  parser.add_argument(
    "target", nargs="?", default="deb-01",
    help="Target name from config/targets.yaml"
         " (default: deb-01)",
  )
  args = parser.parse_args()
  provision(args.target)


if __name__ == "__main__":
  main()
