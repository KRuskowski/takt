#!/usr/bin/env python3
"""Target management CLI.

Manage build/test targets (VMs and hardware) with claim/release
locking, VM lifecycle, and SSH command execution.
"""

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib.config import LOCKS_DIR, load_targets_config
from lib.ssh_utils import SSHError, check_connectivity, run_ssh


def _get_lock_path(name):
  """Return the lock file path for a target."""
  return LOCKS_DIR / f"{name}.lock"


def _read_lock(name):
  """Read lock info for a target. Returns dict or None."""
  lock_path = _get_lock_path(name)
  if not lock_path.exists():
    return None
  with open(lock_path) as f:
    return json.load(f)


def _write_lock(name, workspace):
  """Write a lock file for a target."""
  lock_path = _get_lock_path(name)
  LOCKS_DIR.mkdir(exist_ok=True)
  data = {
    "workspace": workspace,
    "claimed_at": datetime.now(timezone.utc).isoformat(),
  }
  with open(lock_path, "w") as f:
    json.dump(data, f, indent=2)


def _get_target(name):
  """Load and return a target config by name."""
  config = load_targets_config()
  targets = config.get("targets", {})
  if not targets or name not in targets:
    return None
  return targets[name]


def cmd_list(args):
  """List all targets with claim status."""
  config = load_targets_config()
  targets = config.get("targets", {})

  if not targets:
    print("No targets configured.")
    print("Edit config/targets.yaml to add targets.")
    return

  print(
    f"{'Name':<15} {'Type':<10} {'Host':<20} "
    f"{'Claimed By':<20} {'Description'}"
  )
  print("-" * 80)

  for name, cfg in sorted(targets.items()):
    lock = _read_lock(name)
    claimed = lock["workspace"] if lock else "-"
    print(
      f"{name:<15} {cfg.get('type', '?'):<10} "
      f"{cfg.get('host', '?'):<20} {claimed:<20} "
      f"{cfg.get('description', '')}"
    )


def cmd_claim(args):
  """Claim a target for a workspace."""
  name = args.name
  workspace = args.workspace

  target = _get_target(name)
  if target is None:
    print(f"Error: target '{name}' not found.")
    sys.exit(1)

  lock = _read_lock(name)
  if lock:
    print(
      f"Error: target '{name}' already claimed by "
      f"'{lock['workspace']}' at {lock['claimed_at']}."
    )
    sys.exit(1)

  _write_lock(name, workspace)
  print(f"Claimed '{name}' for workspace '{workspace}'.")


def cmd_release(args):
  """Release a target."""
  name = args.name

  lock_path = _get_lock_path(name)
  if not lock_path.exists():
    print(f"Target '{name}' is not claimed.")
    return

  lock = _read_lock(name)
  lock_path.unlink()
  ws = lock["workspace"] if lock else "unknown"
  print(f"Released '{name}' (was claimed by '{ws}').")


def cmd_up(args):
  """Start a VM target."""
  name = args.name
  target = _get_target(name)

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
    print(f"Failed to start VM '{name}': {result.stderr.strip()}")


def cmd_down(args):
  """Stop a VM target."""
  name = args.name
  target = _get_target(name)

  if target is None:
    print(f"Error: target '{name}' not found.")
    sys.exit(1)

  if target.get("type") == "hardware":
    print(f"Target '{name}' is hardware — cannot shut down.")
    return

  if not shutil.which("virsh"):
    print(
      f"Warning: virsh not installed. Cannot stop VM '{name}'."
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
    print(f"Failed to stop VM '{name}': {result.stderr.strip()}")


def cmd_run(args):
  """Run a command on a target via SSH."""
  name = args.name
  command = args.command
  target = _get_target(name)

  if target is None:
    print(f"Error: target '{name}' not found.")
    sys.exit(1)

  host = target.get("host")
  user = target.get("user")
  port = target.get("port")

  if not host:
    print(f"Error: no host configured for target '{name}'.")
    sys.exit(1)

  print(f"Running on {name} ({host}): {command}")
  try:
    output = run_ssh(host, command, user=user, port=port)
    if output:
      print(output)
  except SSHError as e:
    print(f"SSH error: {e}")
    sys.exit(1)


def cmd_status(args):
  """Show target details and connectivity."""
  name = args.name
  target = _get_target(name)

  if target is None:
    print(f"Error: target '{name}' not found.")
    sys.exit(1)

  lock = _read_lock(name)
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
    print(f"  Connectivity: ", end="", flush=True)
    reachable = check_connectivity(
      host, user=target.get("user"), port=target.get("port"),
    )
    print("OK" if reachable else "UNREACHABLE")


def main():
  parser = argparse.ArgumentParser(
    description="Manage build/test targets.",
  )
  sub = parser.add_subparsers(dest="command")

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
  if not args.command:
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
  commands[args.command](args)


if __name__ == "__main__":
  main()
