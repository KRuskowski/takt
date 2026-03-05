"""Chroot CRUD operations for workspace isolation.

Creates overlayfs chroots backed by a shared base rootfs.
Each workspace gets its own upper layer for writes. Root
repos are bind-mounted at their original absolute paths so
git remotes work unchanged.

Privileged ops (mount/chroot) use sudo automatically.
"""

import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

from lib.config import (
  CHROOT_BASE_DIR,
  ROOT_DIR,
  TEMPLATES_DIR,
  WORKSPACES_DIR,
  get_repo_path,
)


def _chroot_dir(workspace):
  """Return the chroot directory for a workspace."""
  return WORKSPACES_DIR / workspace


def _upper_dir(workspace):
  return _chroot_dir(workspace) / "upper"


def _work_dir(workspace):
  return _chroot_dir(workspace) / "work"


def _merged_dir(workspace):
  return _chroot_dir(workspace) / "merged"


def _run(cmd, sudo=False, **kwargs):
  """Run a command, printing it first.

  Args:
    cmd: Command as list of strings.
    sudo: If True, prefix with sudo when not root.
  """
  if sudo and os.geteuid() != 0:
    cmd = ["sudo"] + list(cmd)
  log.debug("$ %s", " ".join(str(c) for c in cmd))
  kwargs.setdefault("check", True)
  return subprocess.run(cmd, **kwargs)


def _is_mountpoint(path):
  """Check if path is a mount point."""
  try:
    result = subprocess.run(
      ["mountpoint", "-q", str(path)],
      capture_output=True,
    )
    return result.returncode == 0
  except FileNotFoundError:
    return False


def chroot_exists(workspace):
  """Check if chroot dirs exist for a workspace."""
  return _chroot_dir(workspace).is_dir()


def is_mounted(workspace):
  """Check if the chroot overlay is currently mounted."""
  merged = _merged_dir(workspace)
  return merged.is_dir() and _is_mountpoint(merged)


def _mount_overlay(workspace):
  """Mount overlayfs for workspace."""
  merged = _merged_dir(workspace)
  if _is_mountpoint(merged):
    return
  _run([
    "mount", "-t", "overlay", "overlay",
    "-o", (
      f"lowerdir={CHROOT_BASE_DIR},"
      f"upperdir={_upper_dir(workspace)},"
      f"workdir={_work_dir(workspace)}"
    ),
    str(merged),
  ], sudo=True)


def _bind_mount(src, dest, readonly=False):
  """Bind-mount src to dest, optionally read-only."""
  if src.is_file():
    _run(["mkdir", "-p", str(dest.parent)], sudo=True)
    _run(["touch", str(dest)], sudo=True)
  else:
    _run(["mkdir", "-p", str(dest)], sudo=True)
  if _is_mountpoint(dest):
    return
  _run(["mount", "--bind", str(src), str(dest)],
       sudo=True)
  if readonly:
    _run([
      "mount", "-o", "remount,bind,ro",
      str(src), str(dest),
    ], sudo=True)


def _mount_system_dirs(workspace):
  """Bind-mount /proc, /sys, /dev, /etc/resolv.conf."""
  merged = _merged_dir(workspace)
  _bind_mount(Path("/proc"), merged / "proc", readonly=True)
  _bind_mount(Path("/sys"), merged / "sys", readonly=True)
  _bind_mount(Path("/dev"), merged / "dev")
  _run(["mkdir", "-p", str(merged / "dev" / "pts")],
       sudo=True)
  _bind_mount(Path("/dev/pts"), merged / "dev" / "pts")
  _run(["mkdir", "-p", str(merged / "dev" / "shm")],
       sudo=True)
  _bind_mount(Path("/dev/shm"), merged / "dev" / "shm")
  # resolv.conf for DNS.
  resolv_dest = merged / "etc" / "resolv.conf"
  if not _is_mountpoint(resolv_dest):
    _bind_mount(
      Path("/etc/resolv.conf"), resolv_dest,
      readonly=True,
    )


def _real_home():
  """Get the real user's home, even under sudo."""
  user = os.environ.get("SUDO_USER")
  if user:
    import pwd
    return Path(pwd.getpwnam(user).pw_dir)
  return Path.home()


def _link_claude_md(workspace):
  """Copy chroot CLAUDE.md template into the chroot.

  Uses templates/chroot_claude.md from takt. Falls back
  to the host's ~/.claude/CLAUDE.md if template missing.
  """
  merged = _merged_dir(workspace)
  dest_dir = merged / "home" / "worker" / ".claude"
  dest_dir.mkdir(parents=True, exist_ok=True)
  dest = dest_dir / "CLAUDE.md"
  if dest.is_symlink():
    dest.unlink()
  template = TEMPLATES_DIR / "chroot_claude.md"
  if template.exists():
    import shutil
    shutil.copy2(template, dest)
    return
  home = _real_home()
  src = home / ".claude" / "CLAUDE.md"
  if src.exists() and not dest.exists():
    dest.symlink_to(src)


