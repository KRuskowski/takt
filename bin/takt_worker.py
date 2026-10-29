#!/usr/bin/env python3
"""takt-worker — execute pipeline runs on another machine.

Dials out to a takt scheduler, leases runs, executes their
steps locally, reports progress, and delivers any commits
produced as a git bundle. Outbound only, so a cloud VM
behind NAT works with no inbound access.

Usage:
  bin/takt_worker.py --scheduler https://takt:7433 \
      --id build-01 --once
  bin/takt_worker.py --scheduler https://takt:7433 \
      --id build-01            # loop until stopped

Configuration by environment:
  TAKT_API_URL      scheduler base URL
  TAKT_API_TOKEN    bearer token, if the scheduler needs one
  TAKT_WORKER_ID    worker name
  TAKT_WORKER_DIR   where mirrors and run trees live

Source for the worker's mirrors (--upstream):
  scheduler  fetch from the scheduler host over SSH. Keeps
             the scheduler the source of truth. LAN only.
  github     fetch from GitHub, for a cloud worker with no
             route back. Workers only ever FETCH; pushing
             upstream remains a scheduler-side step, so the
             operator still gates what reaches GitHub.
"""

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib.config import load_env_file
from lib.worker import (
  LeaseLost,
  SCHEDULER_ONLY_STEPS,
  Scheduler,
  WorkerError,
  bundle_for,
  can_run,
  claude_cli_argv,
  claude_env,
  cleanup,
  detect_capabilities,
  free_disk_mb,
  materialise,
  parse_cli_event,
  read_stage_result,
)

# Set from CLI args in main(); agent steps need them deep
# in the call chain and threading them through every frame
# only to reach one function is worse.
_CLAUDE_HOME = None
_AGENT_BARE = False

log = logging.getLogger("takt.worker")

# Stop taking work below this much free space. A worker
# that fills its disk mid-build fails the run it was given
# and every one after it, which is worse than a worker that
# quietly stops accepting.
DEFAULT_DISK_FLOOR_MB = 20 * 1024

_stop = False


def _handle_signal(signum, frame):
  """Finish the current run, then exit."""
  del signum, frame
  global _stop
  _stop = True
  log.info("Shutdown requested; will stop after this run")


def upstream_resolver(mode, scheduler_host, repos_config,
                      root_path="dev/root"):
  """Build a function mapping repo name -> fetch URL.

  `mode` is either one of the two conventional modes or a
  template containing {repo}. A template covers the cases
  the modes do not: a worker on the same filesystem, a
  mirror server, a layout other than ~/dev/root.

  Args:
    mode: 'scheduler', 'github', or a template string
      containing '{repo}'.
    scheduler_host: user@host for SSH, for 'scheduler'.
    repos_config: Parsed repos.yaml, for GitHub slugs.
    root_path: Path to the root repos on the scheduler,
      relative to the SSH login directory.

  Returns:
    Callable taking a repo name and returning a URL.

  Raises:
    SystemExit: If the mode cannot be satisfied.
  """
  if mode == "none":
    # A worker whose mirrors are seeded from outside has no
    # upstream it can reach. A Windows worker is always in
    # this state: git cannot be pushed to it through
    # cmd.exe, so provisioning sends bundles instead. Saying
    # so is better than pointing it at an unreachable host
    # and tolerating the failure once per repo per run.
    return lambda repo: None

  if mode not in ("scheduler", "github"):
    if "{repo}" not in mode:
      raise SystemExit(
        "--upstream must be 'scheduler', 'github', 'none', "
        "or a template containing {repo}"
      )
    return lambda repo: mode.format(repo=repo)

  if mode == "scheduler":
    if not scheduler_host:
      raise SystemExit(
        "--upstream scheduler needs --scheduler-host "
        "user@host (the machine holding the root repos)"
      )
    prefix = root_path.rstrip("/")
    return lambda repo: f"{scheduler_host}:{prefix}/{repo}"

  def resolve_github(repo):
    # Nested: load_repos_config() normalises a flat
    # repos.yaml by adding a "repos" key, so the top level
    # is not the registry.
    registry = repos_config.get("repos", repos_config)
    entry = registry.get(repo) or {}
    slug = entry.get("github")
    if not slug:
      raise WorkerError(
        f"repo {repo} has no github slug in repos.yaml, "
        f"so a cloud worker cannot fetch it"
      )
    return f"git@github.com:{slug}.git"

  return resolve_github


