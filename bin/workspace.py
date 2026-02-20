#!/usr/bin/env python3
"""Workspace management CLI.

Create, list, delete, and inspect workspaces for multi-repo
agent pipelines.
"""

import argparse
import shutil
import sys
from pathlib import Path
from string import Template

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib.config import (
  CONTEXT_DIR,
  TEMPLATES_DIR,
  WORKSPACES_DIR,
  get_default_branch,
  get_repo_path,
  load_repos_config,
  validate_repo,
)
from lib.git_utils import (
  GitError,
  clone_local,
  create_branch,
  get_current_branch,
  get_status,
)


def cmd_create(args):
  """Create a new workspace with local clones of specified repos."""
  name = args.name
  repos = args.repos
  ws_dir = WORKSPACES_DIR / name

  if ws_dir.exists():
    print(f"Error: workspace '{name}' already exists at {ws_dir}")
    sys.exit(1)

  # Validate all repos before doing anything (fail-fast).
  invalid = [r for r in repos if not validate_repo(r)]
  if invalid:
    print(f"Error: not valid git repos: {', '.join(invalid)}")
    sys.exit(1)

  # Load repos config for metadata.
  repos_config = load_repos_config().get("repos", {})

  print(f"Creating workspace '{name}' with repos: {', '.join(repos)}")
  ws_dir.mkdir(parents=True)

  # Clone each repo and create the workspace branch.
  for repo_name in repos:
    source = get_repo_path(repo_name)
    dest = ws_dir / repo_name
    print(f"  Cloning {repo_name}...")
    try:
      clone_local(source, dest)
      create_branch(dest, name)
      print(f"    -> branch '{name}' created")
    except GitError as e:
      print(f"  Error cloning {repo_name}: {e}")
      print("Cleaning up...")
      shutil.rmtree(ws_dir, ignore_errors=True)
      sys.exit(1)

  # Generate workspace CLAUDE.md from template.
  _generate_workspace_claude_md(ws_dir, name, repos, repos_config)

  # Copy context packets.
  ctx_dest = ws_dir / "context"
  if CONTEXT_DIR.is_dir():
    shutil.copytree(CONTEXT_DIR, ctx_dest)
    print(f"  Copied context packets to {ctx_dest}")

  print(f"\nWorkspace created: {ws_dir}")
  print(f"Branch: {name}")


def _generate_workspace_claude_md(ws_dir, name, repos, repos_config):
  """Generate a workspace CLAUDE.md from the template."""
  tmpl_path = TEMPLATES_DIR / "workspace_claude.md"
  if not tmpl_path.exists():
    print("  Warning: workspace template not found, skipping.")
    return

  tmpl = Template(tmpl_path.read_text())

  # Build repo table rows.
  rows = []
  for repo_name in repos:
    cfg = repos_config.get(repo_name, {})
    repo_path = ws_dir / repo_name
    default_br = cfg.get(
      "default_branch", get_default_branch(repo_path)
    )
    push_order = cfg.get("push_order", "?")
    rows.append(f"| {repo_name} | {default_br} | {push_order} |")
  repo_table = "\n".join(rows)

  # Build context packet listing.
  packets = []
  if CONTEXT_DIR.is_dir():
    for f in sorted(CONTEXT_DIR.iterdir()):
      if f.is_file() and f.suffix == ".md":
        packets.append(f"- `context/{f.name}`")
  context_packets = "\n".join(packets) if packets else "- (none)"

  # Build in-scope repo list.
  in_scope = "\n".join(f"- `{r}/`" for r in repos)

  content = tmpl.safe_substitute(
    workspace_name=name,
    role_section="(specify role when launching agent)",
    task_section="(specify task description)",
    acceptance_criteria="(specify acceptance criteria)",
    in_scope_repos=in_scope,
    reference_repos="- (none specified)",
    context_packets=context_packets,
    repo_table=repo_table,
    status="Not started",
  )

  out_path = ws_dir / "CLAUDE.md"
  out_path.write_text(content)
  print(f"  Generated {out_path}")


def cmd_list(args):
  """List all workspaces."""
  if not WORKSPACES_DIR.exists():
    print("No workspaces directory found.")
    return

  workspaces = sorted(
    d for d in WORKSPACES_DIR.iterdir() if d.is_dir()
  )
  if not workspaces:
    print("No workspaces found.")
    return

  print(f"{'Workspace':<25} {'Repos':<40} {'Branch'}")
  print("-" * 80)

  for ws_dir in workspaces:
    name = ws_dir.name
    repos = sorted(
      d.name for d in ws_dir.iterdir()
      if d.is_dir() and (d / ".git").exists()
    )
    # Get branch from first repo.
    branch = "?"
    if repos:
      try:
        branch = get_current_branch(ws_dir / repos[0])
      except GitError:
        pass
    repos_str = ", ".join(repos) if repos else "(empty)"
    print(f"{name:<25} {repos_str:<40} {branch}")


def cmd_delete(args):
  """Delete a workspace."""
  name = args.name
  ws_dir = WORKSPACES_DIR / name

  if not ws_dir.exists():
    print(f"Error: workspace '{name}' not found.")
    sys.exit(1)

  if not args.force:
    resp = input(f"Delete workspace '{name}' at {ws_dir}? [y/N] ")
    if resp.lower() != "y":
      print("Cancelled.")
      return

  shutil.rmtree(ws_dir)
  print(f"Deleted workspace '{name}'.")


def cmd_status(args):
  """Show status of repos in a workspace."""
  name = args.name
  ws_dir = WORKSPACES_DIR / name

  if not ws_dir.exists():
    print(f"Error: workspace '{name}' not found.")
    sys.exit(1)

  repos = sorted(
    d.name for d in ws_dir.iterdir()
    if d.is_dir() and (d / ".git").exists()
  )

  if not repos:
    print(f"Workspace '{name}' has no repos.")
    return

  print(f"Workspace: {name}")
  print(f"{'Repo':<30} {'Branch':<25} {'Status'}")
  print("-" * 80)

  for repo_name in repos:
    repo_path = ws_dir / repo_name
    try:
      branch = get_current_branch(repo_path)
    except GitError:
      branch = "?"
    try:
      status = get_status(repo_path)
      status_str = status if status else "clean"
    except GitError:
      status_str = "error"
    # Condense multiline status.
    if "\n" in status_str:
      lines = status_str.splitlines()
      status_str = f"{len(lines)} changed files"
    print(f"{repo_name:<30} {branch:<25} {status_str}")


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

  args = parser.parse_args()
  if not args.command:
    parser.print_help()
    sys.exit(1)

  commands = {
    "create": cmd_create,
    "list": cmd_list,
    "delete": cmd_delete,
    "status": cmd_status,
  }
  commands[args.command](args)


if __name__ == "__main__":
  main()
