"""Worker-side logic for executing runs on another machine.

A worker dials out to the scheduler, leases a run, makes
the exact commits present locally, executes the steps, and
reports back over HTTP. It never touches the scheduler's
database or root repos — everything goes through the worker
endpoints.

Why its own mirrors: a run worktree is created with
`git worktree add` from a root repo, and a worktree's .git
is a file pointing into that repo's git directory. A
worktree therefore cannot live on a different machine from
the repo it came from, so each worker keeps its own clone.

Where those mirrors fetch from is per-worker config,
because the answer differs by location. A LAN worker pulls
from the scheduler over SSH, which keeps the scheduler the
source of truth. A cloud worker has no route back and
pulls from GitHub. Fetching from GitHub does not weaken the
"operator gates GitHub" rule: workers only ever fetch, and
pushing upstream stays a scheduler-side step.
"""

import json
import logging
import os
import platform
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("takt.worker")

# Steps a worker must never run, whatever a pipeline says.
# Delivering upstream is the scheduler's job: it holds the
# credentials and it is the single gate on what reaches
# GitHub.
SCHEDULER_ONLY_STEPS = frozenset({
  "push_to_github",
  "create_pr",
})


class WorkerError(Exception):
  """A worker-side failure."""


class SchedulerError(WorkerError):
  """The scheduler rejected a call or is unreachable."""


class LeaseLost(WorkerError):
  """The lease was reclaimed; stop and discard results."""


class Scheduler:
  """HTTP client for the worker endpoints.

  Attributes:
    base: Scheduler base URL.
    worker_id: This worker's id.
  """

  def __init__(self, base, worker_id, token=None,
               timeout=60):
    """Initialise the client.

    Args:
      base: Scheduler base URL, e.g. https://takt:7433.
      worker_id: Stable worker name.
      token: Bearer token, if the scheduler requires one.
      timeout: Per-request timeout in seconds.
    """
    self.base = base.rstrip("/")
    self.worker_id = worker_id
    self._token = token
    self._timeout = timeout

  def _call(self, method, path, body=None, raw=None,
            timeout=None):
    """Make one request and return the data payload.

    Args:
      method: HTTP method.
      path: Path beginning with /api.
      body: Dict to send as JSON.
      raw: Raw bytes to send instead of JSON.
      timeout: Override the default timeout.

    Returns:
      The response's data field.

    Raises:
      LeaseLost: On 409, which always means the lease is
        no longer ours.
      SchedulerError: On any other failure.
    """
    url = f"{self.base}{path}"
    headers = {}
    data = None
    if raw is not None:
      data = raw
      headers["Content-Type"] = "application/octet-stream"
    elif body is not None:
      data = json.dumps(body).encode()
      headers["Content-Type"] = "application/json"
    if self._token:
      headers["Authorization"] = f"Bearer {self._token}"
    req = urllib.request.Request(
      url, data=data, method=method, headers=headers,
    )
    try:
      with urllib.request.urlopen(
        req, timeout=timeout or self._timeout
      ) as resp:
        payload = json.loads(resp.read() or b"{}")
        return payload.get("data", payload)
    except urllib.error.HTTPError as e:
      text = e.read().decode(errors="replace")
      try:
        message = json.loads(text).get("message", text)
      except ValueError:
        message = text
      if e.code == 409:
        raise LeaseLost(message) from e
      raise SchedulerError(
        f"{method} {path} -> {e.code}: {message}"
      ) from e
    except (urllib.error.URLError, OSError) as e:
      raise SchedulerError(
        f"{method} {path} -> unreachable: {e}"
      ) from e

  def register(self, caps):
    """Announce this worker and its capabilities."""
    return self._call(
      "POST", "/api/workers/register",
      {"worker_id": self.worker_id, **caps},
    )

  def heartbeat(self, free_disk_mb=None):
    """Report liveness and free space."""
    return self._call(
      "POST",
      f"/api/workers/{self.worker_id}/heartbeat",
      {"free_disk_mb": free_disk_mb},
    )

  def lease(self, lease_secs=900):
    """Ask for work. Returns the payload or None."""
    data = self._call(
      "POST", f"/api/workers/{self.worker_id}/lease",
      {"lease_secs": lease_secs},
    )
    return (data or {}).get("work")

  def renew(self, run_id, generation, lease_secs=900):
    """Extend the lease on a run."""
    return self._call(
      "POST", f"/api/workers/{self.worker_id}/renew",
      {"run_id": run_id, "generation": generation,
       "lease_secs": lease_secs},
    )

  def report_step(self, run_id, generation, step_id,
                  **fields):
    """Report step status and/or output."""
    return self._call(
      "POST", f"/api/workers/{self.worker_id}/step",
      {"run_id": run_id, "generation": generation,
       "step_id": step_id, **fields},
    )

  def deliver(self, run_id, generation, repo, branch,
              bundle_bytes):
    """Upload a git bundle of produced commits."""
    query = (
      f"?run_id={run_id}&generation={generation}"
      f"&repo={urllib.parse.quote(repo)}"
      f"&branch={urllib.parse.quote(branch)}"
    )
    return self._call(
      "POST",
      f"/api/workers/{self.worker_id}/bundle{query}",
      raw=bundle_bytes, timeout=1800,
    )

  def finish(self, run_id, generation):
    """Mark the run complete."""
    return self._call(
      "POST", f"/api/workers/{self.worker_id}/finish",
      {"run_id": run_id, "generation": generation},
    )

  def abandon(self, run_id, generation, reason=""):
    """Hand the run back to the queue."""
    return self._call(
      "POST", f"/api/workers/{self.worker_id}/abandon",
      {"run_id": run_id, "generation": generation,
       "reason": reason},
    )


