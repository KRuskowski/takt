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


def claude_cli_argv(prompt, model=None, add_dirs=(),
                    bare=False):
  """Build the argv for a non-interactive Claude CLI run.

  Agent steps run the `claude` CLI as a subprocess rather
  than embedding the SDK: the worker then needs only the
  CLI installed, and the scheduler's rendered prompt goes
  in unchanged.

  --verbose is not optional: the CLI refuses
  `--output-format stream-json` under `--print` without it.

  --permission-mode is passed explicitly because an
  interactive shell may alias `claude` to add it, and a
  subprocess does not inherit shell aliases.

  Args:
    prompt: Fully rendered prompt from the scheduler.
    model: Model alias or full name, e.g. 'opus'.
    add_dirs: Extra directories the agent may read.
    bare: Skip hooks, plugins and MCP servers.

  Returns:
    List of argv strings.
  """
  argv = [
    "claude", "-p", prompt,
    "--output-format", "stream-json",
    "--verbose",
    "--permission-mode", "bypassPermissions",
  ]
  if model:
    argv += ["--model", model]
  if bare:
    argv.append("--bare")
  for directory in add_dirs:
    argv += ["--add-dir", str(directory)]
  return argv


# Credentials the CLI will accept from its environment, and
# which a worker may therefore be given. An allowlist, not a
# passthrough of the whole takt env file: that file also
# holds TAKT_API_TOKEN, and handing the scheduler's API
# token to every agent subprocess would let an agent create
# workspaces and trigger runs.
#
# CLAUDE_CODE_OAUTH_TOKEN is the one worth distributing. The
# interactive login writes ~/.claude/.credentials.json,
# whose access token lasts about an hour and whose refresh
# token rotates -- so copying that file to several machines
# has them refresh against the same token and invalidate
# each other. `claude setup-token` mints a long-lived token
# for exactly this, and it is consumed through this variable.
CLAUDE_AUTH_VARS = (
  "CLAUDE_CODE_OAUTH_TOKEN",
  "ANTHROPIC_API_KEY",
  "ANTHROPIC_AUTH_TOKEN",
)


def claude_env(claude_home=None, base_env=None,
               env_file=None):
  """Build the environment for a Claude CLI subprocess.

  CLAUDE_CONFIG_DIR selects which account the CLI
  authenticates as. Rate and usage limits attach to the
  account, so giving each worker its own is what actually
  multiplies agent throughput — more machines sharing one
  account do not.

  Credentials in CLAUDE_AUTH_VARS are filled in from takt's
  credential file when absent from the environment. systemd
  loads that file for a Linux worker, but a Windows
  scheduled task does not, and the token must not go on the
  task's command line where a process listing would show
  it. Reading it here makes both platforms behave the same.

  Args:
    claude_home: Directory to use as CLAUDE_CONFIG_DIR.
    base_env: Environment to copy. Defaults to os.environ.
    env_file: Credential file to read. Defaults to takt's.

  Returns:
    Environment dict.
  """
  env = dict(
    base_env if base_env is not None else os.environ
  )
  if claude_home:
    env["CLAUDE_CONFIG_DIR"] = str(
      Path(claude_home).expanduser()
    )
  from lib.config import load_env_file
  stored = load_env_file(env_file)
  for name in CLAUDE_AUTH_VARS:
    if not env.get(name) and stored.get(name):
      env[name] = stored[name]
  return env


def parse_cli_event(line):
  """Parse one stream-json line into an output record.

  Unknown event types are kept rather than dropped — the
  CLI emits types that did not exist when this was written
  (rate_limit_event is one), and a parser that only
  understood a fixed set would silently discard them.

  Args:
    line: One line of CLI stdout.

  Returns:
    (kind, content, meta) tuple, or None for blank lines.
  """
  line = line.strip()
  if not line:
    return None
  try:
    event = json.loads(line)
  except ValueError:
    # Not JSON: still worth keeping verbatim.
    return ("stdout", line[:4000], {})
  kind = event.get("type", "unknown")
  if kind == "assistant":
    texts = [
      block.get("text", "")
      for block in event.get("message", {}).get(
        "content", []
      )
      if block.get("type") == "text"
    ]
    content = "\n".join(t for t in texts if t)
    if not content:
      content = _tool_summary(event)
    return ("assistant", content[:8000], {})
  if kind == "result":
    return (
      "result",
      str(event.get("result", ""))[:8000],
      {
        "is_error": bool(event.get("is_error")),
        "subtype": event.get("subtype"),
        "num_turns": event.get("num_turns"),
        "total_cost_usd": event.get("total_cost_usd"),
        "terminal_reason": event.get("terminal_reason"),
        "api_error_status": event.get("api_error_status"),
      },
    )
  if kind == "system":
    return (
      "system",
      f"session {event.get('session_id', '?')} "
      f"model {event.get('model', '?')}",
      {},
    )
  return (kind, json.dumps(event)[:4000], {})


def _tool_summary(event):
  """Summarise tool_use blocks in an assistant event."""
  names = [
    block.get("name", "?")
    for block in event.get("message", {}).get("content", [])
    if block.get("type") == "tool_use"
  ]
  return f"tool_use: {', '.join(names)}" if names else ""


def read_stage_result(cwd):
  """Read .stage-result.json from a worktree.

  Mirrors PipelineExecutor._read_stage_result so a step
  passes or fails by the same rule wherever it ran.

  Args:
    cwd: Directory the agent ran in.

  Returns:
    Parsed dict, or None if absent or unreadable.
  """
  path = Path(cwd) / ".stage-result.json"
  if not path.exists():
    return None
  try:
    return json.loads(path.read_text())
  except (ValueError, OSError):
    return None


