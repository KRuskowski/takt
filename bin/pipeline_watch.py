#!/usr/bin/env python3
"""Pipeline watcher — detect branch changes and trigger analysis.

Polls root repos for branch ref changes. When changes are detected,
gathers diffs and logs, then pipes context to Claude CLI for
analysis.
"""

import argparse
import glob
import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib.config import (
  STAGES_DIR,
  STATE_DIR,
  WORKSPACES_DIR,
  get_default_branch,
  get_repo_path,
  load_repos_config,
  validate_repo,
)
from lib.workspace_ops import list_workspaces
from lib.git_utils import (
  GitError,
  get_branch_ref,
  get_branches,
  get_log,
)

REFS_FILE = STATE_DIR / "branch_refs.json"
EVENTS_FILE = STATE_DIR / "events.json"
DEFAULT_INTERVAL = 30
MAX_EVENTS = 200
KITTY_SOCKET_PATH = "/tmp/kitty-pipeline"

# Muted background tones for kitty tab color-coding by role.
ROLE_COLORS = {
  "feature": "#2d5a27",
  "test": "#5a4b27",
  "review": "#27455a",
  "docs": "#3d275a",
  "refactor": "#275a5a",
  "pr": "#5a2727",
  "deploy_qa": "#5a4027",
  "sync": "#3a3a5a",
}


def _find_kitty_socket():
  """Find the kitty remote control socket.

  Checks for the exact path first (CLI --listen-on), then
  falls back to a glob for the -{pid} variant (kitty.conf
  listen_on).

  Returns:
    Socket address string (e.g. "unix:/tmp/kitty-pipeline")
    or None if no socket is found.
  """
  exact = Path(KITTY_SOCKET_PATH)
  if exact.exists():
    return f"unix:{exact}"
  matches = sorted(glob.glob(f"{KITTY_SOCKET_PATH}-*"))
  if matches:
    return f"unix:{matches[-1]}"
  return None


def load_refs():
  """Load stored branch refs from state file."""
  if REFS_FILE.exists():
    with open(REFS_FILE) as f:
      return json.load(f)
  return {}


def save_refs(refs):
  """Save branch refs to state file."""
  STATE_DIR.mkdir(exist_ok=True)
  with open(REFS_FILE, "w") as f:
    json.dump(refs, f, indent=2)


def load_events():
  """Load persisted pipeline events from disk.

  Returns:
    List of event dicts (newest first), up to MAX_EVENTS.
  """
  if not EVENTS_FILE.exists():
    return []
  try:
    with open(EVENTS_FILE) as f:
      entries = json.load(f)
    if not isinstance(entries, list):
      return []
    return entries[:MAX_EVENTS]
  except (json.JSONDecodeError, ValueError, OSError):
    return []


def log_events(events):
  """Append events to persistent rolling log.

  Timestamps each event and caps the file at MAX_EVENTS.

  Args:
    events: List of event dicts to append.
  """
  if not events:
    return
  existing = load_events()
  ts = time.strftime("%H:%M:%S")
  for ev in events:
    if "time" not in ev:
      ev["time"] = ts
  new_entries = events + existing
  new_entries = new_entries[:MAX_EVENTS]
  STATE_DIR.mkdir(exist_ok=True)
  with open(EVENTS_FILE, "w") as f:
    json.dump(new_entries, f, indent=2)


def snapshot_all_refs(repos_config):
  """Snapshot current branch refs for all managed repos.

  Returns:
    Dict mapping "repo:branch" -> commit hash.
  """
  refs = {}
  repos = repos_config.get("repos", {})
  for repo_name, cfg in repos.items():
    repo_path = get_repo_path(cfg.get("path", repo_name))
    if not validate_repo(cfg.get("path", repo_name)):
      continue
    try:
      branches = get_branches(repo_path)
    except GitError:
      continue
    for branch in branches:
      try:
        ref = get_branch_ref(repo_path, branch)
        refs[f"{repo_name}:{branch}"] = ref
      except GitError:
        continue
  return refs


def find_changes(old_refs, new_refs):
  """Compare old and new refs, return changes.

  Returns:
    List of dicts with keys: repo, branch, old_ref, new_ref, type.
    type is one of: "new", "updated", "deleted".
  """
  changes = []
  all_keys = set(old_refs.keys()) | set(new_refs.keys())
  for key in sorted(all_keys):
    repo, branch = key.split(":", 1)
    old = old_refs.get(key)
    new = new_refs.get(key)
    if old is None and new is not None:
      changes.append({
        "repo": repo, "branch": branch,
        "old_ref": None, "new_ref": new, "type": "new",
      })
    elif old is not None and new is None:
      changes.append({
        "repo": repo, "branch": branch,
        "old_ref": old, "new_ref": None, "type": "deleted",
      })
    elif old != new:
      changes.append({
        "repo": repo, "branch": branch,
        "old_ref": old, "new_ref": new, "type": "updated",
      })
  return changes


