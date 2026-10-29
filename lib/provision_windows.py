"""Provision a Windows machine as a takt worker.

Everything in lib/provision.py assumes a POSIX login shell:
heredocs, `umask`, `stat -c`, `command -v`, systemd. None of
that exists on a Windows host whose SSH shell is cmd.exe, so
the same steps live here in a Windows dialect.

Two choices are worth knowing about.

**Files go over SFTP, not through the shell.** Quoting a
multi-line file through ssh -> cmd.exe -> powershell is a
reliable way to produce something subtly wrong. SFTP is
shell-independent and already works on this host.

**Repos arrive as git bundles, not as a push.** git sends
`git-receive-pack 'path'` to the remote shell; cmd.exe does
not strip the single quotes, so git on the far side looks for
a directory whose name literally contains them and reports
"does not appear to be a git repository". The usual fix is to
repoint the host's SSH DefaultShell at powershell, but that
is a machine-wide registry change affecting every other tool
that connects. A bundle is a plain file: it copies over SFTP
and is fetched locally on the worker, so no shell parses a
git path at all.
"""

import logging
import subprocess
import tempfile
from pathlib import Path

from lib import provision
from lib.provision import ProvisionError

# ssh() is reached through the module rather than imported by
# name. A `from ... import ssh` binds at import time, which
# makes the single transport impossible to redirect -- and in
# particular impossible for a test to stub, so the test suite
# could reach a real machine.


log = logging.getLogger("takt.provision.windows")

SENTINEL = "takt-channel-ok"

# Where things land, as cmd.exe sees them.
WIN_TAKT = r"%USERPROFILE%\dev\takt"
WIN_WORK = r"%USERPROFILE%\takt-worker"
WIN_MIRRORS = r"%USERPROFILE%\takt-worker\mirrors"

# ...and as SFTP sees them. OpenSSH on Windows resolves a
# relative remote path against the login directory.
SFTP_TAKT = "dev/takt"
SFTP_WORK = "takt-worker"
SFTP_MIRRORS = "takt-worker/mirrors"

TASK_NAME = "takt-worker"


def is_windows(host):
  """Detect whether the far side is Windows.

  Probed rather than configured, and done before anything is
  changed. `ver` is a cmd.exe builtin that no POSIX shell
  has, so its success is the signal.

  Args:
    host: user@host, or an ssh config alias.

  Returns:
    True if the remote shell is cmd.exe / Windows.
  """
  proc = provision.ssh(host, "ver", check=False)
  out = (proc.stdout or "") + (proc.stderr or "")
  return proc.returncode == 0 and "Windows" in out


def check_channel(host):
  """Verify the SSH channel carries only command output.

  Same purpose as the POSIX check: anything printed at login
  is spliced in front of every command's output and makes
  file transfer fail with an error that names neither the
  file nor the machine.

  Args:
    host: user@host.

  Returns:
    The remote hostname.

  Raises:
    ProvisionError: If the channel carries extra output.
  """
  proc = provision.ssh(host, f"echo {SENTINEL}")
  # cmd.exe terminates lines with CRLF; that is the shell
  # being normal, not noise, so it is not counted as dirt.
  out = proc.stdout.replace("\r\n", "\n").strip()
  if out != SENTINEL:
    extra = out.replace(SENTINEL, "")
    raise ProvisionError(
      f"the SSH channel to {host} is not clean: it carries "
      f"{extra.strip()[:200]!r} in front of command output. "
      "File transfer over this connection will fail with a "
      "confusing error. On Windows, look for a Banner "
      "directive in C:\\ProgramData\\ssh\\sshd_config, or "
      "anything printing from the profile that cmd.exe runs "
      "(an AutoRun value under HKCU\\Software\\Microsoft\\"
      "Command Processor)."
    )
  return provision.ssh(host, "hostname").stdout.strip()


