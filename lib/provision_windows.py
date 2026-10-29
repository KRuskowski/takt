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
# Under C:\Users\Karl so it is readable without elevation,
# and so a SYSTEM-run task and the operator look at the same
# file.
LOG_PATH = r"%USERPROFILE%\takt-worker\worker.log"


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
  # /inheritance:r drops what the profile directory hands
  # down; then the owner, and SYSTEM. SYSTEM is granted
  # explicitly because the build-only worker runs as a
  # scheduled task under that account and has to read this
  # file -- the alternative is putting the token on the
  # task's command line, where any process listing would
  # show it. It is not a weakening: SYSTEM can take
  # ownership of any file on the machine regardless.
  provision.ssh(
    host,
    f'icacls "{target}" /inheritance:r '
    f'/grant:r "%USERNAME%":F '
    f'/grant:r "SYSTEM":R',
  )
  listing = provision.ssh(host, f'icacls "{target}"').stdout
  lowered = listing.lower()
  # "users:" would match "nt authority\\system:" nowhere, but
  # BUILTIN\\Users would -- that is the one to catch.
  for bad in ("everyone", "builtin\\users", "authenticated users"):
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


def home_dir(host):
  """The SSH user's profile directory, absolutely.

  Needed because the launcher runs as SYSTEM, where
  %USERPROFILE% expands to the service profile rather than
  to C:\\Users\\<someone> -- so every path in it has to be
  resolved here, as the SSH user, and written out literally.

  Args:
    host: user@host.

  Returns:
    e.g. 'C:\\Users\\Karl'.
  """
  out = provision.ssh(host, "echo %USERPROFILE%").stdout
  home = out.strip()
  if not home or "%" in home:
    raise ProvisionError(
      f"could not resolve the home directory on {host}: "
      f"{out[:120]!r}"
    )
  return home


