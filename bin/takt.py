#!/home/karl/dev/takt/.venv/bin/python3
"""takt — unified CLI for workspace orchestration.

Usage:
  takt ws list|create|delete|status
  takt chroot <name> [cmd...]
  takt target list|claim|release|up|down|run|status
  takt pipeline set|show|runs
  takt push <branch> [--dry-run] [-y] [--repos ...]
  takt service start|stop|restart|status
"""

import argparse
import sys
from pathlib import Path

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib.commands import CommandResult, dispatch


def _add_names_only(parser):
  """Add --names-only flag to a parser."""
  parser.add_argument(
    "--names-only", action="store_true",
    help="Print names only (for shell completion).",
  )


def build_parser():
  """Build the argparse parser tree."""
  parser = argparse.ArgumentParser(
    prog="takt",
    description="Unified CLI for takt workspace orchestration.",
  )
  sub = parser.add_subparsers(dest="group")

  # -- ws --
  ws = sub.add_parser("ws", help="Workspace management.")
  ws_sub = ws.add_subparsers(dest="subcmd")

  ws_list = ws_sub.add_parser(
    "list", help="List all workspaces.",
  )
  _add_names_only(ws_list)

  ws_create = ws_sub.add_parser(
    "create", help="Create a new workspace.",
  )
  ws_create.add_argument(
    "name", help="Workspace (= branch) name.",
  )
  ws_create.add_argument(
    "repos", nargs="+", help="Repos to include.",
  )
  ws_create.add_argument(
    "--chroot", action="store_true",
    help="Create an isolated chroot environment.",
  )

  ws_delete = ws_sub.add_parser(
    "delete", help="Delete a workspace.",
  )
  ws_delete.add_argument(
    "name", help="Workspace name to delete.",
  )
  ws_delete.add_argument(
    "-f", "--force", action="store_true",
    help="Skip confirmation prompt.",
  )

  ws_status = ws_sub.add_parser(
    "status", help="Show repo status in a workspace.",
  )
  ws_status.add_argument("name", help="Workspace name.")

  # -- chroot --
  chroot = sub.add_parser(
    "chroot",
    help="Enter workspace chroot (or run a command).",
  )
  chroot.add_argument("name", help="Workspace name.")
  chroot.add_argument(
    "cmd", nargs="*",
    help="Command to run (default: interactive shell).",
  )

  # -- target --
  tgt = sub.add_parser("target", help="Target management.")
  tgt_sub = tgt.add_subparsers(dest="subcmd")

  tgt_list = tgt_sub.add_parser(
    "list", help="List all targets.",
  )
  _add_names_only(tgt_list)

  tgt_claim = tgt_sub.add_parser(
    "claim", help="Claim a target.",
  )
  tgt_claim.add_argument("name", help="Target name.")
  tgt_claim.add_argument(
    "workspace", help="Workspace claiming it.",
  )

  tgt_release = tgt_sub.add_parser(
    "release", help="Release a target.",
  )
  tgt_release.add_argument("name", help="Target name.")

  tgt_up = tgt_sub.add_parser(
    "up", help="Start a VM target.",
  )
  tgt_up.add_argument("name", help="Target name.")

  tgt_down = tgt_sub.add_parser(
    "down", help="Stop a VM target.",
  )
  tgt_down.add_argument("name", help="Target name.")

  tgt_run = tgt_sub.add_parser(
    "run", help="Run a command on a target.",
  )
  tgt_run.add_argument("name", help="Target name.")
  tgt_run.add_argument("command", help="Command to run.")

  tgt_status = tgt_sub.add_parser(
    "status", help="Show target details.",
  )
  tgt_status.add_argument("name", help="Target name.")

  # -- pipeline --
  pipe = sub.add_parser(
    "pipeline", help="Pipeline management.",
  )
  pipe_sub = pipe.add_subparsers(dest="subcmd")

  pipe_set = pipe_sub.add_parser(
    "set", help="Define pipeline steps.",
  )
  pipe_set.add_argument(
    "workspace", help="Workspace name.",
  )
  pipe_set.add_argument(
    "steps", nargs="+",
    help="Step names (role slugs or scripts).",
  )

  pipe_show = pipe_sub.add_parser(
    "show", help="Show configured pipeline.",
  )
  pipe_show.add_argument(
    "workspace", help="Workspace name.",
  )

  pipe_runs = pipe_sub.add_parser(
    "runs", help="Show pipeline run history.",
  )
  pipe_runs.add_argument(
    "workspace", help="Workspace name.",
  )
  pipe_runs.add_argument(
    "-n", "--limit", type=int, default=20,
    help="Max runs to show (default: 20).",
  )

  # -- push --
  p_push = sub.add_parser(
    "push",
    help="Push branches from root repos to GitHub.",
  )
  p_push.add_argument(
    "branch", help="Branch name to push.",
  )
  p_push.add_argument(
    "--dry-run", action="store_true",
    help="Show what would be pushed.",
  )
  p_push.add_argument(
    "--repos", nargs="+",
    help="Limit to specific repos.",
  )
  p_push.add_argument(
    "-y", "--yes", action="store_true",
    help="Skip confirmation prompt.",
  )

  # -- service --
  svc = sub.add_parser(
    "service", help="takt-service management.",
  )
  svc_sub = svc.add_subparsers(dest="subcmd")
  svc_sub.add_parser("start", help="Start service.")
  svc_sub.add_parser("stop", help="Stop service.")
  svc_sub.add_parser("restart", help="Restart service.")
  svc_sub.add_parser("status", help="Show service status.")

  return parser


