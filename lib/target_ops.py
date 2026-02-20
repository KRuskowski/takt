"""Target lock and config operations for programmatic use.

Extracted from bin/target.py so both the CLI and TUI dashboard
can share the same logic.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from lib.config import LOCKS_DIR, load_targets_config


def get_lock_path(name):
  """Return the lock file path for a target."""
  return LOCKS_DIR / f"{name}.lock"


def read_lock(name):
  """Read lock info for a target.

  Args:
    name: Target name.

  Returns:
    Dict with 'workspace' and 'claimed_at' keys, or None
    if the target is not locked.
  """
  lock_path = get_lock_path(name)
  if not lock_path.exists():
    return None
  try:
    with open(lock_path) as f:
      return json.load(f)
  except (json.JSONDecodeError, OSError):
    return None


def write_lock(name, workspace):
  """Write a lock file for a target.

  Args:
    name: Target name.
    workspace: Workspace name claiming the target.
  """
  lock_path = get_lock_path(name)
  LOCKS_DIR.mkdir(exist_ok=True)
  data = {
    "workspace": workspace,
    "claimed_at": datetime.now(timezone.utc).isoformat(),
  }
  with open(lock_path, "w") as f:
    json.dump(data, f, indent=2)


def release_lock(name):
  """Remove the lock file for a target.

  Args:
    name: Target name.

  Returns:
    The lock data that was removed, or None if not locked.
  """
  lock_path = get_lock_path(name)
  if not lock_path.exists():
    return None
  try:
    with open(lock_path) as f:
      data = json.load(f)
  except (json.JSONDecodeError, OSError):
    data = None
  lock_path.unlink(missing_ok=True)
  return data


def get_target(name):
  """Load a single target config by name.

  Args:
    name: Target name.

  Returns:
    Target config dict, or None if not found.
  """
  config = load_targets_config()
  targets = config.get("targets", {})
  if not targets or name not in targets:
    return None
  return targets[name]


def get_all_targets():
  """Load all target configs with lock status.

  Returns:
    List of dicts with keys: name, type, host, user, port,
    description, lock (dict or None).
  """
  config = load_targets_config()
  targets = config.get("targets", {})
  if not targets:
    return []

  results = []
  for name, cfg in sorted(targets.items()):
    lock = read_lock(name)
    results.append({
      "name": name,
      "type": cfg.get("type", "?"),
      "host": cfg.get("host", "?"),
      "user": cfg.get("user", ""),
      "port": cfg.get("port"),
      "description": cfg.get("description", ""),
      "lock": lock,
    })
  return results