def execute_step(step, trees, scheduler, work):
  """Run one step locally and report the outcome.

  Args:
    step: Step dict from the work payload.
    trees: Mapping of repo -> worktree path.
    scheduler: Scheduler client.
    work: Work payload.

  Returns:
    True if the step passed.
  """
  run_id = work["run_id"]
  generation = work["generation"]
  step_id = step["step_id"]
  name = step["name"]

  if name in SCHEDULER_ONLY_STEPS:
    # Not a failure: the scheduler runs these itself once
    # the commits are delivered. Skipping keeps the
    # credentials and the GitHub gate on one machine.
    # pending -> queued -> skipped, because the step state
    # machine only allows skipping from queued.
    scheduler.report_step(
      run_id, generation, step_id, status="queued",
    )
    scheduler.report_step(
      run_id, generation, step_id, status="skipped",
      result={
        "status": "skipped",
        "summary": (
          f"{name} runs on the scheduler, not on a worker"
        ),
      },
    )
    return True

  scheduler.report_step(
    run_id, generation, step_id, status="queued",
  )
  scheduler.report_step(
    run_id, generation, step_id, status="running",
  )

  cwd = next(iter(trees.values()), None)
  if len(trees) == 1 and cwd is not None:
    work_dir = cwd
  else:
    work_dir = (
      Path(next(iter(trees.values()))).parent
      if trees else Path.cwd()
    )

  try:
    lines, ok, summary = _run_local_step(
      step, work_dir, work,
    )
  except Exception as e:  # noqa: BLE001 - reported, not raised
    scheduler.report_step(
      run_id, generation, step_id, status="failed",
      error=str(e)[:2000],
    )
    return False

  if lines:
    # Chunked so one enormous build log is not a single
    # request.
    for i in range(0, len(lines), 500):
      scheduler.report_step(
        run_id, generation, step_id,
        output=lines[i:i + 500],
      )
  scheduler.report_step(
    run_id, generation, step_id,
    status="completed" if ok else "failed",
    result={
      "status": "pass" if ok else "fail",
      "summary": summary,
    },
    error=None if ok else summary,
  )
  return ok


def _run_local_step(step, work_dir, work):
  """Execute a step's work on this machine.

  Script steps go through SCRIPT_REGISTRY — the same
  registry and the same (run, config) contract the
  scheduler uses — so a step behaves identically wherever
  it runs. The run dict is reconstructed from the lease
  payload because the worker has no database access.

  Agent steps shell out to the `claude` CLI with the
  prompt the scheduler rendered. A worker declaring
  agent_steps=False is never handed one.

  Args:
    step: Step dict from the lease payload.
    work_dir: Directory the run was materialised into.
    work: The whole lease payload.

  Returns:
    (output_lines, ok, summary).

  Raises:
    WorkerError: If the step type cannot be run here.
  """
  import json as _json
  from lib.pipeline import SCRIPT_REGISTRY
  name = step["name"]
  if step["step_type"] == "agent":
    return _run_agent_step(step, work_dir)
  if step["step_type"] != "script":
    raise WorkerError(
      f"step type {step['step_type']!r} is not supported "
      f"on a worker yet (step {name!r})"
    )
  func = SCRIPT_REGISTRY.get(name)
  if func is None:
    raise WorkerError(f"unknown script step: {name}")
  run_view = {
    "id": work["run_id"],
    "workspace": work["workspace"],
    "worktree_dir": str(work_dir),
    "repos_json": _json.dumps(work.get("repos") or []),
    "head_refs_json": _json.dumps(work.get("refs") or {}),
  }
  result = func(run_view, step.get("config") or {})
  # "pass" exactly, matching PipelineExecutor. Treating
  # anything else as success would turn a failed check into
  # a green run.
  ok = result.get("status") == "pass"
  summary = str(
    result.get("summary")
    or result.get("errors")
    or result.get("status", "")
  )
  return ([], ok, summary[:2000])


