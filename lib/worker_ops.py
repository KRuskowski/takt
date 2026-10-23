"""Scheduler-side operations for remote workers.

The scheduler owns the database, the queue and the root
repos. A worker never touches any of them directly — it
asks for work, reports progress, and delivers the commits
it produced. Everything it is allowed to do goes through
this module, which is where the trust boundary lives.

Every call that changes state checks the caller still holds
a valid lease on the run, at the generation it was given. A
worker whose lease expired and was reclaimed elsewhere is
refused, so a machine that went dark for an hour cannot
come back and report over the top of whoever took over.
"""

import json
import logging
import re
import subprocess
import tempfile
from pathlib import Path

from lib import db
from lib.config import get_repo_path, load_repos_config

log = logging.getLogger("takt.worker_ops")

# A branch name we are willing to pass to git. Rejects
# anything that could be read as an option or a refspec
# trick, rather than trying to sanitise it.
_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,200}$")

# Largest bundle we will accept, to bound memory and disk
# from a single request.
MAX_BUNDLE_BYTES = 2 * 1024 * 1024 * 1024


class LeaseError(Exception):
  """The caller does not hold a valid lease on the run."""


class DeliveryError(Exception):
  """A bundle could not be accepted."""


def lease(worker_id, lease_secs=900, db_path=None):
  """Claim a run for a worker and describe the work.

  The payload is everything the worker needs to reproduce
  the run locally: the repos involved, the exact commit per
  repo, and the ordered steps with their config. Exact SHAs
  rather than branch names, so a requeued run materialises
  the same source as the original attempt.

  Args:
    worker_id: Worker asking for work.
    lease_secs: How long the claim is valid.
    db_path: Override path for testing.

  Returns:
    Work payload dict, or None if the queue is empty.
  """
  run = db.lease_run(
    worker_id, lease_secs=lease_secs, db_path=db_path
  )
  if run is None:
    return None
  steps = db.get_run_steps(run["id"], db_path=db_path)
  repos = _loads(run["repos_json"], [])
  out_steps = []
  for s in steps:
    config = _loads(s["config_json"], {})
    step = {
      "step_id": s["id"],
      "seq": s["seq"],
      "name": s["name"],
      "step_type": s["step_type"],
      "status": s["status"],
      "timeout_secs": s["timeout_secs"],
      "config": config,
    }
    if s["step_type"] == "agent":
      # Rendered here, not on the worker: the role
      # templates on the scheduler are authoritative, and
      # rendering reads commit logs from the root repos,
      # which a worker cannot see.
      step["prompt"] = _render_prompt(
        s["name"], run["workspace"], repos, config, run
      )
      step["model"] = config.get("model", "sonnet")
    out_steps.append(step)
  return {
    "run_id": run["id"],
    "workspace": run["workspace"],
    "generation": run["lease_generation"],
    "lease_expires_at": run["lease_expires_at"],
    "trigger": run["trigger"],
    "repos": repos,
    "refs": _loads(run["head_refs_json"], {}),
    "steps": out_steps,
  }


def _render_prompt(role, workspace, repos, config, run):
  """Render an agent prompt, never failing the lease.

  A prompt that cannot be built is reported to the worker
  as an empty prompt, which it refuses rather than running
  an agent with no instructions. Raising here would fail
  the whole lease call instead.

  Args:
    role: Step name / role slug.
    workspace: Workspace name.
    repos: Repo names in the run.
    config: Step config.
    run: Run row.

  Returns:
    Prompt string, or '' if rendering failed.
  """
  from lib.pipeline import build_agent_prompt
  try:
    return build_agent_prompt(
      role, workspace, repos, config, run
    )
  except Exception:
    log.exception(
      "Failed to render prompt for role %s on run %s",
      role, run.get("id"),
    )
    return ""


def _loads(raw, default):
  """Parse JSON from a column, falling back on garbage."""
  try:
    return json.loads(raw) if raw else default
  except ValueError:
    return default


def check_lease(run_id, worker_id, generation,
                db_path=None):
  """Confirm a worker still holds the run it claims to.

  Args:
    run_id: Run in question.
    worker_id: Worker making the claim.
    generation: Generation the worker was given.
    db_path: Override path for testing.

  Raises:
    LeaseError: If the run is unknown, held by someone
      else, or the generation has moved on.
  """
  run = db.get_run(run_id, db_path=db_path)
  if run is None:
    raise LeaseError(f"run {run_id} does not exist")
  if run.get("worker_id") != worker_id:
    raise LeaseError(
      f"run {run_id} is not held by {worker_id} "
      f"(held by {run.get('worker_id')!r})"
    )
  if int(run.get("lease_generation") or 0) != int(
    generation
  ):
    raise LeaseError(
      f"stale lease on run {run_id}: worker has generation "
      f"{generation}, current is "
      f"{run.get('lease_generation')}"
    )