def free_disk_mb(path):
  """Free space at a path, in MB.

  Args:
    path: Any path on the filesystem of interest.

  Returns:
    Free megabytes, or None if it cannot be determined.
  """
  try:
    usage = shutil.disk_usage(str(path))
  except OSError:
    return None
  return usage.free // (1024 * 1024)


def detect_capabilities(work_dir, claude_available=None):
  """Describe what this machine can do.

  Args:
    work_dir: Directory the worker will build in.
    claude_available: Override the Claude Code check.

  Returns:
    Dict suitable for the register call.
  """
  if claude_available is None:
    claude_available = shutil.which("claude") is not None
  toolchains = {
    name: shutil.which(name) is not None
    for name in (
      "cmake", "ninja", "gcc", "clang", "cargo", "node",
      "dotnet", "python3",
    )
  }
  return {
    "host": platform.node(),
    "os": _os_name(),
    "cores": os.cpu_count(),
    "ram_mb": _ram_mb(),
    "free_disk_mb": free_disk_mb(work_dir),
    "capabilities": {
      "agent_steps": bool(claude_available),
      "toolchains": toolchains,
    },
  }


def _os_name():
  """Short OS label matching the targets.yaml convention."""
  system = platform.system().lower()
  if system == "linux":
    try:
      for line in Path("/etc/os-release").read_text(
      ).splitlines():
        if line.startswith("ID="):
          return line.split("=", 1)[1].strip().strip('"')
    except OSError:
      pass
    return "linux"
  if system == "windows":
    return "windows"
  return system


def _ram_mb():
  """Total RAM in MB, or None if it cannot be read."""
  try:
    pages = os.sysconf("SC_PHYS_PAGES")
    size = os.sysconf("SC_PAGE_SIZE")
    return (pages * size) // (1024 * 1024)
  except (ValueError, OSError, AttributeError):
    return None


def can_run(work, capabilities):
  """Decide whether this worker can execute a run.

  Args:
    work: Work payload from the scheduler.
    capabilities: This worker's capabilities dict.

  Returns:
    (ok, reason) — reason is empty when ok.
  """
  needs_agent = any(
    s["step_type"] == "agent" for s in work.get("steps", [])
  )
  if needs_agent and not capabilities.get("agent_steps"):
    return (
      False,
      "run contains an agent step and this worker has no "
      "authenticated Claude Code",
    )
  unknown = [
    s["name"] for s in work.get("steps", [])
    if s["step_type"] not in ("agent", "script")
  ]
  if unknown:
    return (
      False,
      f"unsupported step types: {', '.join(unknown)}",
    )
  return (True, "")


def git(args, cwd=None, timeout=1800, check=True):
  """Run a git command and return its CompletedProcess.

  Args:
    args: Arguments after 'git'.
    cwd: Working directory.
    timeout: Seconds before giving up.
    check: Raise WorkerError on a non-zero exit.

  Returns:
    subprocess.CompletedProcess.

  Raises:
    WorkerError: If check and the command failed.
  """
  proc = subprocess.run(
    ["git"] + list(args),
    cwd=str(cwd) if cwd else None,
    capture_output=True, text=True, timeout=timeout,
  )
  if check and proc.returncode != 0:
    raise WorkerError(
      f"git {' '.join(args)} failed: "
      f"{proc.stderr.strip()[:400]}"
    )
  return proc


def ensure_mirror(repo, mirror_dir, upstream):
  """Make sure a local mirror of a repo exists.

  Mirrors are kept rather than re-cloned: they are cheap to
  hold and expensive to refetch, especially for a repo of a
  few gigabytes over a cloud link.

  Args:
    repo: Repo name.
    mirror_dir: Directory holding this worker's mirrors.
    upstream: URL or path to clone/fetch from.

  Returns:
    Path to the mirror.
  """
  mirror = Path(mirror_dir) / f"{repo}.git"
  if not mirror.exists():
    mirror.parent.mkdir(parents=True, exist_ok=True)
    log.info("Cloning mirror %s from %s", repo, upstream)
    git(["clone", "--mirror", upstream, str(mirror)],
        timeout=7200)
    return mirror
  git(["remote", "set-url", "origin", upstream],
      cwd=mirror, check=False)
  git(["fetch", "--prune", "origin",
       "+refs/heads/*:refs/heads/*"],
      cwd=mirror, timeout=7200)
  return mirror