def group_by_branch(changes):
  """Group changes by branch name.

  Returns:
    Dict mapping branch_name -> list of change dicts.
  """
  groups = defaultdict(list)
  for change in changes:
    groups[change["branch"]].append(change)
  return dict(groups)


def scan_markers():
  """Walk stages directory for .pipeline-push marker files.

  Returns:
    Dict mapping (ws, role) to list of (repo_name, lines) tuples.
  """
  markers = defaultdict(list)
  if not STAGES_DIR.exists():
    return markers
  for ws_dir in sorted(STAGES_DIR.iterdir()):
    if not ws_dir.is_dir():
      continue
    for role_dir in sorted(ws_dir.iterdir()):
      if not role_dir.is_dir():
        continue
      for repo_dir in sorted(role_dir.iterdir()):
        if not repo_dir.is_dir():
          continue
        marker = repo_dir / ".pipeline-push"
        if not marker.exists():
          continue
        lines = marker.read_text().strip().splitlines()
        if lines:
          markers[(ws_dir.name, role_dir.name)].append(
            (repo_dir.name, lines)
          )
  return dict(markers)


def build_trigger_prompt(ws, role, repo_markers):
  """Build a prompt describing incoming changes for a stage.

  Args:
    ws: Workspace name (= branch name).
    role: Stage role.
    repo_markers: List of (repo_name, marker_lines) tuples.

  Returns:
    Prompt string.
  """
  parts = [f"Incoming changes on branch `{ws}`:\n"]
  for repo, lines in repo_markers:
    parts.append(f"## {repo}")
    stage_repo = STAGES_DIR / ws / role / repo
    # Use first non-zero old ref and last new ref to cover
    # all pushes in one log range.
    first_old = None
    last_new = None
    for line in lines:
      tokens = line.split(None, 3)
      if len(tokens) < 4:
        continue
      old_ref, new_ref = tokens[1], tokens[2]
      if first_old is None and old_ref != "0" * 40:
        first_old = old_ref
      last_new = new_ref
    if last_new is None:
      continue
    if first_old is None:
      parts.append(f"New branch created at {last_new[:8]}")
    else:
      try:
        log = get_log(
          stage_repo, base=first_old, head=last_new,
        )
        if log:
          parts.append(f"```\n{log}\n```")
        else:
          parts.append("(no new commits)")
      except GitError:
        parts.append(
          f"{first_old[:8]}..{last_new[:8]}"
          " (log unavailable)"
        )
    parts.append("")
  parts.append("Process these changes according to your role.")
  parts.append("When done, push to origin.")
  return "\n".join(parts)


def _kitty_tab_exists(title):
  """Check if a kitty tab with the given title exists.

  Args:
    title: Tab title to search for.

  Returns:
    True if a tab with that title exists in any OS window.
  """
  socket = _find_kitty_socket()
  if socket is None:
    return False
  result = subprocess.run(
    ["kitten", "@", "--to", socket, "ls"],
    capture_output=True, text=True,
  )
  if result.returncode != 0:
    return False
  try:
    windows = json.loads(result.stdout)
  except (json.JSONDecodeError, ValueError):
    return False
  for os_window in windows:
    for tab in os_window.get("tabs", []):
      if tab.get("title") == title:
        return True
  return False


def _prune_finished_tabs():
  """Close kitty tabs whose agent process has finished.

  Returns:
    List of tab titles that were pruned.
  """
  socket = _find_kitty_socket()
  if socket is None:
    return []
  result = subprocess.run(
    ["kitten", "@", "--to", socket, "ls"],
    capture_output=True, text=True,
  )
  if result.returncode != 0:
    return []
  try:
    windows = json.loads(result.stdout)
  except (json.JSONDecodeError, ValueError):
    return []
  pruned = []
  for os_window in windows:
    for tab in os_window.get("tabs", []):
      title = tab.get("title", "")
      if "/" not in title:
        continue
      ws, role = title.split("/", 1)
      if role == "sync":
        done = WORKSPACES_DIR / ws / ".agent-done"
      else:
        done = STAGES_DIR / ws / role / ".agent-done"
      if not done.exists():
        continue
      subprocess.run(
        [
          "kitten", "@", "--to", socket,
          "close-tab", "-m", f"title:{title}",
        ],
        capture_output=True, text=True,
      )
      done.unlink(missing_ok=True)
      pruned.append(title)
  return pruned


