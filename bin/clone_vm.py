#!/usr/bin/env python3
"""VM clone management CLI.

Create and delete qcow2-backed clones from template VMs.
Clones use backing files so they're fast to create and
space-efficient (only store diffs from the template).

Requires sudo for create/delete operations.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib.config import (
  load_takt_config, load_targets_config, save_targets_config,
)
from lib.ssh_utils import SSHError, run_ssh
from lib.target_ops import get_target

# All clone disks go here (has space).
_takt = load_takt_config()
CLONE_DISK_DIR = Path(_takt.get(
  "clone_disk_dir", "/home/karl/libvirt/images",
))

# SSH settings for targets.
DEFAULT_SSH_KEY = Path("~/.ssh/id_ed25519_targets").expanduser()

# Timeout for waiting for SSH after boot.
SSH_WAIT_TIMEOUT = 120
SSH_WAIT_INTERVAL = 5


def _run(cmd, check=True, **kwargs):
  """Run a subprocess command, printing it first.

  Args:
    cmd: Command as list of strings.
    check: Raise on non-zero exit.
    **kwargs: Passed to subprocess.run.

  Returns:
    CompletedProcess instance.
  """
  print(f"  $ {' '.join(str(c) for c in cmd)}")
  return subprocess.run(
    cmd, capture_output=True, text=True, check=check, **kwargs,
  )


def _virsh_state(name):
  """Get the libvirt domain state.

  Args:
    name: Domain name.

  Returns:
    State string (e.g. 'running', 'shut off') or None if
    the domain doesn't exist.
  """
  result = _run(
    ["virsh", "domstate", name], check=False,
  )
  if result.returncode != 0:
    return None
  return result.stdout.strip()


def _shutdown_vm(name):
  """Shut down a VM and wait for it to stop.

  Args:
    name: Domain name.
  """
  state = _virsh_state(name)
  if state is None:
    return
  if state == "shut off":
    print(f"  {name} is already shut off.")
    return

  print(f"  Shutting down {name}...")
  _run(["virsh", "shutdown", name])
  for _ in range(60):
    time.sleep(2)
    if _virsh_state(name) == "shut off":
      print(f"  {name} is shut off.")
      return
  raise RuntimeError(f"Timeout waiting for {name} to shut down.")


def _create_backing_disk(template_disk, clone_disk):
  """Create a qcow2 disk with a backing file.

  Args:
    template_disk: Path to the template disk (backing file).
    clone_disk: Path for the new clone disk.
  """
  clone_disk.parent.mkdir(parents=True, exist_ok=True)
  _run([
    "qemu-img", "create",
    "-f", "qcow2",
    "-F", "qcow2",
    "-b", str(template_disk),
    str(clone_disk),
  ])


def _clone_domain(template, name, clone_disk):
  """Clone a libvirt domain with virt-clone.

  Uses --preserve-data to keep the backing file link intact.

  Args:
    template: Template domain name.
    name: Clone domain name.
    clone_disk: Path to the clone disk.
  """
  _run([
    "virt-clone",
    "--original", template,
    "--name", name,
    "--preserve-data",
    "--file", str(clone_disk),
  ])


def _reconfigure_debian(clone_disk, name, ip):
  """Reconfigure a Debian clone offline via virt-customize.

  Sets hostname, static IP, and regenerates SSH host keys.

  Args:
    clone_disk: Path to the clone disk.
    name: New hostname for the clone.
    ip: New static IP address.
  """
  if not shutil.which("virt-customize"):
    raise RuntimeError(
      "virt-customize not found. "
      "Install libguestfs-tools: "
      "sudo apt install libguestfs-tools"
    )

  # Update /etc/network/interfaces for the new IP.
  sed_ip = (
    f"s/address [0-9.]*/address {ip}/g"
  )
  _run([
    "virt-customize",
    "-a", str(clone_disk),
    "--hostname", name,
    "--run-command", f"sed -i '{sed_ip}' /etc/network/interfaces",
    "--run-command",
    "rm -f /etc/ssh/ssh_host_*_key /etc/ssh/ssh_host_*_key.pub",
    "--run-command", "ssh-keygen -A",
  ])


def _reconfigure_windows(name, ip, template_cfg):
  """Reconfigure a Windows clone via SSH.

  Boots the clone with the template's IP, SSHes in, and runs
  PowerShell commands to set the new hostname and IP. Then
  restarts the VM to apply changes.

  Args:
    name: Clone domain name (becomes the new hostname).
    ip: New static IP address.
    template_cfg: Template target config dict (for
      user, host, ssh_key).
  """
  user = template_cfg.get("user", "worker")
  host = template_cfg.get("host")
  key = template_cfg.get("ssh_key")
  if key:
    key = str(Path(key).expanduser())

  # Boot the clone (it starts with the template's IP).
  print(f"  Starting {name} for Windows reconfiguration...")
  _run(["virsh", "start", name])
  _wait_for_ssh(host, user=user, key=key)

  # Set hostname.
  print(f"  Setting hostname to {name}...")
  run_ssh(
    host,
    f"powershell -Command \"Rename-Computer -NewName '{name}'"
    f" -Force\"",
    user=user, key=key,
  )

  # Set static IP. Assumes a single adapter named 'Ethernet'.
  print(f"  Setting IP to {ip}...")
  ps_cmd = (
    f"powershell -Command \""
    f"New-NetIPAddress -InterfaceAlias 'Ethernet'"
    f" -IPAddress '{ip}' -PrefixLength 20"
    f" -DefaultGateway '10.101.0.1'"
    f" -ErrorAction SilentlyContinue;"
    f" Get-NetIPAddress -InterfaceAlias 'Ethernet'"
    f" -AddressFamily IPv4"
    f" | Where-Object {{ $_.IPAddress -ne '{ip}' }}"
    f" | Remove-NetIPAddress -Confirm:\\$false"
    f" -ErrorAction SilentlyContinue"
    f"\""
  )
  run_ssh(host, ps_cmd, user=user, key=key)

  # Restart to apply hostname + IP changes.
  print(f"  Restarting {name}...")
  _run(["virsh", "shutdown", name])
  _shutdown_wait(name)
  _run(["virsh", "start", name])

  # Wait for SSH on the new IP.
  _wait_for_ssh(ip, user=user, key=key)
  print(f"  Windows clone {name} ready at {ip}.")


def _shutdown_wait(name, timeout=60):
  """Wait for a VM to reach 'shut off' state.

  Args:
    name: Domain name.
    timeout: Max seconds to wait.
  """
  for _ in range(timeout // 2):
    time.sleep(2)
    if _virsh_state(name) == "shut off":
      return
  raise RuntimeError(f"Timeout waiting for {name} to shut down.")


def _wait_for_ssh(host, user=None, key=None):
  """Wait for SSH to become available on a host.

  Args:
    host: Hostname or IP.
    user: SSH user.
    key: SSH key path.
  """
  print(f"  Waiting for SSH on {host}...")
  deadline = time.time() + SSH_WAIT_TIMEOUT
  while time.time() < deadline:
    try:
      run_ssh(host, "echo ok", user=user, key=key, timeout=5)
      print(f"  SSH ready on {host}.")
      return
    except SSHError:
      time.sleep(SSH_WAIT_INTERVAL)
  raise RuntimeError(
    f"Timeout waiting for SSH on {host} "
    f"(waited {SSH_WAIT_TIMEOUT}s)."
  )


def _register_target(name, template, ip, template_cfg):
  """Register a clone in targets.yaml.

  Args:
    name: Clone name.
    template: Template name this was cloned from.
    ip: Clone IP address.
    template_cfg: Template config dict.
  """
  config = load_targets_config()
  targets = config.setdefault("targets", {})
  clone_disk = CLONE_DISK_DIR / f"{name}.qcow2"
  entry = {
    "type": "vm",
    "host": ip,
    "user": template_cfg.get("user", "worker"),
    "ssh_key": template_cfg.get(
      "ssh_key", "~/.ssh/id_ed25519_targets",
    ),
    "disk": str(clone_disk),
    "cloned_from": template,
    "description": f"Clone of {template}",
  }
  if template_cfg.get("os"):
    entry["os"] = template_cfg["os"]
  targets[name] = entry
  save_targets_config(config)
  print(f"  Registered {name} in targets.yaml.")


def _unregister_target(name):
  """Remove a clone from targets.yaml.

  Args:
    name: Clone name.
  """
  config = load_targets_config()
  targets = config.get("targets", {})
  if name in targets:
    del targets[name]
    save_targets_config(config)
    print(f"  Removed {name} from targets.yaml.")


def create_clone(template, name, ip):
  """Create a VM clone from a template.

  Args:
    template: Template target name (must have template: true).
    name: Name for the new clone.
    ip: Static IP address for the clone.
  """
  # Validate template.
  template_cfg = get_target(template)
  if template_cfg is None:
    print(f"Error: target '{template}' not found.")
    sys.exit(1)
  if not template_cfg.get("template"):
    print(f"Error: '{template}' is not a template.")
    sys.exit(1)

  template_disk = Path(template_cfg["disk"])
  if not template_disk.exists():
    print(f"Error: template disk not found: {template_disk}")
    sys.exit(1)

  # Check clone doesn't already exist.
  if get_target(name) is not None:
    print(f"Error: target '{name}' already exists.")
    sys.exit(1)

  clone_disk = CLONE_DISK_DIR / f"{name}.qcow2"
  if clone_disk.exists():
    print(f"Error: clone disk already exists: {clone_disk}")
    sys.exit(1)

  is_windows = template_cfg.get("os") == "windows"

  print(f"Creating clone '{name}' from template '{template}'")
  print(f"  Template disk: {template_disk}")
  print(f"  Clone disk:    {clone_disk}")
  print(f"  Clone IP:      {ip}")
  print()

  # Step 1: Shut down template if running.
  print("[1/7] Ensuring template is shut down...")
  _shutdown_vm(template)

  # Step 2: Set template disk to read-only.
  print("[2/7] Setting template disk to read-only...")
  os.chmod(template_disk, 0o444)
  print(f"  {template_disk} -> 0444")

  # Step 3: Create clone disk with backing file.
  print("[3/7] Creating clone disk...")
  _create_backing_disk(template_disk, clone_disk)

  # Step 4: Clone the libvirt domain.
  print("[4/7] Cloning libvirt domain...")
  _clone_domain(template, name, clone_disk)

  # Step 5: OS-specific reconfiguration.
  print("[5/7] Reconfiguring clone...")
  if is_windows:
    # Windows: boot with template IP, SSH in, reconfigure.
    _reconfigure_windows(name, ip, template_cfg)
  else:
    # Debian: offline reconfiguration via virt-customize.
    _reconfigure_debian(clone_disk, name, ip)

  # Step 6: Register in targets.yaml.
  print("[6/7] Registering clone in targets.yaml...")
  _register_target(name, template, ip, template_cfg)

  # Step 7: Start clone and verify SSH.
  print("[7/7] Starting clone...")
  if not is_windows:
    # Windows clones are already booted after reconfiguration.
    _run(["virsh", "start", name])
    _wait_for_ssh(ip, user=template_cfg.get("user"), key=(
      str(Path(template_cfg["ssh_key"]).expanduser())
      if template_cfg.get("ssh_key") else None
    ))

  print()
  print(f"Clone '{name}' created successfully.")
  print(f"  IP: {ip}")
  print(f"  Use: bin/target.py status {name}")


def delete_clone(name):
  """Delete a VM clone.

  Shuts down the VM, undefines it, deletes the disk, and
  removes the target from targets.yaml.

  Args:
    name: Clone target name.
  """
  target = get_target(name)
  if target is None:
    print(f"Error: target '{name}' not found.")
    sys.exit(1)
  if target.get("template"):
    print(f"Error: '{name}' is a template, not a clone.")
    sys.exit(1)

  is_windows = target.get("os") == "windows"
  disk = target.get("disk")

  print(f"Deleting clone '{name}'")

  # Shut down if running.
  _shutdown_vm(name)

  # Undefine the domain.
  undefine_cmd = ["virsh", "undefine", name]
  if is_windows:
    undefine_cmd += ["--nvram", "--tpm"]
  _run(undefine_cmd, check=False)

  # Delete the disk.
  if disk:
    disk_path = Path(disk)
    if disk_path.exists():
      disk_path.unlink()
      print(f"  Deleted disk: {disk_path}")
    else:
      print(f"  Disk not found (already deleted?): {disk_path}")

  # Remove from targets.yaml.
  _unregister_target(name)

  print(f"Clone '{name}' deleted.")


def main():
  """Entry point."""
  parser = argparse.ArgumentParser(
    description="Create and delete VM clones from templates.",
  )
  sub = parser.add_subparsers(dest="subcmd")

  # create
  p_create = sub.add_parser(
    "create", help="Create a clone from a template.",
  )
  p_create.add_argument("template", help="Template target name.")
  p_create.add_argument("name", help="Clone name.")
  p_create.add_argument(
    "--ip", required=True, help="Static IP for the clone.",
  )

  # delete
  p_delete = sub.add_parser("delete", help="Delete a clone.")
  p_delete.add_argument("name", help="Clone name.")

  args = parser.parse_args()
  if not args.subcmd:
    parser.print_help()
    sys.exit(1)

  if args.subcmd == "create":
    create_clone(args.template, args.name, args.ip)
  elif args.subcmd == "delete":
    delete_clone(args.name)


if __name__ == "__main__":
  main()
