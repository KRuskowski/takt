#!/usr/bin/env python3
"""Push branches from root repos to GitHub.

Pushes from ~/dev/root/<repo> (root repos) to their GitHub remote,
respecting dependency push order from repos.yaml.
"""

import argparse
import sys
from pathlib import Path

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib.config import get_repo_path, load_repos_config, validate_repo
from lib.git_utils import GitError, get_branches, push_branch


def find_repos_with_branch(branch, repos_config, limit_repos=None):
  """Find all repos that have the given branch.

  Args:
    branch: Branch name to look for.
    repos_config: Full repos config dict.
    limit_repos: Optional list of repo names to limit search.

  Returns:
    List of (repo_name, config) tuples sorted by push_order.
  """
  repos = repos_config.get("repos", {})
  found = []

  for repo_name, cfg in repos.items():
    if limit_repos and repo_name not in limit_repos:
      continue
    repo_path = get_repo_path(cfg.get("path", repo_name))
    if not validate_repo(cfg.get("path", repo_name)):
      continue
    try:
      branches = get_branches(repo_path)
      if branch in branches:
        found.append((repo_name, cfg))
    except GitError:
      continue

  # Sort by push_order.
  found.sort(key=lambda x: x[1].get("push_order", 999))
  return found


def cmd_push(args):
  """Push a branch to GitHub from root repos."""
  branch = args.branch
  repos_config = load_repos_config()

  limit = args.repos if args.repos else None
  found = find_repos_with_branch(branch, repos_config, limit)

  if not found:
    print(f"No repos found with branch '{branch}'.")
    sys.exit(1)

  print(f"Branch '{branch}' found in {len(found)} repo(s):")
  print(f"{'Order':<7} {'Repo':<30} {'Description'}")
  print("-" * 60)
  for repo_name, cfg in found:
    order = cfg.get("push_order", "?")
    desc = cfg.get("description", "")
    print(f"{order:<7} {repo_name:<30} {desc}")

  if args.dry_run:
    print("\n(dry run — nothing pushed)")
    return

  if not args.yes:
    resp = input(f"\nPush '{branch}' to GitHub? [y/N] ").lower()
    if resp != "y":
      print("Cancelled.")
      return

  # Push in order.
  errors = []
  for repo_name, cfg in found:
    repo_path = get_repo_path(cfg.get("path", repo_name))
    print(f"\nPushing {repo_name}...", end=" ", flush=True)
    try:
      push_branch(repo_path, branch)
      print("OK")
    except GitError as e:
      print("FAILED")
      print(f"  {e}")
      errors.append(repo_name)

  if errors:
    print(f"\nFailed to push: {', '.join(errors)}")
    sys.exit(1)
  else:
    print("\nAll repos pushed successfully.")


def main():
  parser = argparse.ArgumentParser(
    description="Push branches from root repos to GitHub.",
  )
  parser.add_argument(
    "branch", help="Branch name to push.",
  )
  parser.add_argument(
    "--dry-run", action="store_true",
    help="Show what would be pushed without pushing.",
  )
  parser.add_argument(
    "--repos", nargs="+",
    help="Limit to specific repos.",
  )
  parser.add_argument(
    "-y", "--yes", action="store_true",
    help="Skip confirmation prompt.",
  )

  args = parser.parse_args()
  cmd_push(args)


if __name__ == "__main__":
  main()