def check_prereqs(host):
  """Report what the Windows machine has installed.

  Args:
    host: user@host.

  Returns:
    Dict of tool name -> bool.

  Raises:
    ProvisionError: If git or python is missing, or the
      Python yaml module is absent.
  """
  tools = ("git", "python", "claude", "cmake", "ninja",
           "msbuild")
  found = {}
  for tool in tools:
    proc = provision.ssh(host, f"where {tool}", check=False)
    found[tool] = proc.returncode == 0
  # The worker invokes `python`, so that is the name that
  # matters; python3 is usually absent on Windows.
  found["python3"] = found["python"]
  missing = [t for t in ("git", "python") if not found[t]]
  if missing:
    raise ProvisionError(
      f"{host} is missing {', '.join(missing)}; install it "
      "there and run provisioning again"
    )
  proc = provision.ssh(
    host, 'python -c "import yaml"', check=False
  )
  found["python3-yaml"] = proc.returncode == 0
  if not found["python3-yaml"]:
    raise ProvisionError(
      f"{host} has no Python yaml module. Run "
      "'python -m pip install --user pyyaml' there; it is "
      "the only third-party module a worker needs."
    )
  return found


def install_takt(host, repo, ref="master"):
  """Clone or update the takt checkout on the worker.

  Args:
    host: user@host.
    repo: URL to clone from.
    ref: Branch to check out.

  Returns:
    The commit now checked out.
  """
  script = (
    f'if not exist "{WIN_TAKT}\\.git" '
    f'(git clone --quiet --branch {ref} {repo} '
    f'"{WIN_TAKT}") else '
    f'(git -C "{WIN_TAKT}" remote set-url origin {repo} '
    f'&& git -C "{WIN_TAKT}" fetch --quiet origin {ref} '
    f'&& git -C "{WIN_TAKT}" reset --hard --quiet '
    f'FETCH_HEAD)'
  )
  provision.ssh(host, script, timeout=900)
  return provision.ssh(
    host, f'git -C "{WIN_TAKT}" log --oneline -1'
  ).stdout.strip()


def _scp(local, host, remote_path, timeout=3600):
  """Copy a file to the worker over SFTP.

  Args:
    local: Local file path.
    host: user@host.
    remote_path: Destination, relative to the login dir.
    timeout: Seconds before giving up.

  Raises:
    ProvisionError: If the copy failed.
  """
  proc = subprocess.run(
    [
      "scp", "-o", "BatchMode=yes",
      "-o", "ConnectTimeout=10",
      str(local), f"{host}:{remote_path}",
    ],
    capture_output=True, text=True, timeout=timeout,
  )
  if proc.returncode != 0:
    raise ProvisionError(
      f"copying {local} to {host}:{remote_path} failed: "
      f"{proc.stderr.strip()[:400]}"
    )


def write_env(host, scheduler_url, token, worker_id):
  """Install the worker's credential file.

  Written via SFTP then locked down with icacls. There is no
  umask to rely on, so the file is briefly inheriting the
  directory's ACL; it is tightened immediately and the
  result is verified, because this file holds the API token.

  Args:
    host: user@host.
    scheduler_url: URL the worker should dial.
    token: API token, or None.
    worker_id: Name to register under.

  Returns:
    The icacls listing for the file.

  Raises:
    ProvisionError: If the ACL still grants anyone else.
  """
  lines = [
    "# Written by `takt provision`. Holds the scheduler",
    "# API token, so it is kept out of the takt checkout:",
    "# config/takt.yaml is tracked in a public repo.",
    f"TAKT_API_URL={scheduler_url}",
    f"TAKT_WORKER_ID={worker_id}",
  ]
  if token:
    lines.append(f"TAKT_API_TOKEN={token}")
  body = "\n".join(lines) + "\n"

  provision.ssh(
    host,
    r'if not exist "%USERPROFILE%\.config\takt" '
    r'mkdir "%USERPROFILE%\.config\takt"',
  )
  with tempfile.TemporaryDirectory() as tmp:
    local = Path(tmp) / "env"
    local.write_text(body)
    _scp(local, host, ".config/takt/env", timeout=120)

  target = r"%USERPROFILE%\.config\takt\env"
  # /inheritance:r drops the inherited ACEs, then the only
  # grant is the owner. Without the first part the file keeps
  # whatever the profile directory hands down.
  provision.ssh(
    host,
    f'icacls "{target}" /inheritance:r '
    f'/grant:r "%USERNAME%":F',
  )
  listing = provision.ssh(host, f'icacls "{target}"').stdout
  lowered = listing.lower()
  for bad in ("everyone", "users:", "authenticated users"):
    if bad in lowered:
      raise ProvisionError(
        f"credential file on {host} is still readable by "
        f"{bad!r} after icacls:\n{listing[:400]}"
      )
  return listing.strip()