def _run_agent_step(step, work_dir):
  """Run an agent step by invoking the Claude CLI.

  The prompt is whatever the scheduler rendered — the
  worker does not build prompts, so the role templates
  stay authoritative in one place.

  Pass/fail follows the same rule as the local executor:
  the CLI completing is not success on its own; the step
  fails if .stage-result.json says so, and completes
  otherwise.

  Args:
    step: Step dict from the lease payload, with 'prompt'.
    work_dir: Worktree to run in.

  Returns:
    (output_lines, ok, summary).

  Raises:
    WorkerError: If the step cannot be run here.
  """
  import json as _json
  prompt = step.get("prompt") or ""
  if not prompt.strip():
    raise WorkerError(
      f"agent step {step['name']!r} arrived with no "
      f"prompt; refusing to run an agent with no "
      f"instructions"
    )
  argv = claude_cli_argv(
    prompt,
    model=step.get("model"),
    add_dirs=[work_dir],
    bare=_AGENT_BARE,
  )
  env = claude_env(claude_home=_CLAUDE_HOME)
  timeout = int(step.get("timeout_secs") or 1800)
  lines = []
  line_no = 0
  meta_result = {}
  proc = subprocess.Popen(
    argv, cwd=str(work_dir), env=env,
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True,
  )
  try:
    for raw in proc.stdout:
      parsed = parse_cli_event(raw)
      if parsed is None:
        continue
      kind, content, meta = parsed
      if kind == "result":
        meta_result = meta
      line_no += 1
      lines.append({
        "line_no": line_no, "kind": kind,
        "content": content, "meta": meta,
      })
    proc.wait(timeout=timeout)
  except subprocess.TimeoutExpired:
    proc.kill()
    raise WorkerError(
      f"agent step {step['name']!r} exceeded "
      f"{timeout}s"
    )
  finally:
    if proc.poll() is None:
      proc.kill()
  stderr = (proc.stderr.read() or "").strip()

  if proc.returncode != 0 or meta_result.get("is_error"):
    detail = stderr[:1000] or meta_result.get(
      "api_error_status"
    ) or "claude CLI reported an error"
    return (lines, False, f"agent failed: {detail}")

  # The agent's own verdict decides, matching the local
  # executor. A clean CLI exit with a 'fail' result is a
  # failed step, not a passed one.
  result = read_stage_result(work_dir)
  if result and result.get("status") == "fail":
    return (
      lines, False,
      str(result.get("summary", "agent reported fail")),
    )
  summary = (
    str(result.get("summary", "")) if result
    else _json.dumps(meta_result)[:500]
  )
  return (lines, True, summary[:2000])


def run_once(scheduler, caps, args, upstream_for):
  """Lease and execute at most one run.

  Args:
    scheduler: Scheduler client.
    caps: This worker's capabilities payload.
    args: Parsed CLI args.
    repos_config: Parsed repos.yaml.

  Returns:
    True if a run was executed.
  """
  work_dir = Path(args.work_dir)
  free = free_disk_mb(work_dir)
  if free is not None and free < args.disk_floor_mb:
    log.warning(
      "Only %d MB free at %s, below the %d MB floor; not "
      "taking work", free, work_dir, args.disk_floor_mb,
    )
    scheduler.heartbeat(free_disk_mb=free)
    return False

  allow_agents = caps["capabilities"]["agent_steps"]
  work = scheduler.lease(lease_secs=args.lease_secs)
  if not work:
    scheduler.heartbeat(free_disk_mb=free)
    return False

  run_id = work["run_id"]
  generation = work["generation"]
  log.info(
    "Leased run %s (%s) with %d steps",
    run_id, work["workspace"], len(work.get("steps", [])),
  )

  ok, reason = can_run(
    work, caps["capabilities"],
  )
  if not ok:
    log.info("Cannot run %s: %s", run_id, reason)
    scheduler.abandon(run_id, generation, reason=reason)
    return False
  del allow_agents

  trees = {}
  try:
    trees = materialise(
      work, args.mirror_dir, work_dir, upstream_for,
    )
    bases = {
      repo: _head(path) for repo, path in trees.items()
    }
    for step in sorted(
      work["steps"], key=lambda s: s["seq"]
    ):
      scheduler.renew(
        run_id, generation, lease_secs=args.lease_secs
      )
      if not execute_step(step, trees, scheduler, work):
        log.warning(
          "Step %s failed; stopping run %s",
          step["name"], run_id,
        )
        break
    _deliver(scheduler, work, trees, bases)
    scheduler.finish(run_id, generation)
    log.info("Finished run %s", run_id)
    return True
  except LeaseLost as e:
    # Someone else owns this run now. Discard rather than
    # report: our results are from a superseded attempt.
    log.warning(
      "Lease lost on run %s (%s); discarding results",
      run_id, e,
    )
    return False
  except Exception as e:  # noqa: BLE001
    log.exception("Run %s failed on this worker", run_id)
    try:
      scheduler.abandon(
        run_id, generation, reason=str(e)[:500]
      )
    except WorkerError:
      pass
    return False
  finally:
    if trees and not args.keep_worktrees:
      cleanup(trees, args.mirror_dir)


