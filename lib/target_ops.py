"""Target lock and config operations for programmatic use.

Extracted from bin/target.py so the CLI, the REST API and the
MCP server can share the same logic.

Claims live in SQLite, not in lock files. A file under
.locks/ is only visible to processes on the machine holding
it, so once runs execute on more than one machine two of
them can claim the same VM and neither can see the other.
The table also makes claiming atomic, which check-the-file-
then-write never was: two claimants could both pass the
check and the second would silently win.

Lock files left over from the previous scheme are imported
on first use, so claims in flight at upgrade time are not
lost.
"""

import json
import shutil
import subprocess

from lib import db
from lib.config import LOCKS_DIR, load_targets_config

# Imported-once-per-process flag for the file lock import.
_imported_file_locks = False


def get_lock_path(name):
  """Return the legacy lock file path for a target."""
  return LOCKS_DIR / f"{name}.lock"


def import_file_locks(db_path=None):
  """Move any legacy .lock files into the table.

  Idempotent and cheap: does nothing once the directory has
  no lock files left. A file whose target is already
  claimed in the table is discarded rather than overwriting
  the live claim.

  Args:
    db_path: Override path for testing.

  Returns:
    List of target names imported.
  """
  if not LOCKS_DIR.exists():
    return []
  imported = []
  for path in sorted(LOCKS_DIR.glob("*.lock")):
    name = path.stem
    try:
      data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
      data = {}
    workspace = data.get("workspace")
    claimed_at = data.get("claimed_at")
    ok, _existing = db.claim_target(
      name, workspace, claimed_at=claimed_at,
      db_path=db_path,
    )
    if ok:
      imported.append(name)
    path.unlink(missing_ok=True)
  return imported


def _ensure_imported(db_path=None):
  """Import legacy lock files once per process."""
  global _imported_file_locks
  if _imported_file_locks:
    return
  _imported_file_locks = True
  try:
    names = import_file_locks(db_path=db_path)
  except Exception:
    # Never let an unreadable leftover file block claiming.
    return
  if names:
    print(
      f"takt: imported {len(names)} target lock(s) into "
      f"the database: {', '.join(names)}"
    )


def read_lock(name, db_path=None):
  """Read lock info for a target.

  Args:
    name: Target name.
    db_path: Override path for testing.

  Returns:
    Dict with 'workspace' and 'claimed_at' keys, or None
    if the target is not claimed.
  """
  _ensure_imported(db_path=db_path)
  return db.get_target_lock(name, db_path=db_path)


def claim_lock(name, workspace, worker_id=None,
               db_path=None):
  """Claim a target, failing if someone already holds it.

  Atomic, unlike read-then-write: the insert either creates
  the only row for that target or it does not happen.

  Args:
    name: Target name.
    workspace: Workspace claiming it.
    worker_id: Worker making the claim, if any.
    db_path: Override path for testing.

  Returns:
    (ok, existing) — existing is the current holder's lock
    when ok is False.
  """
  _ensure_imported(db_path=db_path)
  return db.claim_target(
    name, workspace, worker_id=worker_id, db_path=db_path
  )


def write_lock(name, workspace, db_path=None):
  """Claim a target, taking it over if already claimed.

  Kept for callers that want the old unconditional
  behaviour. Prefer claim_lock(), which refuses rather than
  stealing.

  Args:
    name: Target name.
    workspace: Workspace name claiming the target.
    db_path: Override path for testing.
  """
  _ensure_imported(db_path=db_path)
  db.claim_target(
    name, workspace, takeover=True, db_path=db_path
  )


def release_lock(name, db_path=None):
  """Release a target.

  Args:
    name: Target name.
    db_path: Override path for testing.

  Returns:
    The lock data that was removed, or None if not claimed.
  """
  _ensure_imported(db_path=db_path)
  return db.release_target(name, db_path=db_path)


def list_locks(db_path=None):
  """All current target claims.

  Args:
    db_path: Override path for testing.

  Returns:
    Dict of target name -> lock dict.
  """
  _ensure_imported(db_path=db_path)
  return db.list_target_locks(db_path=db_path)


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


def is_template(name):
  """Check if a target is a template.

  Args:
    name: Target name.

  Returns:
    True if the target exists and has template: true.
  """
  target = get_target(name)
  if target is None:
    return False
  return target.get("template", False)


def get_vm_state(name):
  """Get the libvirt domain state for a VM target.

  Args:
    name: Domain/target name.

  Returns:
    State string (e.g. 'running', 'shut off') or None if
    virsh is not installed or the domain doesn't exist.
  """
  if not shutil.which("virsh"):
    return None
  try:
    result = subprocess.run(
      ["virsh", "domstate", name],
      capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
      return None
    return result.stdout.strip()
  except (subprocess.TimeoutExpired, OSError):
    return None


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
    t_type = cfg.get("type", "?")
    vm_state = None
    if t_type == "vm":
      vm_state = get_vm_state(name)
    results.append({
      "name": name,
      "type": t_type,
      "host": cfg.get("host", "?"),
      "user": cfg.get("user", ""),
      "port": cfg.get("port"),
      "description": cfg.get("description", ""),
      "template": cfg.get("template", False),
      "vm_state": vm_state,
      "lock": lock,
    })
  return results
