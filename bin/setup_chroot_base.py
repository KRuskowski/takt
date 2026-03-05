#!/usr/bin/env python3
"""Create the shared base rootfs for workspace chroots.

Runs debootstrap to build a minimal Debian trixie rootfs, then
installs build tools, Node.js, claude CLI, and creates the worker
user. Requires sudo. Idempotent — skips steps already done.

Usage:
  sudo python3 bin/setup_chroot_base.py
"""

import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib.config import CHROOT_BASE_DIR

# Marker files to track completed steps.
_MARKERS_DIR = CHROOT_BASE_DIR / ".setup_markers"
DEBOOTSTRAP_SUITE = "trixie"

# Packages to install inside the chroot.
APT_PACKAGES = [
  "build-essential",
  "ca-certificates",
  "clang",
  "clang-format",
  "clang-tidy",
  "cmake",
  "curl",
  "git",
  "libgstreamer1.0-dev",
  "libmodbus-dev",
  "libssl-dev",
  "locales",
  "mold",
  "ninja-build",
  "openssh-client",
  "pkg-config",
  "python3",
  "python3-pip",
  "python3-venv",
  "sudo",
  "wget",
  "zsh",
]

PIP_PACKAGES = [
  "pytest",
  "cpplint",
  "flake8",
]

NODESOURCE_VERSION = "22"
WORKER_UID = 1000
WORKER_SHELL = "/usr/bin/zsh"


def _run(cmd, **kwargs):
  """Run a command, printing it first."""
  print(f"  $ {' '.join(str(c) for c in cmd)}")
  return subprocess.run(cmd, check=True, **kwargs)


def _chroot_run(cmd, **kwargs):
  """Run a command inside the chroot."""
  env = kwargs.pop("env", None) or os.environ.copy()
  env.setdefault("LANG", "C")
  env.setdefault("LC_ALL", "C")
  full = ["chroot", str(CHROOT_BASE_DIR)] + list(cmd)
  return _run(full, env=env, **kwargs)


def _marker_done(name):
  """Check if a setup step is already completed."""
  return (_MARKERS_DIR / name).exists()


def _mark_done(name):
  """Mark a setup step as completed."""
  _MARKERS_DIR.mkdir(parents=True, exist_ok=True)
  (_MARKERS_DIR / name).touch()


def step_debootstrap():
  """Bootstrap minimal Debian rootfs."""
  if _marker_done("debootstrap"):
    print("[skip] debootstrap already done.")
    return
  print("[1/7] Running debootstrap...")
  CHROOT_BASE_DIR.mkdir(parents=True, exist_ok=True)
  _run([
    "debootstrap", "--variant=minbase",
    DEBOOTSTRAP_SUITE, str(CHROOT_BASE_DIR),
  ])
  _mark_done("debootstrap")


def step_locale():
  """Configure en_US.UTF-8 locale."""
  if _marker_done("locale"):
    print("[skip] locale already configured.")
    return
  print("[2/7] Configuring locale...")
  _chroot_run(["apt-get", "install", "-y", "locales"])
  locale_gen = CHROOT_BASE_DIR / "etc" / "locale.gen"
  locale_gen.write_text("en_US.UTF-8 UTF-8\n")
  _chroot_run(["locale-gen"])
  env_file = CHROOT_BASE_DIR / "etc" / "default" / "locale"
  env_file.parent.mkdir(parents=True, exist_ok=True)
  env_file.write_text(
    "LANG=en_US.UTF-8\nLC_ALL=en_US.UTF-8\n"
  )
  _mark_done("locale")


def step_apt_packages():
  """Install APT packages."""
  if _marker_done("apt_packages"):
    print("[skip] APT packages already installed.")
    return
  print("[3/7] Installing APT packages...")
  _chroot_run([
    "apt-get", "update",
  ])
  _chroot_run([
    "apt-get", "install", "-y", "--no-install-recommends",
  ] + APT_PACKAGES)
  _chroot_run(["apt-get", "clean"])
  _mark_done("apt_packages")


def step_nodejs():
  """Install Node.js via nodesource."""
  if _marker_done("nodejs"):
    print("[skip] Node.js already installed.")
    return
  print("[4/7] Installing Node.js...")
  # Add nodesource repo.
  setup_url = (
    f"https://deb.nodesource.com/setup_{NODESOURCE_VERSION}.x"
  )
  _chroot_run([
    "bash", "-c",
    f"curl -fsSL {setup_url} | bash -",
  ])
  _chroot_run([
    "apt-get", "install", "-y", "nodejs",
  ])
  _chroot_run(["apt-get", "clean"])
  _mark_done("nodejs")


def step_claude_cli():
  """Claude CLI is bind-mounted from host — nothing to install."""
  print("[skip] claude CLI bind-mounted from host.")


def step_pip_packages():
  """Install pip packages."""
  if _marker_done("pip_packages"):
    print("[skip] pip packages already installed.")
    return
  print("[6/7] Installing pip packages...")
  _chroot_run([
    "pip3", "install", "--break-system-packages",
  ] + PIP_PACKAGES)
  _mark_done("pip_packages")


def step_worker_user():
  """Create worker user with passwordless sudo."""
  if _marker_done("worker_user"):
    print("[skip] worker user already exists.")
    return
  print("[7/7] Creating worker user...")
  _chroot_run([
    "useradd",
    "--uid", str(WORKER_UID),
    "--create-home",
    "--shell", WORKER_SHELL,
    "worker",
  ])
  # Passwordless sudo for worker.
  sudoers = (
    CHROOT_BASE_DIR / "etc" / "sudoers.d" / "worker"
  )
  sudoers.parent.mkdir(parents=True, exist_ok=True)
  sudoers.write_text("worker ALL=(ALL) NOPASSWD: ALL\n")
  sudoers.chmod(0o440)
  _mark_done("worker_user")


def main():
  """Run all setup steps."""
  if os.geteuid() != 0:
    print("Error: must run as root (use sudo).")
    sys.exit(1)
  print(f"Base rootfs: {CHROOT_BASE_DIR}")
  step_debootstrap()
  step_locale()
  step_apt_packages()
  step_nodejs()
  step_claude_cli()
  step_pip_packages()
  step_worker_user()
  print("\nBase rootfs setup complete.")


if __name__ == "__main__":
  main()
