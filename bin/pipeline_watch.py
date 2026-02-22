#!/usr/bin/env python3
"""Pipeline watcher — thin CLI for branch change detection.

Core logic lives in lib/pipeline.py and lib/db.py. This
script provides a simple CLI for manual poll cycles.
"""

import argparse
import sys
from pathlib import Path

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib import db
from lib.config import load_repos_config
from lib.pipeline import (
  find_changes,
  group_by_branch,
  snapshot_all_refs,
)

DEFAULT_INTERVAL = 30


def cmd_watch(args):
  """CLI entry point for pipeline_watch."""
  db.migrate()

  if args.reset:
    old = db.load_refs()
    if old:
      db.save_refs({})
      print("Cleared stored branch refs.")
    else:
      print("No stored refs to clear.")
    return

  if args.once:
    repos_config = load_repos_config()
    old_refs = db.load_refs()
    new_refs = snapshot_all_refs(repos_config)
    if not old_refs:
      print(
        "First run — snapshotting current branch refs."
      )
      db.save_refs(new_refs)
      print(f"Stored {len(new_refs)} branch refs.")
      return
    changes = find_changes(old_refs, new_refs)
    if changes:
      groups = group_by_branch(changes)
      print(
        f"Detected changes in "
        f"{len(groups)} branch(es):"
      )
      for branch, branch_changes in groups.items():
        repos_affected = [
          c["repo"] for c in branch_changes
        ]
        print(
          f"  {branch}: {', '.join(repos_affected)}"
        )
    else:
      print("No changes detected.")
    db.save_refs(new_refs)
    return

  print(
    "Continuous watching is deprecated. Use "
    "takt-service:\n"
    "  systemctl --user start takt-service\n"
    "  journalctl --user -u takt-service -f"
  )


def main():
  """Parse args and run."""
  parser = argparse.ArgumentParser(
    description="Pipeline watcher utility.",
  )
  parser.add_argument(
    "--interval", type=int, default=DEFAULT_INTERVAL,
    help=(
      "Poll interval in seconds "
      f"(default: {DEFAULT_INTERVAL}). Deprecated."
    ),
  )
  parser.add_argument(
    "--once", action="store_true",
    help="Run a single poll cycle and exit.",
  )
  parser.add_argument(
    "--reset", action="store_true",
    help="Clear stored branch refs and exit.",
  )
  args = parser.parse_args()
  cmd_watch(args)


if __name__ == "__main__":
  main()