def launch_in_kitty(ws, role, stage_dir, prompt):
  """Launch a claude agent in a kitty tab.

  Skips if a tab with the same title already exists.

  Args:
    ws: Workspace name.
    role: Stage role.
    stage_dir: Path to the stage directory.
    prompt: Trigger prompt for the agent.

  Raises:
    RuntimeError: If the kitty launch command fails or no
      socket is found.
  """
  title = f"{ws}/{role}"
  if _kitty_tab_exists(title):
    print(f"  Tab '{title}' already exists, skipping.")
    return
  socket = _find_kitty_socket()
  if socket is None:
    raise RuntimeError(
      "no kitty socket found at"
      f" {KITTY_SOCKET_PATH}"
    )
  # Remove stale done marker from a previous run.
  done_marker = stage_dir / ".agent-done"
  done_marker.unlink(missing_ok=True)
  # Use bash -i so shell aliases (e.g. claude) are loaded.
  # Single quotes inside prompt are escaped for the shell.
  # Append touch so a marker is written when claude exits.
  escaped = prompt.replace("'", "'\\''")
  shell_cmd = (
    f"unset CLAUDECODE; claude --model sonnet '{escaped}';"
    f" touch '{done_marker}'"
  )
  result = subprocess.run(
    [
      "kitten", "@", "--to", socket,
      "launch", "--type", "tab",
      "--tab-title", title,
      "--cwd", str(stage_dir),
      "--hold",
      "zsh", "-ic", shell_cmd,
    ],
    capture_output=True, text=True,
  )
  if result.returncode != 0:
    raise RuntimeError(
      f"kitty launch failed: {result.stderr.strip()}"
    )
  print(f"  Launched agent in kitty tab '{title}'.")
  # Color-code the tab by role (fire-and-forget).
  color = ROLE_COLORS.get(role)
  if color:
    subprocess.run(
      [
        "kitten", "@", "--to", socket,
        "set-tab-color",
        "-m", f"title:{title}",
        f"active_bg={color}",
        f"inactive_bg={color}",
      ],
      capture_output=True, text=True,
    )
  # Auto-focus the newly launched tab.
  subprocess.run(
    [
      "kitten", "@", "--to", socket,
      "focus-tab",
      "-m", f"title:{title}",
    ],
    capture_output=True, text=True,
  )


def _scan_and_trigger():
  """Scan for pipeline push markers and trigger stage agents.

  Returns:
    List of event dicts with keys: stage, repos, event.
    event is one of: "triggered", "error".
  """
  markers = scan_markers()
  if not markers:
    return []
  events = []
  for (ws, role), repo_markers in markers.items():
    repos = [r for r, _ in repo_markers]
    title = f"{ws}/{role}"
    if _kitty_tab_exists(title):
      events.append({
        "stage": title,
        "repos": ", ".join(repos),
        "event": "skipped",
      })
      continue
    stage_dir = STAGES_DIR / ws / role
    prompt = build_trigger_prompt(ws, role, repo_markers)
    try:
      launch_in_kitty(ws, role, stage_dir, prompt)
      # Delete markers only after successful launch.
      for repo, _ in repo_markers:
        marker = (
          STAGES_DIR / ws / role / repo / ".pipeline-push"
        )
        marker.unlink(missing_ok=True)
      event_type = "triggered"
    except RuntimeError:
      event_type = "error"
    events.append({
      "stage": title,
      "repos": ", ".join(repos),
      "event": event_type,
    })
  return events


def write_sync_markers(changes, repos_config):
  """Write upstream sync markers for workspaces affected by changes.

  For each default-branch change, finds active workspaces that
  contain the changed repo and writes a marker line to
  WORKSPACES_DIR/<ws>/<repo>/.upstream-sync.

  Args:
    changes: List of change dicts (from find_changes) on
      default branches. Only "new" and "updated" are processed.
    repos_config: Full repos config dict.
  """
  workspaces = list_workspaces()
  for c in changes:
    if c["type"] == "deleted":
      continue
    repo_name = c["repo"]
    for ws in workspaces:
      if repo_name not in ws["repos"]:
        continue
      if ws["branch"] == c["branch"]:
        continue
      marker = (
        WORKSPACES_DIR / ws["name"] / repo_name
        / ".upstream-sync"
      )
      ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
      old = c["old_ref"] or ("0" * 40)
      new = c["new_ref"] or ("0" * 40)
      line = f"{ts} {old} {new} refs/heads/{c['branch']}\n"
      with open(marker, "a") as f:
        f.write(line)


