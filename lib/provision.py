"""Set a machine up as a takt worker, from the scheduler.

A worker needs four things: the takt code, a credential
file naming the scheduler, somewhere to build, and mirrors
of the root repos to cut worktrees from. Doing that by hand
is a dozen steps and the failure modes are obscure, so takt
does it.

The direction of travel matters. A worker normally *fetches*
root repos (`--upstream scheduler`), which means the worker
needs an unlocked key of its own. Provisioning runs the
other way: the scheduler already has working SSH to the
machine, so it **pushes** the repos across. A worker can
therefore be fully stocked while holding no git credentials
at all, which is the usual state of a laptop whose key is
passphrase-protected.

Mirrors are pushed as bare repos and the worker's fetch
becomes a refresh it is allowed to fail: see ensure_mirror()
in lib/worker.py.
"""

import logging
import shlex
import subprocess
from pathlib import Path

from lib.config import (
  ROOT_DIR,
  api_base_url,
  api_token,
  load_repos_config,
)

log = logging.getLogger("takt.provision")

TAKT_REPO = "https://github.com/KRuskowski/takt.git"

# Where things land on the worker. Relative to its login
# directory so the same values work for any user.
REMOTE_TAKT = "dev/takt"
REMOTE_MIRRORS = "takt-worker/mirrors"
REMOTE_WORK = "takt-worker"

SSH_OPTS = [
  "-o", "BatchMode=yes",
  "-o", "ConnectTimeout=10",
]


class ProvisionError(Exception):
  """Provisioning could not continue."""


def ssh(host, command, timeout=120, check=True):
  """Run a command on the worker over SSH.

  Args:
    host: user@host, or an ssh config alias.
    command: Shell command to run remotely.
    timeout: Seconds before giving up.
    check: Raise ProvisionError on a non-zero exit.

  Returns:
    subprocess.CompletedProcess.

  Raises:
    ProvisionError: If check and the command failed.
  """
  proc = subprocess.run(
    ["ssh"] + SSH_OPTS + [host, command],
    capture_output=True, text=True, timeout=timeout,
  )
  if check and proc.returncode != 0:
    raise ProvisionError(
      f"ssh {host}: {command!r} failed "
      f"({proc.returncode}): {proc.stderr.strip()[:400]}"
    )
  return proc


def check_channel(host):
  """Verify the SSH channel carries nothing but the output.

  This is checked first and on its own because the symptom
  is so far from the cause. Anything that writes to stdout
  on login -- an sshd `Banner`, a `~/.ssh/rc` that echoes, a
  shell rc printing a greeting -- is spliced in front of
  every command's output. git-over-ssh and rsync then fail
  with "protocol incompatibility" or "is your shell clean?",
  which names neither the file nor the machine at fault.

  Args:
    host: user@host to test.

  Returns:
    The remote hostname.

  Raises:
    ProvisionError: If the channel carries extra output,
      naming the likely culprits.
  """
  sentinel = "takt-channel-ok"
  proc = ssh(host, f"printf '%s' {sentinel}")
  if proc.stdout != sentinel:
    extra = proc.stdout.replace(sentinel, "")
    raise ProvisionError(
      f"the SSH channel to {host} is not clean: it carries "
      f"{extra.strip()[:200]!r} in front of command output. "
      "git and rsync over this connection will fail with a "
      "confusing 'protocol incompatibility' error. Look for "
      "an sshd Banner directive (/etc/ssh/sshd_config and "
      "sshd_config.d/), a ~/.ssh/rc that writes to stdout, "
      "or a shell startup file printing a greeting -- for "
      "zsh that is ~/.zshenv, which non-interactive shells "
      "also read."
    )
  return ssh(host, "hostname").stdout.strip()