def _mount_claude(workspace):
  """Bind-mount claude CLI binary and config into chroot."""
  home = _real_home()
  merged = _merged_dir(workspace)
  worker_home = merged / "home" / "worker"
  # Claude binary + versions.
  claude_data = home / ".local" / "share" / "claude"
  claude_bin = home / ".local" / "bin" / "claude"
  if claude_bin.exists():
    dest_bin = worker_home / ".local" / "bin" / "claude"
    _bind_mount(claude_bin, dest_bin, readonly=True)
  if claude_data.is_dir():
    dest_data = (
      worker_home / ".local" / "share" / "claude"
    )
    _bind_mount(claude_data, dest_data, readonly=True)
  # Claude config + OAuth credentials.
  claude_config = home / ".claude"
  if claude_config.is_dir():
    dest_config = worker_home / ".claude"
    _bind_mount(claude_config, dest_config)


def _mount_root_repos(workspace, repos):
  """Bind-mount root repos at their absolute paths."""
  merged = _merged_dir(workspace)
  for repo_name in repos:
    host_path = get_repo_path(repo_name)
    if not host_path.is_dir():
      log.warning("root repo %s not found", host_path)
      continue
    chroot_path = merged / host_path.relative_to("/")
    _bind_mount(host_path, chroot_path)


def _clone_workspace_repos(workspace, repos):
  """Clone workspace repos inside the chroot."""
  merged = _merged_dir(workspace)
  worker_home = merged / "home" / "worker"
  worker_home.mkdir(parents=True, exist_ok=True)
  for repo_name in repos:
    dest = worker_home / repo_name
    if dest.exists():
      log.debug("[skip] %s already cloned", repo_name)
      continue
    root_repo = get_repo_path(repo_name)
    # Inside the chroot, the root repo is at its absolute
    # host path (bind-mounted). Clone from there.
    chroot_origin = str(root_repo)
    _run([
      "chroot", str(merged),
      "su", "-l", "worker", "-c",
      f"git clone {chroot_origin} /home/worker/{repo_name}",
    ], sudo=True)
    # Create workspace branch (workspace name = branch name).
    _run([
      "chroot", str(merged),
      "su", "-l", "worker", "-c",
      f"git -C /home/worker/{repo_name}"
      f" checkout -b {workspace}",
    ], sudo=True)


def _setup_gitconfig(workspace):
  """Write git config for the worker user."""
  merged = _merged_dir(workspace)
  dest = merged / "home" / "worker" / ".gitconfig"
  if dest.exists():
    return
  dest.write_text(
    "[user]\n"
    "\tname = Karl Ruskowski\n"
    "\temail = karl.ruskowski@optris.de\n"
  )


def _setup_profile(workspace):
  """Set up worker shell profile (zsh + bash)."""
  merged = _merged_dir(workspace)
  content = (
    'export PATH="$HOME/.local/bin:$PATH"\n'
  )
  for name in [".zprofile", ".profile"]:
    path = merged / "home" / "worker" / name
    if path.exists():
      continue
    _run(["tee", str(path)], sudo=True,
         input=content, capture_output=True, text=True)
    _run(["chown", "1000:1000", str(path)],
         sudo=True)


def create_chroot(workspace, repos):
  """Create overlayfs chroot for a workspace.

  Sets up overlay dirs, mounts overlay + system dirs,
  bind-mounts root repos, clones workspace repos inside,
  and configures git for the worker user.

  Args:
    workspace: Workspace name.
    repos: List of repo names.

  Raises:
    FileNotFoundError: If base rootfs doesn't exist.
    FileExistsError: If chroot already exists.
    subprocess.CalledProcessError: On mount/clone failure.
  """
  if not CHROOT_BASE_DIR.is_dir():
    raise FileNotFoundError(
      f"Base rootfs not found at {CHROOT_BASE_DIR}. "
      f"Run: sudo python3 bin/setup_chroot_base.py"
    )
  if chroot_exists(workspace):
    raise FileExistsError(
      f"Chroot already exists for '{workspace}'."
    )
  log.info("Creating chroot for '%s'...", workspace)
  # Create overlay dirs.
  for d in [_upper_dir(workspace), _work_dir(workspace),
            _merged_dir(workspace)]:
    d.mkdir(parents=True, exist_ok=True)
  # Mount overlay.
  _mount_overlay(workspace)
  # Mount system dirs.
  _mount_system_dirs(workspace)
  # Bind-mount root repos.
  _mount_root_repos(workspace, repos)
  # Clone workspace repos.
  _clone_workspace_repos(workspace, repos)
  # Set up git config and shell profile.
  _setup_gitconfig(workspace)
  _setup_profile(workspace)
  # Symlink global CLAUDE.md into worker's .claude dir.
  _link_claude_md(workspace)
  # Mount claude CLI + config.
  _mount_claude(workspace)
  log.info("Chroot ready: %s", _merged_dir(workspace))