def report_step(worker_id, run_id, generation, step_id,
                status=None, output=None, result=None,
                error=None, cost_usd=None, num_turns=None,
                db_path=None):
  """Record a worker's progress on one step.

  Output and status go through the same tables the local
  executor writes, so a remote run is readable by every
  existing tool without special cases.

  Args:
    worker_id: Reporting worker.
    run_id: Run the step belongs to.
    generation: Lease generation held by the worker.
    step_id: Step being reported.
    status: New step status, if it changed.
    output: List of {line_no, kind, content, meta} dicts.
    result: Result payload to store as result_json.
    error: Error text, when failing.
    cost_usd: Agent cost, if known.
    num_turns: Agent turn count, if known.
    db_path: Override path for testing.

  Returns:
    Dict with what was applied.

  Raises:
    LeaseError: If the lease is not valid.
    ValueError: If the step is not part of the run.
  """
  check_lease(run_id, worker_id, generation,
              db_path=db_path)
  step = db.get_step(step_id, db_path=db_path)
  if step is None or step["run_id"] != run_id:
    raise ValueError(
      f"step {step_id} is not part of run {run_id}"
    )
  applied = {"step_id": step_id}
  if output:
    db.record_output(step_id, output, db_path=db_path)
    applied["output_lines"] = len(output)
  if status:
    db.advance_step(
      step_id, status,
      result_json=(
        json.dumps(result) if result is not None else None
      ),
      error=error, cost_usd=cost_usd,
      num_turns=num_turns, db_path=db_path,
    )
    applied["status"] = status
    # Recompute the run as each step moves, the way the
    # local executor does. Two reasons: an operator sees a
    # remote run as running rather than queued while it
    # works, and the run reaches 'running' before its
    # steps finish — without which a run whose steps all
    # skip would be stuck, since RUN_TRANSITIONS has no
    # queued -> passed edge.
    applied["run_status"] = db.advance_run(
      run_id, db_path=db_path
    )
  return applied


def finish_run(worker_id, run_id, generation,
               db_path=None):
  """Advance the run and drop the worker's claim.

  Args:
    worker_id: Worker that executed the run.
    run_id: Run to advance.
    generation: Lease generation held by the worker.
    db_path: Override path for testing.

  Returns:
    The run's resulting status.

  Raises:
    LeaseError: If the lease is not valid.
  """
  check_lease(run_id, worker_id, generation,
              db_path=db_path)
  status = db.advance_run(run_id, db_path=db_path)
  db.release_run(
    run_id, worker_id=worker_id, generation=generation,
    db_path=db_path,
  )
  return status


def abandon_run(worker_id, run_id, generation, reason="",
                db_path=None):
  """Give a run back without completing it.

  The run returns to the queue for another worker. Used
  when a worker is shutting down, is out of disk, or hit
  something it cannot proceed through.

  Args:
    worker_id: Worker giving up.
    run_id: Run to release.
    generation: Lease generation held by the worker.
    reason: Why, for the event log.
    db_path: Override path for testing.

  Returns:
    True if the claim was dropped.

  Raises:
    LeaseError: If the lease is not valid.
  """
  check_lease(run_id, worker_id, generation,
              db_path=db_path)
  db.log_event(
    "run", run_id, None, "abandoned",
    reason=reason or f"released by {worker_id}",
    db_path=db_path,
  )
  return db.release_run(
    run_id, worker_id=worker_id, generation=generation,
    db_path=db_path,
  )


