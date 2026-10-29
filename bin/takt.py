#!/home/karl/dev/takt/.venv/bin/python3
"""takt — the single CLI for takt.

Usage:
  takt ws list|create|delete|status
  takt target list|claim|release|up|down|run|status
  takt pipeline set|show|runs
  takt push <branch> [--dry-run] [-y] [--repos ...]
  takt service start|stop|restart|status
  takt worker --scheduler <url> --id <name> [...]
  takt reclaim [--older-than N] [--apply] [...]

Subcommands that own a long-running process or their own
output (worker, reclaim) contribute their flags from their
own module and are invoked directly, rather than going
through lib.commands.dispatch(). The standalone scripts
(bin/takt_worker.py, bin/reclaim.py) remain as thin
wrappers over the same functions, so neither entry point
can drift from the other.
"""

import argparse
import sys
from pathlib import Path

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

# lib.commands is NOT imported here. It reaches
# lib.pipeline -> lib.agent_runner -> claude_agent_sdk, which
# only the scheduler needs. A worker machine is promised to
# need nothing but the `claude` CLI, so importing it at
# module scope would make `takt worker` die on exactly the
# machines this command exists for. Imported inside main()
# instead, after the worker and reclaim paths have exited.


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

  # -- worker --
  # Flags come from the worker module itself, so this and
  # bin/takt_worker.py cannot drift apart.
  wrk = sub.add_parser(
    "worker",
    help="Execute pipeline runs on this machine.",
    description=(
      "Dial out to a takt scheduler, lease runs, execute "
      "them here, and report back."
    ),
  )
  _worker_module().add_worker_args(wrk)

  # -- provision --
  prov = sub.add_parser(
    "provision",
    help="Set up another machine as a takt worker.",
    description=(
      "Install takt on a machine, give it credentials for "
      "this scheduler, and push the root repos to it. Run "
      "from the scheduler: the repos travel outward over "
      "the SSH connection that already works, so the "
      "worker needs no git credentials of its own."
    ),
  )
  prov.add_argument(
    "host",
    help="user@host, or an ssh config alias (e.g. laptop).",
  )
  prov.add_argument(
    "--id", dest="worker_id", default=None,
    help="Name to register under. Default: remote hostname.",
  )
  prov.add_argument(
    "--repos", nargs="+", default=None,
    help="Root repos to push. Default: all managed repos.",
  )
  prov.add_argument(
    "--skip-repos", action="store_true",
    help="Set the worker up but push no repos.",
  )
  prov.add_argument(
    "--scheduler", default=None,
    help=(
      "URL the worker should dial. Must be reachable from "
      "the worker, so not loopback."
    ),
  )
  prov.add_argument(
    "--ref", default="master",
    help="takt branch to check out on the worker.",
  )
  prov.add_argument(
    "--check", action="store_true",
    help=(
      "Only run the checks (clean SSH channel, required "
      "tools); change nothing."
    ),
  )
  prov.add_argument(
    "--service", action="store_true",
    help=(
      "Install, enable and start the worker as a systemd "
      "user service, with lingering so it keeps taking "
      "work with nobody logged in."
    ),
  )
  prov.add_argument(
    "--scheduler-host", default=None,
    help=(
      "user@host holding the root repos, for the worker's "
      "--upstream scheduler. Used in the service unit."
    ),
  )
  prov.add_argument(
    "--upstream", default="scheduler",
    help=(
      "Where the worker's mirrors refresh from: "
      "'scheduler' or 'github'."
    ),
  )
  prov.add_argument(
    "--no-agent-steps", action="store_true",
    help=(
      "Declare the worker build-only. Use when the Claude "
      "CLI is installed but not signed in yet, so the "
      "scheduler does not hand it agent runs that fail."
    ),
  )

  # -- reclaim --
  rec = sub.add_parser(
    "reclaim",
    help="Report/reclaim build output in workspaces.",
    description=(
      "Survey regenerable build output. Dry run unless "
      "--apply is given."
    ),
  )
  _reclaim_module().add_reclaim_args(rec)

  return parser


def _load_bin_module(name):
  """Import a sibling bin/ script as a module.

  The scripts are executables rather than a package, so
  they are loaded by path. Done lazily: building the parser
  must not pay for every subcommand's imports.

  Args:
    name: Script name without the .py suffix.

  Returns:
    The imported module.
  """
  import importlib.util
  path = Path(__file__).resolve().parent / f"{name}.py"
  spec = importlib.util.spec_from_file_location(
    f"takt_bin_{name}", str(path)
  )
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def _worker_module():
  """Load bin/takt_worker.py."""
  return _load_bin_module("takt_worker")


