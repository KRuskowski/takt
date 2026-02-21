#!/usr/bin/env python3
"""Workspace management CLI.

Create, list, delete, and inspect workspaces for multi-repo
agent pipelines.
"""

import argparse
import sys
from pathlib import Path

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib.git_utils import GitError
from lib.workspace_ops import (
  create_stage,
  create_workspace,
  delete_stage,
  delete_workspace,
  get_pipeline,
  get_workspace_status,
  list_stages,
  list_workspaces,
)


def cmd_create(args):
  """Create a new workspace with local clones of specified repos."""
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
  if not args.force:
    resp = input(
      f"Delete workspace '{args.name}'? [y/N] "
    )
    if resp.lower() != "y":
      print("Cancelled.")
      return

  try:
    delete_workspace(args.name)
  except FileNotFoundError as e:
    print(f"Error: {e}")
    sys.exit(1)

  print(f"Deleted workspace '{args.name}'.")


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


def cmd_stage_add(args):
  """Add a stage to a workspace's pipeline."""
  try:
    stage_dir = create_stage(args.name, args.role)
  except (FileNotFoundError, FileExistsError,
          ValueError) as e:
    print(f"Error: {e}")
    sys.exit(1)
  except GitError as e:
    print(f"Error: {e}")
    sys.exit(1)

  print(f"Stage '{args.role}' created: {stage_dir}")
  pipeline = get_pipeline(args.name)
  print(f"Pipeline: {pipeline['chain']}")


def cmd_stage_remove(args):
  """Remove a stage from a workspace's pipeline."""
  if not args.force:
    resp = input(
      f"Remove stage '{args.role}' from "
      f"'{args.name}'? [y/N] "
    )
    if resp.lower() != "y":
      print("Cancelled.")
      return

  try:
    delete_stage(args.name, args.role)
  except FileNotFoundError as e:
    print(f"Error: {e}")
    sys.exit(1)

  print(
    f"Removed stage '{args.role}' from '{args.name}'."
  )


def cmd_stage_list(args):
  """List stages."""
  workspace = getattr(args, "workspace", None)
  stages = list_stages(workspace)
  if not stages:
    if workspace:
      print(f"No stages for workspace '{workspace}'.")
    else:
      print("No stages found.")
    return

  print(
    f"{'Workspace':<20} {'Role':<15} "
    f"{'Repos':<30} {'Branch'}"
  )
  print("-" * 80)

  for s in stages:
    repos_str = (
      ", ".join(s["repos"]) if s["repos"] else "(empty)"
    )
    print(
      f"{s['workspace']:<20} {s['role']:<15} "
      f"{repos_str:<30} {s['branch']}"
    )


def cmd_pipeline(args):
  """Show the full pipeline chain for a workspace."""
  try:
    pipeline = get_pipeline(args.name)
  except FileNotFoundError as e:
    print(f"Error: {e}")
    sys.exit(1)

  print(f"Workspace: {args.name}")
  print(f"Chain: {pipeline['chain']}")
  if pipeline["stages"]:
    print(f"Stages: {', '.join(pipeline['stages'])}")
  else:
    print("Stages: (none)")


def main():
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

  # stage-add
  p_stage_add = sub.add_parser(
    "stage-add",
    help="Add a stage to a workspace's pipeline.",
  )
  p_stage_add.add_argument(
    "name", help="Workspace name.",
  )
  p_stage_add.add_argument(
    "role", help="Role slug (e.g. test, review, deploy_qa).",
  )

  # stage-remove
  p_stage_remove = sub.add_parser(
    "stage-remove",
    help="Remove a stage from a workspace's pipeline.",
  )
  p_stage_remove.add_argument(
    "name", help="Workspace name.",
  )
  p_stage_remove.add_argument(
    "role", help="Role slug to remove.",
  )
  p_stage_remove.add_argument(
    "-f", "--force", action="store_true",
    help="Skip confirmation prompt.",
  )

  # stage-list
  p_stage_list = sub.add_parser(
    "stage-list", help="List all stages.",
  )
  p_stage_list.add_argument(
    "workspace", nargs="?", default=None,
    help="Optional workspace name to filter by.",
  )

  # pipeline
  p_pipeline = sub.add_parser(
    "pipeline",
    help="Show the full pipeline chain for a workspace.",
  )
  p_pipeline.add_argument(
    "name", help="Workspace name.",
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
    "stage-add": cmd_stage_add,
    "stage-remove": cmd_stage_remove,
    "stage-list": cmd_stage_list,
    "pipeline": cmd_pipeline,
  }
  commands[args.command](args)


if __name__ == "__main__":
  main()