def scan_sync_markers():
  """Walk workspaces for .upstream-sync marker files.

  Returns:
    Dict mapping workspace name to list of (repo_name, lines)
    tuples.
  """
  markers = defaultdict(list)
  if not WORKSPACES_DIR.exists():
    return markers
  for ws_dir in sorted(WORKSPACES_DIR.iterdir()):
    if not ws_dir.is_dir():
      continue
    for repo_dir in sorted(ws_dir.iterdir()):
      if not repo_dir.is_dir():
        continue
      marker = repo_dir / ".upstream-sync"
      if not marker.exists():
        continue
      lines = marker.read_text().strip().splitlines()
      if lines:
        markers[ws_dir.name].append((repo_dir.name, lines))
  return dict(markers)


def build_sync_prompt(ws, repo_markers):
  """Build a prompt for an upstream sync agent.

  Args:
    ws: Workspace name (= branch name).
    repo_markers: List of (repo_name, marker_lines) tuples.

  Returns:
    Prompt string.
  """
  parts = [
    "IMPORTANT: Ignore the CLAUDE.md in this directory — it is"
    " for the workspace agent, not you. You are a **sync"
    " agent**. Follow only the instructions in this prompt.\n",
    f"Upstream changes detected for workspace `{ws}`.\n",
    "Merge the following upstream changes into your branch"
    " and push. Do NOT modify code beyond resolving merge"
    " conflicts. Do not update session state in CLAUDE.md.\n",
  ]
  for repo, lines in repo_markers:
    parts.append(f"## {repo}")
    ws_repo = WORKSPACES_DIR / ws / repo
    # Extract default branch from the ref line.
    default_br = None
    first_old = None
    last_new = None
    for line in lines:
      tokens = line.split(None, 3)
      if len(tokens) < 4:
        continue
      old_ref, new_ref, ref_path = tokens[1], tokens[2], tokens[3]
      # refs/heads/<branch> -> branch name
      default_br = ref_path.rsplit("/", 1)[-1]
      if first_old is None and old_ref != "0" * 40:
        first_old = old_ref
      last_new = new_ref
    if default_br is None:
      default_br = "main"
    if last_new is None:
      continue
    if first_old and first_old != "0" * 40:
      try:
        log = get_log(
          ws_repo, base=first_old, head=last_new,
        )
        if log:
          parts.append(f"```\n{log}\n```")
        else:
          parts.append("(no new commits)")
      except GitError:
        parts.append(
          f"{first_old[:8]}..{last_new[:8]}"
          " (log unavailable)"
        )
    else:
      parts.append(f"New upstream at {last_new[:8]}")
    parts.append("")
  # Collect unique default branches from markers.
  default_branches = set()
  for _, lines in repo_markers:
    for line in lines:
      tokens = line.split(None, 3)
      if len(tokens) >= 4:
        default_branches.add(
          tokens[3].rsplit("/", 1)[-1]
        )
  br = next(iter(default_branches), "main")
  parts.append(
    "Important: origin points to the first pipeline stage,"
    " not the root repo. Fetch upstream from the root repo"
    " path directly.\n"
  )
  parts.append("Steps:")
  repo_names = [r for r, _ in repo_markers]
  for repo in repo_names:
    root = f"~/dev/root/{repo}"
    parts.append(
      f"1. `git -C {repo} fetch {root} {br}`"
    )
    parts.append(
      f"2. `git -C {repo} merge FETCH_HEAD`"
    )
  parts.append(
    "3. Resolve conflicts or stop if unresolvable."
  )
  parts.append(
    f"4. Push all repos: `git push origin {ws}`\n"
    "   This propagates through the pipeline stages"
    " to root."
  )
  return "\n".join(parts)


def _scan_and_sync():
  """Scan for upstream sync markers and launch sync agents.

  Returns:
    List of event dicts with keys: stage, repos, event.
    event is one of: "triggered", "skipped", "error".
  """
  markers = scan_sync_markers()
  if not markers:
    return []
  events = []
  for ws, repo_markers in markers.items():
    repos = [r for r, _ in repo_markers]
    title = f"{ws}/sync"
    if _kitty_tab_exists(title):
      events.append({
        "stage": title,
        "repos": ", ".join(repos),
        "event": "skipped",
      })
      continue
    prompt = build_sync_prompt(ws, repo_markers)
    ws_dir = WORKSPACES_DIR / ws
    try:
      launch_in_kitty(ws, "sync", ws_dir, prompt)
      # Delete markers only after successful launch.
      for repo, _ in repo_markers:
        marker = (
          WORKSPACES_DIR / ws / repo / ".upstream-sync"
        )
        marker.unlink(missing_ok=True)
      event_type = "triggered"
    except RuntimeError:
      event_type = "error"
    events.append({
      "stage": title,
      "repos": ", ".join(repos),
      "event": event_type,
    })
  return events