def free_disk_mb(path):
  """Free space at a path, in MB.

  Args:
    path: Any path on the filesystem of interest.

  Returns:
    Free megabytes, or None if it cannot be determined.
  """
  # Walk up to an existing ancestor rather than giving up.
  # The path is usually the worker's work dir, which may not
  # exist yet on a fresh machine -- and returning None there
  # disables the --disk-floor-mb guard silently, so the
  # worker would accept work on a nearly-full disk and fail
  # partway through a build instead of declining.
  current = Path(path).resolve()
  for candidate in [current] + list(current.parents):
    try:
      usage = shutil.disk_usage(str(candidate))
    except OSError:
      continue
    return usage.free // (1024 * 1024)
  return None


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
  """Total RAM in MB, or None if it cannot be read.

  os.sysconf does not exist on Windows, so that path alone
  reported None there -- leaving the scheduler with no idea
  how much memory a Windows worker has, which is exactly the
  figure that decides whether to send it a heavy build.
  """
  try:
    pages = os.sysconf("SC_PHYS_PAGES")
    size = os.sysconf("SC_PAGE_SIZE")
    return (pages * size) // (1024 * 1024)
  except (ValueError, OSError, AttributeError):
    pass
  if platform.system().lower() == "windows":
    return _ram_mb_windows()
  return None


def _ram_mb_windows():
  """Total RAM in MB via the Win32 API.

  ctypes rather than shelling out to wmic: wmic is deprecated
  and absent from recent Windows builds, and this needs no
  subprocess.

  Returns:
    Megabytes, or None if the call failed.
  """
  import ctypes

  class MemoryStatusEx(ctypes.Structure):
    _fields_ = [
      ("dwLength", ctypes.c_ulong),
      ("dwMemoryLoad", ctypes.c_ulong),
      ("ullTotalPhys", ctypes.c_ulonglong),
      ("ullAvailPhys", ctypes.c_ulonglong),
      ("ullTotalPageFile", ctypes.c_ulonglong),
      ("ullAvailPageFile", ctypes.c_ulonglong),
      ("ullTotalVirtual", ctypes.c_ulonglong),
      ("ullAvailVirtual", ctypes.c_ulonglong),
      ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]

  try:
    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(MemoryStatusEx)
    ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(
      ctypes.byref(status)
    )
    if not ok:
      return None
    return status.ullTotalPhys // (1024 * 1024)
  except (AttributeError, OSError):
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
  if upstream is None:
    # `--upstream none`: mirrors are seeded from outside, so
    # there is nothing to clone or refresh from. Saying so
    # beats pointing the worker at an unreachable host and
    # tolerating the failure once per repo per run.
    if not mirror.exists():
      raise WorkerError(
        f"no mirror for {repo} at {mirror} and this worker "
        "has no upstream to fetch from (--upstream none). "
        "Run `takt provision` against it to seed the repo."
      )
    return mirror
  if not mirror.exists():
    mirror.parent.mkdir(parents=True, exist_ok=True)
    log.info("Cloning mirror %s from %s", repo, upstream)
    git(["clone", "--mirror", upstream, str(mirror)],
        timeout=7200)
    return mirror
  git(["remote", "set-url", "origin", upstream],
      cwd=mirror, check=False)
  # A failed refresh is not fatal when a mirror is already
  # here. It legitimately has no upstream it can reach: a
  # worker provisioned by `takt provision` had its mirrors
  # *pushed* to it from the scheduler, so it may hold no
  # credentials for fetching at all. Whether the run can
  # proceed is decided by materialise(), which checks for
  # the exact commit and fails with a precise message if it
  # is genuinely missing. Failing here instead would reject
  # work the worker can perfectly well do.
  proc = git(
    ["fetch", "--prune", "origin",
     "+refs/heads/*:refs/heads/*"],
    cwd=mirror, timeout=7200, check=False,
  )
  if proc.returncode != 0:
    log.warning(
      "Could not refresh mirror %s from %s: %s. Continuing "
      "with what the mirror already holds.",
      repo, upstream, proc.stderr.strip()[:200],
    )
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
      #
      # check=False so the *absence of the commit* is what
      # gets reported, not the fetch failure. A push-seeded
      # worker has no upstream it can reach, so raising here
      # would answer "why can this run not proceed?" with
      # "origin does not appear to be a git repository" --
      # sending the reader to look at remotes when the real
      # answer is that this mirror needs another push.
      if upstream_for(repo) is None:
        # Nothing to retry against.
        raise WorkerError(
          f"commit {sha} for {repo} is not in this "
          f"worker's mirror, and it has no upstream to "
          f"fetch from (--upstream none). Run `takt "
          f"provision` against it to send the newer commits."
        )
      fetched = git(
        ["fetch", "--prune", "origin",
         "+refs/heads/*:refs/heads/*"],
        cwd=mirror, timeout=7200, check=False,
      )
      if not have_commit(mirror, sha):
        detail = (
          "" if fetched.returncode == 0 else
          " (it could not be fetched either: "
          f"{fetched.stderr.strip().splitlines()[0][:120]})"
        )
        raise WorkerError(
          f"commit {sha} for {repo} is not reachable from "
          f"this worker's mirror{detail}. If this worker "
          "was set up with `takt provision`, run it again "
          "to push the newer commits across."
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