def user_path(host):
  """The SSH user's PATH entries.

  A SYSTEM-run task inherits only the *machine* PATH. On
  this hardware that is enough for cmake and git but not for
  ninja or python, which sit under the user profile -- so a
  SYSTEM worker would report a thinner toolchain than the
  machine has and be mis-gated for builds. The launcher
  prepends these, the same reason the systemd unit sets PATH
  explicitly.

  Args:
    host: user@host.

  Returns:
    The user PATH string, or "" if it could not be read.
  """
  proc = provision.ssh(
    host,
    "powershell -NoProfile -Command "
    "\"[Environment]::GetEnvironmentVariable('Path','User')\"",
    check=False,
  )
  if proc.returncode != 0:
    return ""
  return proc.stdout.strip().replace("\r", "").replace(
    "\n", ""
  )


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
                    agent_steps=True, claude_home=None):
  """Run the worker as a scheduled task.

  Windows has no systemd, and a scheduled task is the
  equivalent. It runs as SYSTEM on an at-startup trigger,
  which is the only identity Windows will start with nobody
  logged in unless a password is stored -- and a password
  would have to go on a command line. Verified on this
  hardware with no user logged on: `Logon Mode:
  Interactive/Background`, and the worker registered.

  Running as SYSTEM does **not** prevent agent steps, which
  an earlier version of this assumed. Measured as
  nt authority\\system on this machine: `where claude`
  resolves through the launcher's PATH, `claude --version`
  runs, and a config directory under the operator's profile
  is readable. The only thing SYSTEM lacks is credentials,
  and those are supplied by pointing --claude-home at a
  config directory -- which also happens to be how each
  worker gets its own account, the thing that actually
  multiplies agent throughput.

  Signing that directory in needs no console either: the
  CLI prints a URL and a code, which works in an
  interactive SSH session.

  Args:
    host: user@host.
    scheduler_host: Passed to the worker for --upstream
      scheduler.
    upstream: Where mirrors refresh from.
    user: Unused; retained so the signature matches the
      POSIX side.
    agent_steps: False to declare the worker build-only.
      Right while the Claude CLI is installed but not yet
      signed in -- otherwise the scheduler hands it agent
      runs that fail.
    claude_home: Directory to use as CLAUDE_CONFIG_DIR for
      agent steps. Required for agent steps under SYSTEM,
      which has no user profile of its own.

  Returns:
    Dict with the task's state.

  Raises:
    ProvisionError: If the task could not be created or is
      not ready.
  """
  python = python_path(host)
  home = home_dir(host)

  # The command goes in a launcher script, not in /tr:
  # schtasks caps /tr at 261 characters and the full command
  # with its redirection is longer than that. The script is
  # also where the paths become absolute -- the task runs as
  # SYSTEM, where %USERPROFILE% is the service profile, not
  # C:\Users\<someone>, so nothing here may be left to
  # expand at trigger time.
  takt_dir = f"{home}\\dev\\takt"
  work_dir = f"{home}\\takt-worker"
  mirror_dir = f"{work_dir}\\mirrors"
  log_path = f"{work_dir}\\worker.log"
  env_file = f"{home}\\.config\\takt\\env"
  extra_path = user_path(host)
  path_line = (
    f'set "PATH={extra_path};%PATH%"\r\n'
    if extra_path else ""
  )
  launcher_win = f"{work_dir}\\takt-worker.cmd"

  flags = f"--upstream {upstream}"
  if upstream == "scheduler" and scheduler_host:
    flags += f" --scheduler-host {scheduler_host}"
  if not agent_steps:
    flags += " --no-agent-steps"
  if claude_home:
    flags += f' --claude-home "{claude_home}"'

  script = (
    "@echo off\r\n"
    "rem Generated by `takt provision`. Edit the generator,\r\n"
    "rem not this file: provisioning overwrites it.\r\n"
    "rem\r\n"
    "rem TAKT_ENV_FILE is set explicitly because this may\r\n"
    "rem run as SYSTEM, where Path.home() is the service\r\n"
    "rem profile -- so the default lookup would miss the\r\n"
    "rem credential file entirely and the worker would exit\r\n"
    "rem with '--scheduler is required'.\r\n"
    "rem\r\n"
    "rem PATH carries the user's entries because a SYSTEM\r\n"
    "rem task inherits only the machine PATH -- enough for\r\n"
    "rem cmake and git here, but not ninja or python.\r\n"
    f'set "TAKT_ENV_FILE={env_file}"\r\n'
    f'{path_line}'
    f'cd /d "{takt_dir}"\r\n'
    f'"{python}" "{takt_dir}\\bin\\takt.py" worker '
    f'--work-dir "{work_dir}" '
    f'--mirror-dir "{mirror_dir}" '
    f'{flags} >> "{log_path}" 2>&1\r\n'
  )
  provision.ssh(
    host,
    f'if not exist "{work_dir}" mkdir "{work_dir}"',
  )
  if claude_home:
    # Created here, and granted to SYSTEM, because the task
    # runs as SYSTEM and the CLI needs to both read and
    # write this directory. Karl signs it in over SSH:
    #   set CLAUDE_CONFIG_DIR=<dir> && claude setup-token
    provision.ssh(
      host,
      f'if not exist "{claude_home}" '
      f'mkdir "{claude_home}"',
    )
    provision.ssh(
      host,
      f'icacls "{claude_home}" /grant "SYSTEM":(OI)(CI)F',
      check=False,
    )
  with tempfile.TemporaryDirectory() as tmp:
    local = Path(tmp) / "takt-worker.cmd"
    local.write_text(script)
    _scp(local, host, f"{SFTP_WORK}/takt-worker.cmd",
         timeout=120)

  provision.ssh(
    host,
    f'schtasks /create /tn {TASK_NAME} '
    f'/sc onstart /ru SYSTEM '
    f'/f /tr "\'{launcher_win}\'"',
    timeout=180,
  )
  # End before running. `schtasks /run` on a task that is
  # already running is a no-op, so re-provisioning would
  # leave the *previous* process alive -- still on the old
  # launcher and the old code. Exactly the trap `systemctl
  # enable --now` set on the Linux side.
  provision.ssh(
    host, f"schtasks /end /tn {TASK_NAME}", check=False
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
    "linger": True,
    "task": TASK_NAME,
    "runs_as": "SYSTEM",
    "unattended": True,
    "agent_steps": agent_steps,
    "log": log_path,
    "launcher": launcher_win,
  }