def have_commit(mirror, sha):
  """Check a mirror already contains a commit.

  Args:
    mirror: Path to the mirror.
    sha: Commit to look for.

  Returns:
    True if the object is present and is a commit.
  """
  proc = git(
    ["cat-file", "-e", f"{sha}^{{commit}}"],
    cwd=mirror, check=False, timeout=120,
  )
  return proc.returncode == 0


def materialise(work, mirror_dir, run_root, upstream_for):
  """Create worktrees for a run at its exact commits.

  Args:
    work: Work payload from the scheduler.
    mirror_dir: Where this worker keeps mirrors.
    run_root: Directory to create the run tree under.
    upstream_for: Callable repo -> clone/fetch URL.

  Returns:
    Dict of repo name -> worktree path.

  Raises:
    WorkerError: If a required commit cannot be obtained.
  """
  refs = work.get("refs") or {}
  run_dir = Path(run_root) / (
    f"{work['workspace']}-{work['run_id']}"
  )
  run_dir.mkdir(parents=True, exist_ok=True)
  trees = {}
  for repo in work.get("repos") or []:
    sha = _ref_for(refs, repo, work["workspace"])
    mirror = ensure_mirror(
      repo, mirror_dir, upstream_for(repo)
    )
    if sha and not have_commit(mirror, sha):
      # One more fetch in case the scheduler is ahead of
      # the last sync, then give up rather than silently
      # building the wrong commit.
      git(["fetch", "--prune", "origin",
           "+refs/heads/*:refs/heads/*"],
          cwd=mirror, timeout=7200)
      if not have_commit(mirror, sha):
        raise WorkerError(
          f"commit {sha} for {repo} is not reachable from "
          f"this worker's mirror"
        )
    target = run_dir / repo
    if target.exists():
      shutil.rmtree(target, ignore_errors=True)
    git(["worktree", "add", "--detach", str(target),
         sha or "HEAD"],
        cwd=mirror, timeout=3600)
    trees[repo] = target
  return trees


def _ref_for(refs, repo, workspace):
  """Find the commit for a repo in a refs mapping.

  refs is keyed "<repo>:<branch>" as produced by
  snapshot_all_refs. Prefer the workspace branch, since the
  workspace name is the branch name, and fall back to any
  entry for the repo.

  Args:
    refs: Mapping of "repo:branch" to SHA.
    repo: Repo name.
    workspace: Workspace (= branch) name.

  Returns:
    Commit SHA, or None if the repo is not listed.
  """
  exact = refs.get(f"{repo}:{workspace}")
  if exact:
    return exact
  for key, sha in refs.items():
    if key.split(":", 1)[0] == repo:
      return sha
  return None


def cleanup(trees, mirror_dir):
  """Remove worktrees and their build output.

  Called when a run finishes, which is the only moment this
  is certainly safe. An unattended worker has nobody to
  notice it filling up, and build output is the dominant
  storage cost, so this is not optional.

  Args:
    trees: Mapping of repo -> worktree path.
    mirror_dir: Where mirrors live, so they can be pruned.
  """
  for repo, path in (trees or {}).items():
    mirror = Path(mirror_dir) / f"{repo}.git"
    git(["worktree", "remove", "--force", str(path)],
        cwd=mirror, check=False, timeout=600)
    if Path(path).exists():
      shutil.rmtree(path, ignore_errors=True)
    git(["worktree", "prune"], cwd=mirror, check=False,
        timeout=300)


def bundle_for(tree, branch, base_sha):
  """Create a bundle of commits produced in a worktree.

  Bundles only the range this run added, never the whole
  history, so delivering from a multi-gigabyte repo does
  not ship the repo.

  Args:
    tree: Worktree path.
    branch: Branch name to record in the bundle.
    base_sha: Commit the run started from.

  Returns:
    Bundle bytes, or None if nothing new was committed.
  """
  head = git(["rev-parse", "HEAD"], cwd=tree).stdout.strip()
  if not head or head == base_sha:
    return None
  # Name the branch in the bundle so the scheduler can
  # fetch "<branch>" from it.
  git(["branch", "-f", branch, "HEAD"], cwd=tree,
      check=False)
  out = Path(tree) / ".takt-delivery.bundle"
  if out.exists():
    out.unlink()
  git(["bundle", "create", str(out),
       f"{base_sha}..{branch}", branch],
      cwd=tree, timeout=3600)
  data = out.read_bytes()
  out.unlink()
  return data
