#!/usr/bin/env python3
"""Workspace management CLI.

Create, list, delete, and inspect workspaces. Define
pipelines in SQLite and view run history.
"""

import argparse
import json
import sys
from pathlib import Path

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib import db
from lib.config import parse_pipeline_roles
from lib.git_utils import GitError
from lib.pipeline import SCRIPT_REGISTRY
from lib.workspace_ops import (
  create_workspace,
  delete_workspace,
  get_workspace_status,
  list_workspaces,
)


def cmd_create(args):
  """Create a new workspace with local clones."""
  try:
    ws_dir = create_workspace(args.name, args.repos)
  except FileExistsError as e:
    print(f"Error: {e}")
    sys.exit(1)
  except ValueError as e:
    print(f"Error: {e}")
    sys.exit(1)
  except GitError as e:
    print(f"Error cloning: {e}")
    sys.exit(1)
  print(f"Workspace created: {ws_dir}")
  print(f"Branch: {args.name}")


def cmd_list(args):
  """List all workspaces."""
  workspaces = list_workspaces()
  if not workspaces:
    print("No workspaces found.")
    return
  print(f"{'Workspace':<25} {'Repos':<40} {'Branch'}")
  print("-" * 80)
  for ws in workspaces:
    repos_str = (
      ", ".join(ws["repos"]) if ws["repos"] else "(empty)"
    )
    print(
      f"{ws['name']:<25} {repos_str:<40} {ws['branch']}"
    )


def cmd_delete(args):
  """Delete a workspace."""
  label = f"workspace '{args.name}'"
  if not args.force:
    resp = input(f"Delete {label}? [y/N] ")
    if resp.lower() != "y":
      print("Cancelled.")
      return
  try:
    delete_workspace(args.name)
  except FileNotFoundError as e:
    print(f"Error: {e}")
    sys.exit(1)
  print(f"Deleted {label}.")


def cmd_status(args):
  """Show status of repos in a workspace."""
  try:
    statuses = get_workspace_status(args.name)
  except FileNotFoundError as e:
    print(f"Error: {e}")
    sys.exit(1)
  if not statuses:
    print(f"Workspace '{args.name}' has no repos.")
    return
  print(f"Workspace: {args.name}")
  print(f"{'Repo':<30} {'Branch':<25} {'Status'}")
  print("-" * 80)
  for s in statuses:
    print(
      f"{s['repo']:<30} {s['branch']:<25} {s['status']}"
    )


def cmd_pipeline_set(args):
  """Define pipeline steps for a workspace."""
  db.migrate()
  roles = parse_pipeline_roles()
  steps = []
  for name in args.steps:
    if name in SCRIPT_REGISTRY:
      steps.append({
        "name": name,
        "step_type": "script",
      })
    elif name in roles:
      steps.append({
        "name": name,
        "step_type": "agent",
      })
    else:
      print(
        f"Error: unknown step '{name}'. "
        f"Known roles: {', '.join(sorted(roles))}. "
        f"Known scripts: "
        f"{', '.join(sorted(SCRIPT_REGISTRY))}."
      )
      sys.exit(1)
  db.define_pipeline(args.name, steps)
  print(f"Pipeline set for '{args.name}':")
  for i, s in enumerate(steps):
    print(f"  {i}: {s['name']} ({s['step_type']})")


def cmd_pipeline_show(args):
  """Show configured pipeline steps."""
  db.migrate()
  steps = db.get_pipeline(args.name)
  if not steps:
    print(f"No pipeline defined for '{args.name}'.")
    return
  print(f"Pipeline for '{args.name}':")
  print(f"{'Seq':<5} {'Name':<20} {'Type':<10} {'Timeout'}")
  print("-" * 50)
  for s in steps:
    print(
      f"{s['seq']:<5} {s['name']:<20} "
      f"{s['step_type']:<10} {s['timeout_secs']}s"
    )


def cmd_runs(args):
  """Show pipeline run history from SQLite."""
  db.migrate()
  runs = db.list_runs(args.name, limit=args.limit)
  if not runs:
    print(f"No pipeline runs for '{args.name}'.")
    return
  print(f"Pipeline runs for '{args.name}':")
  print(
    f"{'ID':<6} {'Created':<26} {'Status':<10} "
    f"{'Trigger':<8} Repos"
  )
  print("-" * 70)
  for run in runs:
    repos = json.loads(run.get("repos_json", "[]"))
    repos_str = ", ".join(repos) if repos else "-"
    print(
      f"{run['id']:<6} {run['created_at']:<26} "
      f"{run['status']:<10} {run['trigger']:<8} "
      f"{repos_str}"
    )


def main():
  """Parse args and run."""
  parser = argparse.ArgumentParser(
    description=(
      "Workspace management for multi-repo pipelines."
    ),
  )
  sub = parser.add_subparsers(dest="command")

  # create
  p_create = sub.add_parser(
    "create", help="Create a new workspace.",
  )
  p_create.add_argument(
    "name", help="Workspace (= branch) name.",
  )
  p_create.add_argument(
    "repos", nargs="+", help="Repos to include.",
  )

  # list
  sub.add_parser("list", help="List all workspaces.")

  # delete
  p_delete = sub.add_parser(
    "delete", help="Delete a workspace.",
  )
  p_delete.add_argument(
    "name", help="Workspace name to delete.",
  )
  p_delete.add_argument(
    "-f", "--force", action="store_true",
    help="Skip confirmation prompt.",
  )

  # status
  p_status = sub.add_parser(
    "status", help="Show repo status in a workspace.",
  )
  p_status.add_argument("name", help="Workspace name.")

  # pipeline-set
  p_pset = sub.add_parser(
    "pipeline-set",
    help="Define pipeline steps for a workspace.",
  )
  p_pset.add_argument(
    "name", help="Workspace name.",
  )
  p_pset.add_argument(
    "steps", nargs="+",
    help=(
      "Step names: role slugs (test, review) "
      "or scripts (push_to_github, create_pr)."
    ),
  )

  # pipeline-show
  p_pshow = sub.add_parser(
    "pipeline-show",
    help="Show configured pipeline steps.",
  )
  p_pshow.add_argument(
    "name", help="Workspace name.",
  )

  # runs
  p_runs = sub.add_parser(
    "runs",
    help="Show pipeline run history.",
  )
  p_runs.add_argument(
    "name", help="Workspace name.",
  )
  p_runs.add_argument(
    "-n", "--limit", type=int, default=20,
    help="Max runs to show (default: 20).",
  )

  args = parser.parse_args()
  if not args.command:
    parser.print_help()
    sys.exit(1)

  commands = {
    "create": cmd_create,
    "list": cmd_list,
    "delete": cmd_delete,
    "status": cmd_status,
    "pipeline-set": cmd_pipeline_set,
    "pipeline-show": cmd_pipeline_show,
    "runs": cmd_runs,
  }
  commands[args.command](args)


if __name__ == "__main__":
  main()