def _run_provision(args):
  """Provision a worker machine, reporting as it goes.

  Args:
    args: Parsed arguments for the provision group.

  Returns:
    Process exit code.
  """
  from lib.provision import ProvisionError, provision

  def progress(stage, detail):
    label = {
      "channel": "checking ssh channel",
      "prereqs": "checking tools",
      "takt": "installing takt",
      "credentials": "writing credentials",
      "repo": "pushing",
      "verify": "verifying",
    }.get(stage, stage)
    print(f"  {label}: {detail}", flush=True)

  try:
    if args.check:
      # Dispatch on the remote platform here too. Running
      # the POSIX probes against cmd.exe fails on `printf`
      # and says nothing useful about the machine.
      from lib import provision as _prov
      from lib import provision_windows as _win
      ops = _win if _win.is_windows(args.host) else _prov
      flavour = "windows" if ops is _win else "posix"
      hostname = ops.check_channel(args.host)
      print(
        f"  ssh channel to {hostname} ({flavour}): clean"
      )
      tools = ops.check_prereqs(args.host)
      for name, present in sorted(tools.items()):
        print(f"    {name:<14} {'yes' if present else 'no'}")
      if not tools.get("claude"):
        print(
          "  note: no claude CLI, so this machine would be "
          "build-only and never given agent steps."
        )
      return 0

    result = provision(
      args.host,
      worker_id=args.worker_id,
      repos=args.repos,
      scheduler_url=args.scheduler,
      ref=args.ref,
      skip_repos=args.skip_repos,
      service=args.service,
      scheduler_host=args.scheduler_host,
      upstream=args.upstream,
      agent_steps=not args.no_agent_steps,
      on_progress=progress,
    )
  except ProvisionError as e:
    print(f"takt provision: {e}", file=sys.stderr)
    return 1

  caps = result["capabilities"]
  print()
  print(f"  {result['worker_id']} ready on "
        f"{result['hostname']}")
  print(f"    takt        {result['takt_commit']}")
  print(f"    scheduler   {result['scheduler_url']}"
        f"{'' if result['authenticated'] else ' (no token)'}")
  print(f"    cores       {caps.get('cores')}")
  print(f"    free disk   {caps.get('free_disk_mb')} MB")
  print(f"    agent steps "
        f"{caps.get('capabilities', {}).get('agent_steps')}")
  updated = [r["repo"] for r in result["repos"]
             if r["updated"]]
  print(f"    repos       {len(result['repos'])} seeded"
        + (f", {len(updated)} updated" if updated else ""))
  windows = result.get("platform") == "windows"
  svc = result.get("service")
  if svc:
    print(f"    service     {svc['state']}")
    if windows:
      print(f"    task        {svc['task']} "
            f"(runs as {svc.get('runs_as')})")
      if svc.get("unattended"):
        print("    starts      at boot; runs with nobody "
              "logged in")
      else:
        print("    starts      at logon only -- a machine "
              "sitting at the login screen takes no work, "
              "because schtasks makes a passwordless "
              "named-user task 'Interactive only'")
    elif svc.get("linger"):
      print("    starts      at boot; runs with nobody "
            "logged in")
    else:
      print("    note: lingering is off, so the worker "
            "stops when the user logs out")
    print()
    print("  inspect it with:")
    if windows:
      print(f"    ssh {args.host} "
            f"'schtasks /query /tn {svc['task']} /fo list'")
    else:
      print(f"    ssh {args.host} "
            "'journalctl --user -u takt-worker -f'")
  else:
    print()
    print("  start it with:")
    print(f"    ssh {args.host} '{result['start_command']}'")
    kind = ("a scheduled task" if windows
            else "a systemd user service")
    print(f"  or re-run with --service to install it as "
          f"{kind}.")
  return 0


def _reclaim_module():
  """Load bin/reclaim.py."""
  return _load_bin_module("reclaim")


def main():
  """Parse args and dispatch."""
  parser = build_parser()
  args = parser.parse_args()

  if not args.group:
    parser.print_help()
    sys.exit(0)

  group = args.group

  # -- worker: a long-running daemon, not a command that
  # returns output, so it bypasses dispatch() entirely --
  if group == "worker":
    sys.exit(
      _worker_module().run_worker(args, parser=parser)
    )

  # -- reclaim: owns its own output and exit code --
  if group == "reclaim":
    sys.exit(
      _reclaim_module().run_reclaim(args, parser=parser)
    )

  # -- provision: reports progress as it goes, because a
  # repo push takes minutes and silence looks like a hang --
  if group == "provision":
    sys.exit(_run_provision(args))

  # Everything past here is a scheduler-side command.
  from lib.commands import dispatch

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