def check_prereqs(host):
  """Report what the worker machine has installed.

  Args:
    host: user@host to inspect.

  Returns:
    Dict of tool name -> True/False. 'claude' decides
    whether this worker can be given agent steps; 'git' and
    'python3' are required.

  Raises:
    ProvisionError: If git or python3 is missing, or the
      Python yaml module is absent.
  """
  tools = ("git", "python3", "claude", "cmake", "ninja",
           "gcc", "rsync")
  found = {}
  for tool in tools:
    proc = ssh(
      host, f"command -v {tool} >/dev/null 2>&1", check=False
    )
    found[tool] = proc.returncode == 0
  missing = [t for t in ("git", "python3") if not found[t]]
  if missing:
    raise ProvisionError(
      f"{host} is missing {', '.join(missing)}; install it "
      "there and run provisioning again"
    )
  proc = ssh(
    host, "python3 -c 'import yaml'", check=False
  )
  found["python3-yaml"] = proc.returncode == 0
  if not found["python3-yaml"]:
    raise ProvisionError(
      f"{host} has no Python yaml module. Install "
      "python3-yaml (apt) there; it is the only "
      "third-party module a worker needs."
    )
  return found


def install_takt(host, repo=TAKT_REPO, ref="master"):
  """Clone or update the takt checkout on the worker.

  Cloned over HTTPS from the public repo, so the worker
  needs no git credentials for this either.

  Args:
    host: user@host.
    repo: URL to clone takt from.
    ref: Branch to check out.

  Returns:
    The commit now checked out on the worker.
  """
  quoted_repo = shlex.quote(repo)
  quoted_ref = shlex.quote(ref)
  script = (
    f"set -e; "
    f"if [ -d ~/{REMOTE_TAKT}/.git ]; then "
    f"  git -C ~/{REMOTE_TAKT} remote set-url origin "
    f"{quoted_repo}; "
    f"  git -C ~/{REMOTE_TAKT} fetch --quiet origin "
    f"{quoted_ref}; "
    f"  git -C ~/{REMOTE_TAKT} reset --hard --quiet "
    f"FETCH_HEAD; "
    f"else "
    f"  mkdir -p ~/$(dirname {REMOTE_TAKT}); "
    f"  git clone --quiet --branch {quoted_ref} "
    f"{quoted_repo} ~/{REMOTE_TAKT}; "
    f"fi; "
    f"git -C ~/{REMOTE_TAKT} log --oneline -1"
  )
  return ssh(host, script, timeout=600).stdout.strip()