def _remote_tips(host, repo):
  """Commit ids the worker's mirror already has.

  Used to make the bundle incremental. A first run returns
  nothing and the bundle carries everything.

  Args:
    host: user@host.
    repo: Repo name.

  Returns:
    List of commit ids.
  """
  mirror = f"{WIN_MIRRORS}\\{repo}.git"
  proc = provision.ssh(
    host,
    f'git --git-dir="{mirror}" for-each-ref '
    f'--format="%(objectname)" refs/heads',
    check=False,
  )
  if proc.returncode != 0:
    return []
  return [
    line.strip() for line in proc.stdout.splitlines()
    if line.strip()
  ]


def seed_mirror(host, repo, path, timeout=7200):
  """Send one root repo to the worker as a bare mirror.

  Transferred as a git bundle over SFTP rather than pushed,
  because git-over-ssh cannot address a path through
  cmd.exe. Incremental: only commits the worker lacks are
  bundled, so re-running is cheap.

  Args:
    host: user@host.
    repo: Repo name.
    path: Local path to the root repo.
    timeout: Seconds before giving up.

  Returns:
    True if anything was transferred, False if the worker was
    already up to date.

  Raises:
    ProvisionError: If any step failed.
  """
  mirror_win = f"{WIN_MIRRORS}\\{repo}.git"
  provision.ssh(
    host,
    f'if not exist "{WIN_MIRRORS}" '
    f'mkdir "{WIN_MIRRORS}"',
  )
  fresh = provision.ssh(
    host, f'if exist "{mirror_win}" (echo yes) else (echo no)'
  ).stdout.strip() == "no"
  if fresh:
    provision.ssh(
      host,
      f'git init --quiet --bare "{mirror_win}"',
    )

  have = [] if fresh else _remote_tips(host, repo)
  with tempfile.TemporaryDirectory() as tmp:
    bundle = Path(tmp) / f"{repo}.bundle"
    args = [
      "git", "bundle", "create", str(bundle), "--all",
    ]
    for sha in have:
      args.append(f"^{sha}")
    proc = subprocess.run(
      args, cwd=str(path), capture_output=True, text=True,
      timeout=timeout,
    )
    if proc.returncode != 0:
      combined = proc.stderr + proc.stdout
      # git refuses to write an empty bundle, which is what
      # "nothing new" looks like. That is success, not
      # failure.
      if "create empty bundle" in combined.lower():
        return False
      raise ProvisionError(
        f"bundling {repo} failed: "
        f"{proc.stderr.strip()[:400]}"
      )
    remote_bundle = f"{SFTP_MIRRORS}/{repo}.bundle"
    _scp(bundle, host, remote_bundle, timeout=timeout)

  try:
    provision.ssh(
      host,
      f'git --git-dir="{mirror_win}" fetch '
      f'"{WIN_MIRRORS}\\{repo}.bundle" '
      f'"+refs/heads/*:refs/heads/*"',
      timeout=timeout,
    )
  finally:
    provision.ssh(
      host,
      f'del /q "{WIN_MIRRORS}\\{repo}.bundle"',
      check=False,
    )
  return True


def remote_capabilities(host):
  """Ask the worker what it reports about itself.

  Args:
    host: user@host.

  Returns:
    Parsed capability dict.

  Raises:
    ProvisionError: If the output could not be parsed.
  """
  import json
  script = (
    f'cd /d "{WIN_TAKT}" && python -c '
    '"import sys, json, os; sys.path.insert(0, \'.\'); '
    'from lib.worker import detect_capabilities; '
    'print(json.dumps(detect_capabilities('
    'os.path.expanduser(\'~/takt-worker\'))))"'
  )
  out = provision.ssh(host, script, timeout=180).stdout.strip()
  try:
    return json.loads(out)
  except ValueError as e:
    raise ProvisionError(
      f"could not read capabilities from {host}: {e}: "
      f"{out[:200]!r}"
    ) from e


def python_path(host):
  """Absolute path to the Python the worker should run.

  A scheduled task starts with a minimal environment, so the
  interpreter is named absolutely rather than relying on
  PATH resolution at trigger time.

  Args:
    host: user@host.

  Returns:
    Absolute path to python.exe.

  Raises:
    ProvisionError: If no Python could be located.
  """
  out = provision.ssh(host, "where python", check=False).stdout
  for line in out.splitlines():
    candidate = line.strip()
    # The WindowsApps entry is the Store stub that opens the
    # Microsoft Store instead of running Python.
    if candidate.endswith("python.exe") and (
      "WindowsApps" not in candidate
    ):
      return candidate
  raise ProvisionError(
    f"no usable python.exe found on {host}: {out[:200]!r}"
  )


