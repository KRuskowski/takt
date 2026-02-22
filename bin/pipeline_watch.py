#!/usr/bin/env python3
"""Pipeline watcher — reusable functions for branch change
detection, marker scanning, and prompt building.

Agent execution is handled by takt-service. This module
provides the low-level functions used by the service.
The --once flag runs a direct poll cycle; continuous mode
is deprecated in favor of takt-service.
"""

import argparse
import json
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
from lib.git_utils import (
  GitError,
  get_branch_ref,
  get_branches,
  get_log,
)
from lib.notify import notify
from lib.run_log import (
  finish_run,
  get_active_run,
  start_run,
  update_stage,
)
from lib.workspace_ops import (
  get_pipeline_stages,
  list_workspaces,
)

REFS_FILE = STATE_DIR / "branch_refs.json"
EVENTS_FILE = STATE_DIR / "events.json"
DEFAULT_INTERVAL = 30
MAX_EVENTS = 200


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
    List of dicts with keys: repo, branch, old_ref,
    new_ref, type. type is one of: "new", "updated",
    "deleted".
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
        "old_ref": old, "new_ref": None,
        "type": "deleted",
      })
    elif old != new:
      changes.append({
        "repo": repo, "branch": branch,
        "old_ref": old, "new_ref": new,
        "type": "updated",
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
    Dict mapping (ws, role) to list of (repo_name, lines)
    tuples.
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
      parts.append(
        f"New branch created at {last_new[:8]}"
      )
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
  parts.append(
    "Process these changes according to your role."
  )
  parts.append("When done, push to origin.")
  return "\n".join(parts)


def _detect_stage_result(ws, role):
  """Detect whether a finished stage passed or failed.

  Non-terminal stages pass if .pipeline-push markers exist
  in the next stage's repo dirs (meaning the agent pushed).
  Terminal stages (last in pipeline) pass on completion.

  Args:
    ws: Workspace name.
    role: Stage role slug.

  Returns:
    'passed', 'failed', or None (for sync agents).
  """
  stages = get_pipeline_stages(ws)
  if role not in stages:
    return None
  idx = stages.index(role)
  if idx + 1 >= len(stages):
    return "passed"
  next_role = stages[idx + 1]
  next_dir = STAGES_DIR / ws / next_role
  if not next_dir.is_dir():
    return "failed"
  for repo_dir in next_dir.iterdir():
    if not repo_dir.is_dir():
      continue
    if (repo_dir / ".pipeline-push").exists():
      return "passed"
  return "failed"


def _maybe_finish_run(ws):
  """Finish the active run if all stages are terminal.

  Sends a notification on completion.

  Args:
    ws: Workspace name.

  Returns:
    Completed run dict, or None.
  """
  run = finish_run(ws)
  if run is None:
    return None
  status = run["status"]
  if status == "passed":
    notify(
      f"Pipeline passed: {ws}",
      "All stages completed successfully.",
    )
  else:
    failed = [
      r for r, s in run["stages"].items()
      if s["status"] == "failed"
    ]
    notify(
      f"Pipeline failed: {ws}",
      f"Failed stages: {', '.join(failed)}",
      urgency="critical",
    )
  return run


def write_sync_markers(changes, repos_config):
  """Write upstream sync markers for affected workspaces.

  For each default-branch change, finds active workspaces
  that contain the changed repo and writes a marker line
  to WORKSPACES_DIR/<ws>/<repo>/.upstream-sync.

  Args:
    changes: List of change dicts (from find_changes) on
      default branches.
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
      line = (
        f"{ts} {old} {new} refs/heads/{c['branch']}\n"
      )
      with open(marker, "a") as f:
        f.write(line)


def scan_sync_markers():
  """Walk workspaces for .upstream-sync marker files.

  Returns:
    Dict mapping workspace name to list of
    (repo_name, lines) tuples.
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
        markers[ws_dir.name].append(
          (repo_dir.name, lines)
        )
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
    "IMPORTANT: Ignore the CLAUDE.md in this directory"
    " — it is for the workspace agent, not you. You are"
    " a **sync agent**. Follow only the instructions in"
    " this prompt.\n",
    f"Upstream changes detected for workspace `{ws}`.\n",
    "Merge the following upstream changes into your"
    " branch and push. Do NOT modify code beyond"
    " resolving merge conflicts. Do not update session"
    " state in CLAUDE.md.\n",
  ]
  for repo, lines in repo_markers:
    parts.append(f"## {repo}")
    ws_repo = WORKSPACES_DIR / ws / repo
    default_br = None
    first_old = None
    last_new = None
    for line in lines:
      tokens = line.split(None, 3)
      if len(tokens) < 4:
        continue
      old_ref, new_ref = tokens[1], tokens[2]
      ref_path = tokens[3]
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
    "Important: origin points to the first pipeline"
    " stage, not the root repo. Fetch upstream from"
    " the root repo path directly.\n"
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


def _retrigger_pr_stage(ws):
  """Write pipeline-push markers to re-trigger a PR stage.

  Called after a sync agent completes. If a PR stage exists
  for the workspace, writes .pipeline-push markers in each
  repo so the next poll cycle triggers it.

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
  return events


def cmd_watch(args):
  """CLI entry point for pipeline_watch."""
  if args.reset:
    if REFS_FILE.exists():
      REFS_FILE.unlink()
      print("Cleared stored branch refs.")
    return

  if args.once:
    repos_config = load_repos_config()
    old_refs = load_refs()
    new_refs = snapshot_all_refs(repos_config)
    if not old_refs:
      print("First run — snapshotting current branch refs.")
      save_refs(new_refs)
      print(f"Stored {len(new_refs)} branch refs.")
      return
    changes = find_changes(old_refs, new_refs)
    if changes:
      groups = group_by_branch(changes)
      print(
        f"Detected changes in {len(groups)} branch(es):"
      )
      for branch, branch_changes in groups.items():
        repos_affected = [
          c["repo"] for c in branch_changes
        ]
        print(f"  {branch}: {', '.join(repos_affected)}")
      repos = repos_config.get("repos", {})
      default_changes = []
      for branch, branch_changes in groups.items():
        for c in branch_changes:
          cfg = repos.get(c["repo"], {})
          repo_path = get_repo_path(
            cfg.get("path", c["repo"])
          )
          default_br = cfg.get(
            "default_branch",
            get_default_branch(repo_path),
          )
          if c["branch"] == default_br:
            default_changes.append(c)
      if default_changes:
        write_sync_markers(default_changes, repos_config)
    else:
      print("No changes detected.")
    save_refs(new_refs)
    return

  # Continuous mode — deprecated.
  print(
    "Continuous watching is deprecated. Use takt-service:\n"
    "  systemctl --user start takt-service\n"
    "  journalctl --user -u takt-service -f"
  )


def main():
  """Parse args and run."""
  parser = argparse.ArgumentParser(
    description="Pipeline watcher utility functions.",
  )
  parser.add_argument(
    "--interval", type=int, default=DEFAULT_INTERVAL,
    help=(
      "Poll interval in seconds "
      f"(default: {DEFAULT_INTERVAL}). Deprecated."
    ),
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