def write_env(host, scheduler_url, token, worker_id):
  """Install the worker's credential file.

  Written with a restrictive umask and verified afterwards,
  because this file holds the API token. It is deliberately
  not part of the checkout: takt's config/takt.yaml is in a
  public repo.

  Args:
    host: user@host.
    scheduler_url: URL the worker should dial.
    token: API token, or None if the scheduler has none.
    worker_id: Name this worker registers under.

  Returns:
    The file mode as reported by the worker, e.g. '600'.

  Raises:
    ProvisionError: If the file did not end up private.
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
  script = (
    "set -e; umask 077; mkdir -p ~/.config/takt; "
    f"cat > ~/.config/takt/env <<'TAKT_EOF'\n{body}"
    "TAKT_EOF\n"
    "chmod 600 ~/.config/takt/env; "
    "stat -c '%a' ~/.config/takt/env"
  )
  mode = ssh(host, script).stdout.strip()
  if mode != "600":
    raise ProvisionError(
      f"credential file on {host} has mode {mode}, "
      "expected 600"
    )
  return mode


def repos_to_seed(selected=None):
  """Decide which root repos to push to a worker.

  Args:
    selected: Explicit repo names, or None for all managed
      repos.

  Returns:
    List of (name, path) for repos that exist locally.

  Raises:
    ProvisionError: If a named repo is not managed or not
      present.
  """
  config = load_repos_config().get("repos", {})
  if selected:
    unknown = [r for r in selected if r not in config]
    if unknown:
      raise ProvisionError(
        f"not managed repos: {', '.join(unknown)}. See "
        "config/repos.yaml."
      )
    names = list(selected)
  else:
    names = list(config)
  pairs = []
  for name in names:
    path = ROOT_DIR / name
    if not (path / ".git").is_dir() and not (
      path / "HEAD"
    ).is_file():
      if selected:
        raise ProvisionError(
          f"root repo {name} is not at {path}"
        )
      log.warning("Skipping %s: not at %s", name, path)
      continue
    pairs.append((name, path))
  return pairs


def seed_mirror(host, repo, path, mirrors=REMOTE_MIRRORS,
                timeout=7200):
  """Push one root repo to the worker as a bare mirror.

  Pushed rather than fetched so the worker needs no
  credentials. Re-running is cheap: git sends only what the
  far side lacks, so this doubles as the refresh path.

  Args:
    host: user@host.
    repo: Repo name.
    path: Local path to the root repo.
    mirrors: Remote mirror directory, relative to login dir.
    timeout: Seconds before giving up on the push.

  Returns:
    True if objects were transferred, False if already
    up to date.

  Raises:
    ProvisionError: If the push failed.
  """
  remote = f"{mirrors}/{repo}.git"
  ssh(
    host,
    f"set -e; mkdir -p ~/{shlex.quote(mirrors)}; "
    f"if [ ! -d ~/{shlex.quote(remote)} ]; then "
    f"git init --quiet --bare ~/{shlex.quote(remote)}; fi",
  )
  # --mirror would also delete refs the scheduler no longer
  # has, which is right: the scheduler is the source of
  # truth for what branches exist.
  proc = subprocess.run(
    [
      "git", "push", "--mirror",
      f"{host}:{remote}",
    ],
    cwd=str(path), capture_output=True, text=True,
    timeout=timeout,
    env=_push_env(),
  )
  if proc.returncode != 0:
    raise ProvisionError(
      f"pushing {repo} to {host} failed: "
      f"{proc.stderr.strip()[:400]}"
    )
  return "Everything up-to-date" not in (
    proc.stderr + proc.stdout
  )


def _push_env():
  """Environment for the seeding push.

  BatchMode so a missing key fails instead of hanging on a
  prompt, matching how every other call here behaves.

  Returns:
    Dict suitable for subprocess env.
  """
  import os
  env = dict(os.environ)
  env["GIT_SSH_COMMAND"] = (
    "ssh " + " ".join(SSH_OPTS)
  )
  return env


def worker_unit(scheduler_host=None, upstream="scheduler",
                agent_steps=True):
  """Render the systemd user unit for a worker.

  Generated rather than shipped as a file because the unit
  needs values only provisioning knows (the scheduler host).
  A half-substituted template on disk would drift.

  PATH is set explicitly: a systemd unit inherits almost
  nothing, and the worker must be able to find `claude` --
  which usually lives in ~/.local/bin, not a system path.
  Without it the worker would start, declare itself
  build-only, and silently never take an agent step.

  Args:
    scheduler_host: user@host holding the root repos, for
      --upstream scheduler.
    upstream: Where mirrors fetch from.
    agent_steps: False to declare the worker build-only.

  Returns:
    Unit file text.
  """
  args = [
    f"worker --work-dir %h/{REMOTE_WORK}",
    f"--mirror-dir %h/{REMOTE_MIRRORS}",
    f"--upstream {upstream}",
  ]
  if upstream == "scheduler" and scheduler_host:
    args.append(f"--scheduler-host {scheduler_host}")
  if not agent_steps:
    args.append("--no-agent-steps")
  exec_args = " ".join(args)
  return f"""\