def mount_chroot(workspace):
  """Mount overlay + binds for an existing chroot.

  Idempotent — skips already-mounted paths.

  Args:
    workspace: Workspace name.

  Raises:
    FileNotFoundError: If chroot doesn't exist.
  """
  if not chroot_exists(workspace):
    raise FileNotFoundError(
      f"No chroot for '{workspace}'."
    )
  _mount_overlay(workspace)
  _mount_system_dirs(workspace)
  # Re-mount root repos (discover from worker home).
  merged = _merged_dir(workspace)
  worker_home = merged / "home" / "worker"
  if worker_home.is_dir():
    repos = [
      d.name for d in worker_home.iterdir()
      if d.is_dir() and (d / ".git").exists()
    ]
    _mount_root_repos(workspace, repos)
  _mount_claude(workspace)


def _get_mount_list(workspace):
  """Get list of mount points under the chroot, sorted deepest first."""
  merged = _merged_dir(workspace)
  prefix = str(merged)
  result = subprocess.run(
    ["findmnt", "-rn", "-o", "TARGET"],
    capture_output=True, text=True,
  )
  mounts = []
  for line in result.stdout.splitlines():
    target = line.strip()
    if target.startswith(prefix):
      mounts.append(target)
  # Sort deepest first for safe unmounting.
  mounts.sort(key=lambda p: p.count("/"), reverse=True)
  return mounts


def unmount_chroot(workspace):
  """Unmount all bind mounts + overlay.

  Unmounts in reverse depth order (deepest first).

  Args:
    workspace: Workspace name.
  """
  if not chroot_exists(workspace):
    return
  mounts = _get_mount_list(workspace)
  for mount_path in mounts:
    log.debug("Unmounting %s", mount_path)
    _run(["umount", "-l", mount_path],
         sudo=True, check=False)


def enter_chroot(workspace, cmd=None):
  """Run a command inside the chroot.

  Defaults to interactive shell if no command given.

  Args:
    workspace: Workspace name.
    cmd: Optional command string to run.

  Returns:
    subprocess.CompletedProcess result.

  Raises:
    FileNotFoundError: If chroot doesn't exist.
  """
  if not chroot_exists(workspace):
    raise FileNotFoundError(
      f"No chroot for '{workspace}'."
    )
  if not is_mounted(workspace):
    mount_chroot(workspace)
  merged = _merged_dir(workspace)
  if cmd:
    shell_cmd = cmd
    # Auto-add permission bypass for claude CLI.
    parts = cmd.strip().split()
    if parts and parts[0] == "claude":
      if "--dangerously-skip-permissions" not in parts:
        shell_cmd = (
          cmd.strip()
          + " --dangerously-skip-permissions"
        )
  else:
    shell_cmd = "exec zsh -l"
  env = dict(os.environ)
  # Pass API key into chroot if available.
  api_key = os.environ.get("ANTHROPIC_API_KEY", "")
  full_cmd = [
    "chroot", str(merged),
    "su", "-l", "worker", "-c", shell_cmd,
  ]
  env_args = {}
  if api_key:
    env["ANTHROPIC_API_KEY"] = api_key
    env_args["env"] = env
  return _run(full_cmd, sudo=True, check=False,
              **env_args)


def add_repo(workspace, repo):
  """Clone an additional repo into a mounted chroot.

  Args:
    workspace: Workspace name.
    repo: Repo name.

  Raises:
    FileNotFoundError: If chroot doesn't exist.
  """
  if not chroot_exists(workspace):
    raise FileNotFoundError(
      f"No chroot for '{workspace}'."
    )
  if not is_mounted(workspace):
    mount_chroot(workspace)
  # Bind-mount root repo.
  _mount_root_repos(workspace, [repo])
  # Clone into worker home.
  _clone_workspace_repos(workspace, [repo])


def remove_repo(workspace, repo):
  """Remove a repo from the chroot.

  Args:
    workspace: Workspace name.
    repo: Repo name.
  """
  if not chroot_exists(workspace):
    return
  merged = _merged_dir(workspace)
  repo_dir = merged / "home" / "worker" / repo
  if repo_dir.exists():
    shutil.rmtree(repo_dir)
  # Unmount root repo bind mount if active.
  host_path = get_repo_path(repo)
  chroot_path = merged / host_path.relative_to("/")
  if _is_mountpoint(chroot_path):
    _run(["umount", "-l", str(chroot_path)],
         sudo=True, check=False)


def delete_chroot(workspace):
  """Unmount everything and delete chroot dirs.

  Args:
    workspace: Workspace name.
  """
  if not chroot_exists(workspace):
    return
  log.info("Deleting chroot for '%s'...", workspace)
  unmount_chroot(workspace)
  chroot_dir = _chroot_dir(workspace)
  _run(["rm", "-rf", str(chroot_dir)], sudo=True)
  log.info("Chroot deleted: %s", chroot_dir)
