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
  create_testing_stage,
  create_workspace,
  delete_testing_stage,
  delete_workspace,
  get_workspace_status,
  list_testing_stages,
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
    print(f"{s['repo']:<30} {s['branch']:<25} {s['status']}")


def cmd_stage_create(args):
  """Create a testing stage for a workspace."""
  try:
    stage_dir = create_testing_stage(args.name)
  except FileNotFoundError as e:
    print(f"Error: {e}")
    sys.exit(1)
  except FileExistsError as e:
    print(f"Error: {e}")
    sys.exit(1)
  except GitError as e:
    print(f"Error: {e}")
    sys.exit(1)

  print(f"Testing stage created: {stage_dir}")
  print(f"Workspace origins re-pointed to testing stage.")


def cmd_stage_delete(args):
  """Delete a testing stage."""
  if not args.force:
    resp = input(
      f"Delete testing stage '{args.name}'? "
      f"Workspace origins will revert to root. [y/N] "
    )
    if resp.lower() != "y":
      print("Cancelled.")
      return

  try:
    delete_testing_stage(args.name)
  except FileNotFoundError as e:
    print(f"Error: {e}")
    sys.exit(1)

  print(f"Deleted testing stage '{args.name}'.")
  print("Workspace origins restored to root repos.")


def cmd_stage_list(args):
  """List all testing stages."""
  stages = list_testing_stages()
  if not stages:
    print("No testing stages found.")
    return

  print(f"{'Stage':<25} {'Repos':<40} {'Branch'}")
  print("-" * 80)

  for s in stages:
    repos_str = (
      ", ".join(s["repos"]) if s["repos"] else "(empty)"
    )
    print(
      f"{s['name']:<25} {repos_str:<40} {s['branch']}"
    )


def main():
  parser = argparse.ArgumentParser(
    description="Workspace management for multi-repo pipelines.",
  )
  sub = parser.add_subparsers(dest="command")

  # create
  p_create = sub.add_parser(
    "create", help="Create a new workspace.",
  )
  p_create.add_argument("name", help="Workspace (= branch) name.")
  p_create.add_argument(
    "repos", nargs="+", help="Repos to include.",
  )

  # list
  sub.add_parser("list", help="List all workspaces.")

  # delete
  p_delete = sub.add_parser(
    "delete", help="Delete a workspace.",
  )
  p_delete.add_argument("name", help="Workspace name to delete.")
  p_delete.add_argument(
    "-f", "--force", action="store_true",
    help="Skip confirmation prompt.",
  )

  # status
  p_status = sub.add_parser(
    "status", help="Show repo status in a workspace.",
  )
  p_status.add_argument("name", help="Workspace name.")

  # stage create
  p_stage_create = sub.add_parser(
    "stage-create",
    help="Create a testing stage for a workspace.",
  )
  p_stage_create.add_argument(
    "name", help="Workspace name to create stage for.",
  )

  # stage delete
  p_stage_delete = sub.add_parser(
    "stage-delete",
    help="Delete a testing stage.",
  )
  p_stage_delete.add_argument(
    "name", help="Testing stage name to delete.",
  )
  p_stage_delete.add_argument(
    "-f", "--force", action="store_true",
    help="Skip confirmation prompt.",
  )

  # stage list
  sub.add_parser(
    "stage-list", help="List all testing stages.",
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
    "stage-create": cmd_stage_create,
    "stage-delete": cmd_stage_delete,
    "stage-list": cmd_stage_list,
  }
  commands[args.command](args)


if __name__ == "__main__":
  main()