def _head(path):
  """Current commit in a worktree, or None."""
  from lib.worker import git
  proc = git(
    ["rev-parse", "HEAD"], cwd=path, check=False,
    timeout=120,
  )
  return proc.stdout.strip() if proc.returncode == 0 else None


def _deliver(scheduler, work, trees, bases):
  """Send back any commits the run produced."""
  for repo, path in (trees or {}).items():
    base = bases.get(repo)
    if not base:
      continue
    data = bundle_for(path, work["workspace"], base)
    if not data:
      continue
    log.info(
      "Delivering %d bytes for %s:%s",
      len(data), repo, work["workspace"],
    )
    result = scheduler.deliver(
      work["run_id"], work["generation"], repo,
      work["workspace"], data,
    )
    if result.get("parked"):
      log.warning(
        "Delivery parked on the scheduler: %s",
        result.get("note", ""),
      )


def add_worker_args(parser):
  """Attach the worker's flags to a parser.

  Shared by `takt worker` and this script, so the two
  entry points cannot drift apart.

  Args:
    parser: An ArgumentParser or subparser to populate.

  Returns:
    The same parser.
  """
  # Fall back to ~/.config/takt/env so a worker started by
  # hand or by systemd does not need the token in its shell.
  # Deliberately load_env_file() and not api_base_url():
  # that defaults to loopback, and a worker silently
  # dialling itself is worse than a usage error.
  env_file = load_env_file()
  parser.add_argument(
    "--scheduler",
    default=(
      os.environ.get("TAKT_API_URL")
      or env_file.get("TAKT_API_URL")
    ),
    help="Scheduler base URL, e.g. https://takt:7433.",
  )
  parser.add_argument(
    "--id", dest="worker_id",
    default=(
      os.environ.get("TAKT_WORKER_ID")
      or env_file.get("TAKT_WORKER_ID")
    ),
    help="Stable worker name, e.g. build-lan-01.",
  )
  parser.add_argument(
    "--token",
    default=(
      os.environ.get("TAKT_API_TOKEN")
      or env_file.get("TAKT_API_TOKEN")
    ),
    help="Bearer token, if the scheduler requires one.",
  )
  parser.add_argument(
    "--work-dir",
    default=os.environ.get(
      "TAKT_WORKER_DIR", str(Path.home() / "takt-worker")
    ),
    help="Where run worktrees are created.",
  )
  parser.add_argument(
    "--mirror-dir", default=None,
    help="Where repo mirrors live. Defaults to "
         "<work-dir>/mirrors.",
  )
  parser.add_argument(
    "--upstream", default="scheduler",
    help="Where mirrors fetch from: 'scheduler' over SSH "
         "for a LAN worker, 'github' for a cloud worker "
         "with no route back, or a template containing "
         "{repo} for anything else "
         "(e.g. /srv/mirrors/{repo}.git).",
  )
  parser.add_argument(
    "--scheduler-host", default=None,
    help="user@host holding the root repos, for "
         "--upstream scheduler.",
  )
  parser.add_argument(
    "--root-path", default="dev/root",
    help="Path to the root repos on the scheduler, "
         "relative to the SSH login directory.",
  )
  parser.add_argument(
    "--lease-secs", type=int, default=900,
    help="Lease length, renewed before every step.",
  )
  parser.add_argument(
    "--interval", type=int, default=15,
    help="Seconds to wait when the queue is empty.",
  )
  parser.add_argument(
    "--disk-floor-mb", type=int,
    default=DEFAULT_DISK_FLOOR_MB,
    help="Stop taking work below this much free space.",
  )
  parser.add_argument(
    "--once", action="store_true",
    help="Execute at most one run, then exit.",
  )
  parser.add_argument(
    "--keep-worktrees", action="store_true",
    help="Do not clean up after a run (for debugging; "
         "build output is the dominant disk cost).",
  )
  parser.add_argument(
    "--no-agent-steps", action="store_true",
    help="Declare this a build-only worker even if Claude "
         "Code is installed.",
  )
  parser.add_argument(
    "--claude-home", default=os.environ.get("CLAUDE_HOME"),
    help="CLAUDE_CONFIG_DIR for agent steps, selecting "
         "which account the CLI authenticates as. Limits "
         "attach to the account, so give each worker its "
         "own to actually gain agent throughput.",
  )
  parser.add_argument(
    "--agent-bare", action="store_true",
    help="Run agent steps with --bare (no hooks, plugins "
         "or MCP servers). Off by default, matching the "
         "scheduler's in-process agents.",
  )
  return parser