def main():
  """Parse args and dispatch."""
  parser = build_parser()
  args = parser.parse_args()

  if not args.group:
    parser.print_help()
    sys.exit(0)

  group = args.group

  # -- chroot: special (no subcmd) --
  if group == "chroot":
    result = dispatch(
      "chroot", "enter",
      name=args.name,
      cmd=args.cmd if args.cmd else None,
    )
    if result.output:
      print(result.output)
    rc = result.data.get("returncode", 0) if result.ok else 1
    sys.exit(rc)

  # -- push: special (no subcmd) --
  if group == "push":
    if not args.yes:
      # Interactive confirmation.
      from lib.commands import push as _push
      from lib.config import (
        get_repo_path, load_repos_config, validate_repo,
      )
      from lib.git_utils import GitError, get_branches
      repos_config = load_repos_config()
      all_repos = repos_config.get("repos", {})
      found = []
      for repo_name, cfg in all_repos.items():
        if args.repos and repo_name not in args.repos:
          continue
        rp = get_repo_path(cfg.get("path", repo_name))
        if not validate_repo(cfg.get("path", repo_name)):
          continue
        try:
          branches = get_branches(rp)
          if args.branch in branches:
            found.append((repo_name, cfg))
        except GitError:
          continue
      if found and not args.dry_run:
        found.sort(
          key=lambda x: x[1].get("push_order", 999),
        )
        print(
          f"Branch '{args.branch}' found in "
          f"{len(found)} repo(s):"
        )
        for repo_name, cfg in found:
          order = cfg.get("push_order", "?")
          print(f"  {order}: {repo_name}")
        resp = input(
          f"\nPush '{args.branch}' to GitHub? [y/N] "
        ).lower()
        if resp != "y":
          print("Cancelled.")
          sys.exit(0)
    result = dispatch(
      "push", "push",
      branch=args.branch,
      repos=args.repos,
      dry_run=args.dry_run,
      yes=True,
    )
    if result.output:
      print(result.output)
    sys.exit(0 if result.ok else 1)

  # -- Groups with subcommands --
  subcmd = getattr(args, "subcmd", None)
  if not subcmd:
    # Print group help.
    parser.parse_args([group, "-h"])
    sys.exit(0)

  # Build kwargs from args.
  kwargs = {}
  skip = {"group", "subcmd"}
  for key, val in vars(args).items():
    if key in skip:
      continue
    kwargs[key] = val

  # ws delete: interactive confirmation.
  if group == "ws" and subcmd == "delete":
    if not kwargs.get("force"):
      resp = input(
        f"Delete workspace '{kwargs['name']}'? [y/N] "
      ).lower()
      if resp != "y":
        print("Cancelled.")
        sys.exit(0)

  result = dispatch(group, subcmd, **kwargs)
  if result.output:
    print(result.output)
  sys.exit(0 if result.ok else 1)


if __name__ == "__main__":
  main()