def _retrigger_pr_stage(ws):
  """Write pipeline-push markers to re-trigger a PR stage.

  Called after a sync tab is pruned. If a PR stage exists for
  the workspace, writes empty .pipeline-push markers in each
  repo so the next _scan_and_trigger() cycle picks them up.

  Args:
    ws: Workspace name.

  Returns:
    List of event dicts for any re-triggered PR stages.
  """
  pr_dir = STAGES_DIR / ws / "pr"
  if not pr_dir.is_dir():
    return []
  events = []
  repos = []
  for repo_dir in sorted(pr_dir.iterdir()):
    if not repo_dir.is_dir():
      continue
    marker = repo_dir / ".pipeline-push"
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    marker.write_text(
      f"{ts} 0000000000000000000000000000000000000000"
      f" 0000000000000000000000000000000000000000"
      f" refs/heads/{ws}\n"
    )
    repos.append(repo_dir.name)
  if repos:
    events.append({
      "stage": f"{ws}/pr",
      "repos": ", ".join(repos),
      "event": "retrigger",
    })
    print(f"  Re-triggered PR stage for '{ws}'.")
  return events


def poll_once(repos_config):
  """Run a single poll cycle.

  Returns:
    List of event dicts from pruning, triggering, and syncing.
  """
  events = []
  for title in _prune_finished_tabs():
    events.append({
      "stage": title, "repos": "", "event": "pruned",
    })
    print(f"  Pruned finished tab '{title}'.")
  # Re-trigger PR stages for pruned sync tabs.
  for ev in events:
    if ev["event"] == "pruned":
      ws, role = ev["stage"].split("/", 1)
      if role == "sync":
        events.extend(_retrigger_pr_stage(ws))
  for ev in _scan_and_trigger():
    events.append(ev)
    print(f"  {ev['stage']}: {ev['repos']} [{ev['event']}]")
  for ev in _scan_and_sync():
    events.append(ev)
    print(f"  {ev['stage']}: {ev['repos']} [{ev['event']}]")

  old_refs = load_refs()
  new_refs = snapshot_all_refs(repos_config)

  if not old_refs:
    print("First run — snapshotting current branch refs.")
    save_refs(new_refs)
    print(f"Stored {len(new_refs)} branch refs.")
    log_events(events)
    return events

  changes = find_changes(old_refs, new_refs)
  if not changes:
    log_events(events)
    return events

  groups = group_by_branch(changes)
  print(f"\nDetected changes in {len(groups)} branch(es):")
  for branch, branch_changes in groups.items():
    repos_affected = [c["repo"] for c in branch_changes]
    print(f"  {branch}: {', '.join(repos_affected)}")

  default_changes = []
  repos = repos_config.get("repos", {})
  for branch, branch_changes in groups.items():
    for c in branch_changes:
      cfg = repos.get(c["repo"], {})
      repo_path = get_repo_path(cfg.get("path", c["repo"]))
      default_br = cfg.get(
        "default_branch", get_default_branch(repo_path),
      )
      if c["branch"] == default_br:
        default_changes.append(c)

  if default_changes:
    write_sync_markers(default_changes, repos_config)

  save_refs(new_refs)
  log_events(events)
  return events


def cmd_watch(args):
  """Main watch loop."""
  repos_config = load_repos_config()
  interval = args.interval

  if args.reset:
    if REFS_FILE.exists():
      REFS_FILE.unlink()
      print("Cleared stored branch refs.")
    return

  if args.once:
    poll_once(repos_config)
    return

  print(f"Watching for branch changes (interval: {interval}s)")
  print("Press Ctrl+C to stop.\n")

  try:
    while True:
      poll_once(repos_config)
      time.sleep(interval)
  except KeyboardInterrupt:
    print("\nStopped.")


def main():
  parser = argparse.ArgumentParser(
    description="Watch for branch changes across repos.",
  )
  parser.add_argument(
    "--interval", type=int, default=DEFAULT_INTERVAL,
    help=f"Poll interval in seconds (default: {DEFAULT_INTERVAL}).",
  )
  parser.add_argument(
    "--once", action="store_true",
    help="Run a single poll cycle and exit.",
  )
  parser.add_argument(
    "--reset", action="store_true",
    help="Clear stored branch refs and exit.",
  )
  args = parser.parse_args()
  cmd_watch(args)


if __name__ == "__main__":
  main()