[Unit]
Description=takt worker
Documentation=https://github.com/KRuskowski/takt
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
# Scheduler URL, worker id and token. Not in the checkout:
# config/takt.yaml is tracked in a public repo.
EnvironmentFile=%h/.config/takt/env
# A unit inherits no PATH worth having, and the worker needs
# to find `claude` to be given agent steps at all.
Environment=PATH=%h/.local/bin:%h/bin:/usr/local/bin:/usr/bin:/bin
WorkingDirectory=%h/{REMOTE_TAKT}
ExecStart=/usr/bin/python3 %h/{REMOTE_TAKT}/bin/takt.py \
{exec_args}
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
"""


def install_service(host, scheduler_host=None,
                    upstream="scheduler", agent_steps=True):
  """Install, enable and start the worker as a service.

  Also enables lingering, without which a systemd *user*
  service stops when the operator logs out -- which would
  make a machine that is "on anyway" stop taking work the
  moment nobody is sitting at it.

  Args:
    host: user@host.
    scheduler_host: For --upstream scheduler.
    upstream: Where mirrors fetch from.

  Returns:
    Dict with the unit's active state and whether lingering
    is enabled.

  Raises:
    ProvisionError: If the unit did not come up.
  """
  unit = worker_unit(scheduler_host, upstream, agent_steps)
  # `enable --now` does nothing to an already-running unit,
  # so re-provisioning would update the checkout and leave
  # the worker executing the old code -- and a rewritten
  # unit file unused. Observed: checkout moved to the new
  # commit while the process stayed on the previous one.
  # restart covers both: it starts a stopped unit and
  # replaces a running one.
  ssh(
    host,
    "set -e; mkdir -p ~/.config/systemd/user; "
    "cat > ~/.config/systemd/user/takt-worker.service "
    f"<<'TAKT_UNIT_EOF'\n{unit}TAKT_UNIT_EOF\n"
    "systemctl --user daemon-reload; "
    "systemctl --user enable takt-worker.service; "
    "systemctl --user restart takt-worker.service",
    timeout=180,
  )
  # Needs no password for one's own user.
  linger = ssh(
    host,
    "loginctl enable-linger $USER >/dev/null 2>&1; "
    "loginctl show-user $USER -p Linger --value",
    check=False,
  ).stdout.strip()
  state = ssh(
    host,
    "systemctl --user is-active takt-worker.service",
    check=False,
  ).stdout.strip()
  if state != "active":
    logs = ssh(
      host,
      "journalctl --user -u takt-worker.service -n 20 "
      "--no-pager",
      check=False,
    ).stdout
    raise ProvisionError(
      f"takt-worker did not start on {host} (state="
      f"{state!r}):\n{logs[-800:]}"
    )
  return {"state": state, "linger": linger == "yes"}


def remote_capabilities(host):
  """Ask the worker what it reports about itself.

  Runs the same detect_capabilities() the worker uses at
  registration, so provisioning ends by showing exactly
  what the scheduler will see.

  Args:
    host: user@host.

  Returns:
    Parsed capability dict.

  Raises:
    ProvisionError: If the output could not be parsed.
  """
  import json
  # expanduser, not the bare relative path: this runs with
  # the takt checkout as cwd, so a relative work dir does
  # not resolve and free_disk_mb() silently reports None.
  script = (
    f"cd ~/{REMOTE_TAKT} && python3 -c "
    "\"import sys, json, os; sys.path.insert(0, '.'); "
    "from lib.worker import detect_capabilities; "
    "print(json.dumps(detect_capabilities("
    f"os.path.expanduser('~/{REMOTE_WORK}'))))\""
  )
  out = ssh(host, script, timeout=120).stdout.strip()
  try:
    return json.loads(out)
  except ValueError as e:
    raise ProvisionError(
      f"could not read capabilities from {host}: {e}: "
      f"{out[:200]!r}"
    ) from e


def _posix_ops():
  """The POSIX implementations, as a namespace.

  Lets provision() call ops.<step>(...) without caring which
  platform it got, so the ordering and the guards live in one
  place instead of being duplicated per OS.

  Returns:
    An object exposing the same step names as
    lib/provision_windows.py.
  """
  import types
  return types.SimpleNamespace(
    check_channel=check_channel,
    check_prereqs=check_prereqs,
    install_takt=lambda host, repo, ref="master": (
      install_takt(host, repo=repo, ref=ref)
    ),
    write_env=write_env,
    seed_mirror=seed_mirror,
    remote_capabilities=remote_capabilities,
    install_service=install_service,
  )


def provision(host, worker_id=None, repos=None,
              scheduler_url=None, token=None, ref="master",
              takt_repo=TAKT_REPO, skip_repos=False,
              service=False, scheduler_host=None,
              upstream="scheduler", agent_steps=True,
              on_progress=None):
  """Set a machine up as a takt worker.

  Ordered so the cheap checks fail first: a machine missing
  git should not be found out after a 4 GB push.

  Args:
    host: user@host, or an ssh config alias.
    worker_id: Name to register under. Defaults to the
      remote hostname.
    repos: Root repos to seed, or None for all managed.
    scheduler_url: URL the worker dials. Defaults to this
      machine's configured API URL.
    token: API token. Defaults to the configured one.
    ref: takt branch to check out on the worker.
    takt_repo: URL to clone takt from.
    skip_repos: Set up the worker but push no repos.
    service: Install, enable and start the worker as a
      systemd user service, with lingering, so the machine
      takes work without anyone logged in.
    scheduler_host: user@host for the worker's --upstream
      scheduler, used in the unit.
    upstream: Where the worker's mirrors fetch from.
    agent_steps: False to declare the worker build-only.
      Right when the Claude CLI is present but not signed
      in: the scheduler would otherwise hand it agent runs
      that fail.
    on_progress: Optional callback(stage, detail) so a CLI
      can report during a push that takes minutes.

  Returns:
    Dict describing what was done.

  Raises:
    ProvisionError: On any step that could not complete.
  """
  def progress(stage, detail=""):
    if on_progress:
      on_progress(stage, detail)

  # Detect before changing anything: the Windows steps are a
  # different dialect entirely (no POSIX shell, no systemd,
  # and git cannot be pushed to through cmd.exe), so the
  # wrong one half-applied would be worse than neither.
  from lib import provision_windows as win
  windows = win.is_windows(host)
  ops = win if windows else _posix_ops()

  progress("channel", host)
  hostname = ops.check_channel(host)

  flavour = "windows" if windows else "posix"
  progress("prereqs", f"{hostname} ({flavour})")
  tools = ops.check_prereqs(host)

  url = scheduler_url or api_base_url()
  if "127.0.0.1" in url or "localhost" in url:
    raise ProvisionError(
      f"scheduler URL {url!r} points at loopback, which on "
      f"{host} means {host} itself. Pass --scheduler with "
      "an address the worker can reach, or set TAKT_API_URL."
    )
  resolved_token = token if token is not None else api_token()
  name = worker_id or hostname

  progress("takt", f"{ref} on {hostname}")
  commit = ops.install_takt(host, takt_repo, ref=ref)

  progress("credentials", "~/.config/takt/env")
  ops.write_env(host, url, resolved_token, name)

  if not windows:
    ssh(
      host,
      f"mkdir -p ~/{shlex.quote(REMOTE_WORK)} "
      f"~/{shlex.quote(REMOTE_MIRRORS)}",
    )

  seeded = []
  if not skip_repos:
    pairs = repos_to_seed(repos)
    for index, (repo, path) in enumerate(pairs, start=1):
      progress(
        "repo", f"{repo} ({index}/{len(pairs)})"
      )
      changed = ops.seed_mirror(host, repo, path)
      seeded.append({"repo": repo, "updated": changed})

  progress("verify", hostname)
  caps = ops.remote_capabilities(host)

  svc = None
  if service:
    progress("service", "worker")
    kwargs = {
      "scheduler_host": scheduler_host,
      "upstream": upstream,
    }
    # Only the Windows task takes this today; the systemd
    # unit is added to in a follow-up rather than silently
    # ignoring the flag.
    if not agent_steps:
      kwargs["agent_steps"] = False
    svc = ops.install_service(host, **kwargs)

  return {
    "host": host,
    "hostname": hostname,
    "platform": "windows" if windows else "posix",
    "worker_id": name,
    "scheduler_url": url,
    "authenticated": bool(resolved_token),
    "takt_commit": commit,
    "tools": tools,
    "repos": seeded,
    "capabilities": caps,
    "service": svc,
    "start_command": (
      f'cd /d "{win.WIN_TAKT}" && python bin\\takt.py '
      f'worker --work-dir "{win.WIN_WORK}" '
      f'--mirror-dir "{win.WIN_MIRRORS}"'
      if windows else
      f"cd ~/{REMOTE_TAKT} && python3 bin/takt.py worker "
      f"--work-dir ~/{REMOTE_WORK} "
      f"--mirror-dir ~/{REMOTE_MIRRORS}"
    ),
  }