def install_service(host, scheduler_host=None,
                    upstream="scheduler", user=None,
                    agent_steps=True):
  """Run the worker as a scheduled task.

  Windows has no systemd, and a scheduled task is the
  equivalent. Which identity it runs as is not a free
  choice -- it follows from whether the worker does agent
  steps, and the two options exclude each other:

  **Build-only -> SYSTEM, at startup.** Runs unattended
  with nobody logged in, which is the whole point of using
  a machine that is "just on". Verified on this hardware:
  with no user logged on, a SYSTEM/ONSTART task reports
  `Logon Mode: Interactive/Background` and ran to
  completion. It works because the interpreter is named
  absolutely and git is on the *machine* PATH.

  **Agent steps -> the user, at logon.** The Claude CLI
  keeps its credentials in the user profile and lives on the
  user's PATH, so SYSTEM has no account to run as. The cost
  is real: schtasks will only create a named-user task
  without a stored password as "Interactive only", so it
  does **not** run while the machine sits at the login
  screen. Observed here: `Last Result: 267011`
  (SCHED_S_TASK_HAS_NOT_RUN) with no user logged on.

  A password would avoid that and is not an option: it
  would have to go on the command line.

  Args:
    host: user@host.
    scheduler_host: Passed to the worker for --upstream
      scheduler.
    upstream: Where mirrors refresh from.
    user: Account to run as for the agent-capable mode.
      Defaults to the SSH user.
    agent_steps: False to declare the worker build-only,
      which is also what makes unattended operation
      possible. Right while the Claude CLI is installed but
      not yet signed in -- otherwise the scheduler hands it
      agent runs that fail.

  Returns:
    Dict with the task's state.

  Raises:
    ProvisionError: If the task could not be created or is
      not ready.
  """
  if user is None:
    user = host.split("@")[0] if "@" in host else "%USERNAME%"
  python = python_path(host)
  # %USERPROFILE% in the paths below is expanded by cmd.exe
  # when schtasks is invoked, so what gets stored is already
  # absolute -- which is what makes the SYSTEM variant find
  # the right mirrors rather than the service profile's.
  # Do not "fix" these into literal strings.
  # Mirrors arrive by bundle, so the worker has no upstream
  # it can reach. ensure_mirror() treats a failed refresh of
  # an existing mirror as a warning, which is what makes
  # this work; materialise() still fails precisely if a
  # required commit is genuinely absent.
  command = (
    f'\\"{python}\\" \\"{WIN_TAKT}\\bin\\takt.py\\" worker '
    f'--work-dir \\"{WIN_WORK}\\" '
    f'--mirror-dir \\"{WIN_MIRRORS}\\" '
    f'--upstream {upstream}'
  )
  if upstream == "scheduler" and scheduler_host:
    command += f" --scheduler-host {scheduler_host}"
  if not agent_steps:
    command += " --no-agent-steps"
  if agent_steps:
    identity = f"/sc onlogon /ru {user}"
    runs_as, unattended = user, False
  else:
    identity = "/sc onstart /ru SYSTEM"
    runs_as, unattended = "SYSTEM", True
  provision.ssh(
    host,
    f'schtasks /create /tn {TASK_NAME} {identity} '
    f'/f /tr "{command}"',
    timeout=180,
  )
  provision.ssh(
    host, f"schtasks /run /tn {TASK_NAME}", check=False
  )
  query = provision.ssh(
    host, f"schtasks /query /tn {TASK_NAME} /fo list",
    check=False,
  ).stdout
  if "Ready" not in query and "Running" not in query:
    raise ProvisionError(
      f"scheduled task {TASK_NAME} on {host} is not ready:"
      f"\n{query[:600]}"
    )
  state = "running" if "Running" in query else "ready"
  return {
    "state": state,
    # "linger" is the POSIX name for the same property the
    # caller cares about: does this keep working with nobody
    # logged in.
    "linger": unattended,
    "task": TASK_NAME,
    "runs_as": runs_as,
    "unattended": unattended,
    "agent_steps": agent_steps,
  }