def run_worker(args, parser=None):
  """Register with the scheduler and execute runs.

  Args:
    args: Parsed arguments from add_worker_args().
    parser: Parser to raise usage errors through. Falls
      back to SystemExit when absent.

  Returns:
    Process exit code.
  """
  def fail(message):
    if parser is not None:
      parser.error(message)
    raise SystemExit(f"takt worker: error: {message}")

  global _CLAUDE_HOME, _AGENT_BARE
  _CLAUDE_HOME = args.claude_home
  _AGENT_BARE = args.agent_bare

  logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
  )

  if not args.scheduler:
    fail("--scheduler is required (or set TAKT_API_URL)")
  if not args.worker_id:
    fail("--id is required (or set TAKT_WORKER_ID)")
  if args.mirror_dir is None:
    args.mirror_dir = str(Path(args.work_dir) / "mirrors")
  Path(args.work_dir).mkdir(parents=True, exist_ok=True)
  Path(args.mirror_dir).mkdir(parents=True, exist_ok=True)

  from lib.config import load_repos_config
  try:
    repos_config = load_repos_config()
  except OSError:
    # A worker does not need repos.yaml unless it fetches
    # from GitHub, where the slug comes from it.
    repos_config = {}

  # Resolved before registering, let alone leasing. This
  # used to happen inside run_once() after the lease, so a
  # worker with a bad --upstream took a run and then exited
  # on a usage error, leaving the run claimed by a process
  # that no longer existed until the lease swept. Observed
  # on win-desk: "Leased run 10 ... with 1 steps" followed
  # by "--upstream scheduler needs --scheduler-host".
  upstream_for = upstream_resolver(
    args.upstream, args.scheduler_host, repos_config,
    args.root_path,
  )

  caps = detect_capabilities(args.work_dir)
  if args.no_agent_steps:
    caps["capabilities"]["agent_steps"] = False
  scheduler = Scheduler(
    args.scheduler, args.worker_id, token=args.token,
  )
  try:
    scheduler.register(caps)
  except WorkerError as e:
    log.error("Could not register: %s", e)
    return 1
  log.info(
    "Registered %s (%s, %s cores, agent_steps=%s, "
    "claude_home=%s)",
    args.worker_id, caps["os"], caps["cores"],
    caps["capabilities"]["agent_steps"],
    _CLAUDE_HOME or "default",
  )

  signal.signal(signal.SIGINT, _handle_signal)
  signal.signal(signal.SIGTERM, _handle_signal)

  while True:
    try:
      did_work = run_once(
        scheduler, caps, args, upstream_for,
      )
    except WorkerError as e:
      log.error("Scheduler problem: %s", e)
      did_work = False
    if args.once or _stop:
      return 0
    if not did_work:
      time.sleep(max(1, args.interval))


def main():
  """Standalone entry point: `bin/takt_worker.py`."""
  parser = argparse.ArgumentParser(
    prog="takt_worker.py",
    description="Execute takt pipeline runs on this machine.",
  )
  add_worker_args(parser)
  return run_worker(parser.parse_args(), parser=parser)


if __name__ == "__main__":
  sys.exit(main())