def accept_bundle(worker_id, run_id, generation, repo,
                  branch, data, db_path=None):
  """Take delivery of commits a worker produced.

  A worker has no route to the scheduler's git remotes, and
  a cloud worker cannot be reached at all, so commits come
  back as a git bundle over the connection the worker
  already has. The bundle is verified before anything is
  fetched from it, and the fetch is a plain fast-forward:
  history on the scheduler is never rewritten to
  accommodate a worker.

  Args:
    worker_id: Delivering worker.
    run_id: Run that produced the commits.
    generation: Lease generation held by the worker.
    repo: Repo name, which must be a configured repo.
    branch: Branch to update.
    data: Bundle bytes.
    db_path: Override path for testing.

  Returns:
    Dict describing what was fetched.

  Raises:
    LeaseError: If the lease is not valid.
    DeliveryError: If the repo, branch or bundle is not
      acceptable.
  """
  check_lease(run_id, worker_id, generation,
              db_path=db_path)
  if not data:
    raise DeliveryError("empty bundle")
  if len(data) > MAX_BUNDLE_BYTES:
    raise DeliveryError(
      f"bundle is {len(data)} bytes, over the "
      f"{MAX_BUNDLE_BYTES} limit"
    )
  # Only repos takt already manages, so a worker cannot
  # name a path and have it treated as a repo.
  if repo not in load_repos_config():
    raise DeliveryError(f"unknown repo: {repo!r}")
  if not _SAFE_REF.match(branch or ""):
    raise DeliveryError(f"unsafe branch name: {branch!r}")
  repo_path = get_repo_path(repo)
  if not repo_path.exists():
    raise DeliveryError(
      f"root repo not found: {repo_path}"
    )

  with tempfile.TemporaryDirectory(
    prefix="takt-bundle-"
  ) as tmp:
    bundle = Path(tmp) / "delivery.bundle"
    bundle.write_bytes(data)
    # Verify inside the destination repo. A bundle of just
    # the new commits names its base as a prerequisite, and
    # only the target repo can confirm it has that base —
    # which is exactly the check worth making. Without -C,
    # git verifies against whatever repo the process
    # happens to be sitting in.
    verify = subprocess.run(
      ["git", "-C", str(repo_path), "bundle", "verify",
       str(bundle)],
      capture_output=True, text=True, timeout=600,
    )
    if verify.returncode != 0:
      raise DeliveryError(
        f"bundle failed verification: "
        f"{verify.stderr.strip()[:400]}"
      )
    # No --force: a worker delivering work on its own
    # branch is a fast-forward. Anything else is a conflict
    # the operator should see, not something to overwrite.
    target = f"refs/heads/{branch}"
    fetch = subprocess.run(
      ["git", "-C", str(repo_path), "fetch",
       str(bundle), f"{branch}:{target}"],
      capture_output=True, text=True, timeout=1800,
    )
    parked = False
    if fetch.returncode != 0 and _is_checked_out_error(
      fetch.stderr
    ):
      # git refuses to move a branch that is checked out,
      # and rightly so — it would desync the working tree
      # and index. Root repos are not consistently on a
      # detached HEAD (OTC.Relay sits on master,
      # PIXConnect on main), so this is reachable in
      # normal use. Park the commits on a side ref instead
      # of failing: the objects arrive either way, and
      # nothing a worker spent an hour producing is lost
      # because of how a root repo happens to be checked
      # out.
      target = f"refs/takt/incoming/{branch}"
      fetch = subprocess.run(
        ["git", "-C", str(repo_path), "fetch",
         str(bundle), f"{branch}:{target}"],
        capture_output=True, text=True, timeout=1800,
      )
      parked = True
    if fetch.returncode != 0:
      raise DeliveryError(
        f"fetch from bundle failed: "
        f"{fetch.stderr.strip()[:400]}"
      )
  head = subprocess.run(
    ["git", "-C", str(repo_path), "rev-parse", target],
    capture_output=True, text=True, timeout=60,
  )
  sha = (
    head.stdout.strip() if head.returncode == 0 else None
  )
  log.info(
    "Accepted bundle from %s for %s:%s -> %s%s",
    worker_id, repo, branch, sha,
    " (parked: branch is checked out)" if parked else "",
  )
  db.log_event(
    "run", run_id, None, "delivered",
    reason=(
      f"{repo}:{branch} -> {sha}"
      + (f" parked at {target}" if parked else "")
    ),
    db_path=db_path,
  )
  result = {
    "repo": repo, "branch": branch, "head": sha,
    "ref": target, "parked": parked,
    "bytes": len(data),
  }
  if parked:
    result["note"] = (
      f"{branch} is checked out in the root repo, so the "
      f"commits were parked at {target} rather than "
      f"moving a branch out from under a working tree. "
      f"Fast-forward it with: git -C {repo_path} update-ref "
      f"refs/heads/{branch} {sha} (after checking the "
      f"working tree is clean), or leave the root repo on a "
      f"detached HEAD so deliveries land directly."
    )
  return result


def _is_checked_out_error(stderr):
  """Detect git refusing to update a checked-out branch.

  Args:
    stderr: git's error output.

  Returns:
    True if the failure was the checked-out-branch refusal.
  """
  text = (stderr or "").lower()
  return (
    "refusing to fetch into branch" in text
    or "checked out at" in text
  )
