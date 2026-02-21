#!/usr/bin/env python3
"""Target management CLI.

Manage build/test targets (VMs and hardware) with claim/release
locking, VM lifecycle, and SSH command execution.
"""

import argparse
import shutil
import sys
from pathlib import Path

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib.ssh_utils import SSHError, check_connectivity, run_ssh
from lib.target_ops import (
  get_all_targets,
  get_target,
  is_template,
  read_lock,
  release_lock,
  write_lock,
)


def cmd_list(args):
  """List all targets with claim status."""
  targets = get_all_targets()

  if not targets:
    print("No targets configured.")
    print("Edit config/targets.yaml to add targets.")
    return

  print(
    f"{'Name':<15} {'Type':<10} {'Host':<20} "
    f"{'Claimed By':<20} {'Description'}"
  )
  print("-" * 80)

  for t in targets:
    lock = t["lock"]
    claimed = lock["workspace"] if lock else "-"
    tag = " [template]" if t.get("template") else ""
    print(
      f"{t['name']:<15} {t['type']:<10} "
      f"{t['host']:<20} {claimed:<20} "
      f"{t['description']}{tag}"
    )


def cmd_claim(args):
  """Claim a target for a workspace."""
  name = args.name
  workspace = args.workspace

  target = get_target(name)
  if target is None:
    print(f"Error: target '{name}' not found.")
    sys.exit(1)

  if target.get("template"):
    print(
      f"Error: '{name}' is a template. "
      f"Use bin/clone_vm.py to create a clone."
    )
    sys.exit(1)

  lock = read_lock(name)
  if lock:
    print(
      f"Error: target '{name}' already claimed by "
      f"'{lock['workspace']}' at {lock['claimed_at']}."
    )
    sys.exit(1)

  write_lock(name, workspace)
  print(f"Claimed '{name}' for workspace '{workspace}'.")


def cmd_release(args):
  """Release a target."""
  name = args.name
  lock = release_lock(name)
  if lock is None:
    print(f"Target '{name}' is not claimed.")
    return
  ws = lock.get("workspace", "unknown")
  print(f"Released '{name}' (was claimed by '{ws}').")


def cmd_up(args):
  """Start a VM target."""
  name = args.name
  target = get_target(name)

  if target is None:
    print(f"Error: target '{name}' not found.")
    sys.exit(1)

  if target.get("type") == "hardware":
    print(f"Target '{name}' is hardware — always on.")
    return

  # Check for virsh.
  if not shutil.which("virsh"):
    print(
      f"Warning: virsh not installed. Cannot start VM '{name}'."
    )
    print("Install libvirt to manage VMs.")
    return

  import subprocess
  result = subprocess.run(
    ["virsh", "start", name],
    capture_output=True, text=True,
  )
  if result.returncode == 0:
    print(f"Started VM '{name}'.")
  else:
    print(
      f"Failed to start VM '{name}': "
      f"{result.stderr.strip()}"
    )


def cmd_down(args):
  """Stop a VM target."""
  name = args.name
  target = get_target(name)

  if target is None:
    print(f"Error: target '{name}' not found.")
    sys.exit(1)

  if target.get("type") == "hardware":
    print(f"Target '{name}' is hardware — cannot shut down.")
    return

  if not shutil.which("virsh"):
    print(
      f"Warning: virsh not installed. "
      f"Cannot stop VM '{name}'."
    )
    return

  import subprocess
  result = subprocess.run(
    ["virsh", "shutdown", name],
    capture_output=True, text=True,
  )
  if result.returncode == 0:
    print(f"Shutting down VM '{name}'.")
  else:
    print(
      f"Failed to stop VM '{name}': "
      f"{result.stderr.strip()}"
    )


def cmd_run(args):
  """Run a command on a target via SSH."""
  name = args.name
  command = args.command
  target = get_target(name)

  if target is None:
    print(f"Error: target '{name}' not found.")
    sys.exit(1)

  host = target.get("host")
  user = target.get("user")
  port = target.get("port")
  key = target.get("ssh_key")
  if key:
    key = str(Path(key).expanduser())

  if not host:
    print(f"Error: no host configured for target '{name}'.")
    sys.exit(1)

  print(f"Running on {name} ({host}): {command}")
  try:
    output = run_ssh(host, command, user=user, port=port, key=key)
    if output:
      print(output)
  except SSHError as e:
    print(f"SSH error: {e}")
    sys.exit(1)


def cmd_status(args):
  """Show target details and connectivity."""
  name = args.name
  target = get_target(name)

  if target is None:
    print(f"Error: target '{name}' not found.")
    sys.exit(1)

  lock = read_lock(name)
  print(f"Target: {name}")
  print(f"  Type: {target.get('type', '?')}")
  print(f"  Host: {target.get('host', '?')}")
  print(f"  User: {target.get('user', '?')}")
  print(f"  Description: {target.get('description', '')}")

  if lock:
    print(f"  Claimed by: {lock['workspace']}")
    print(f"  Claimed at: {lock['claimed_at']}")
  else:
    print("  Claimed by: (none)")

  host = target.get("host")
  if host:
    key = target.get("ssh_key")
    if key:
      key = str(Path(key).expanduser())
    print("  Connectivity: ", end="", flush=True)
    reachable = check_connectivity(
      host, user=target.get("user"),
      port=target.get("port"), key=key,
    )
    print("OK" if reachable else "UNREACHABLE")


def main():
  parser = argparse.ArgumentParser(
    description="Manage build/test targets.",
  )
  sub = parser.add_subparsers(dest="subcmd")

  # list
  sub.add_parser("list", help="List all targets.")

  # claim
  p_claim = sub.add_parser("claim", help="Claim a target.")
  p_claim.add_argument("name", help="Target name.")
  p_claim.add_argument("workspace", help="Workspace claiming it.")

  # release
  p_release = sub.add_parser("release", help="Release a target.")
  p_release.add_argument("name", help="Target name.")

  # up
  p_up = sub.add_parser("up", help="Start a VM target.")
  p_up.add_argument("name", help="Target name.")

  # down
  p_down = sub.add_parser("down", help="Stop a VM target.")
  p_down.add_argument("name", help="Target name.")

  # run
  p_run = sub.add_parser(
    "run", help="Run a command on a target.",
  )
  p_run.add_argument("name", help="Target name.")
  p_run.add_argument("command", help="Command to run.")

  # status
  p_status = sub.add_parser(
    "status", help="Show target details.",
  )
  p_status.add_argument("name", help="Target name.")

  args = parser.parse_args()
  if not args.subcmd:
    parser.print_help()
    sys.exit(1)

  commands = {
    "list": cmd_list,
    "claim": cmd_claim,
    "release": cmd_release,
    "up": cmd_up,
    "down": cmd_down,
    "run": cmd_run,
    "status": cmd_status,
  }
  commands[args.subcmd](args)


if __name__ == "__main__":
  main()
