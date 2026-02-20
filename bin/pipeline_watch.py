#!/usr/bin/env python3
"""Pipeline watcher — detect branch changes and trigger analysis.

Polls root repos for branch ref changes. When changes are detected,
gathers diffs and logs, then pipes context to Claude CLI for
analysis.
"""

import argparse
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
  STATE_DIR,
  get_default_branch,
  get_repo_path,
  load_repos_config,
  validate_repo,
)
from lib.git_utils import (
  GitError,
  get_branch_ref,
  get_branches,
  get_diff,
  get_log,
)

REFS_FILE = STATE_DIR / "branch_refs.json"
DEFAULT_INTERVAL = 30
DEFAULT_MAX_DIFF_LINES = 500


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


def build_context(branch, changes, repos_config,
                  max_diff_lines=DEFAULT_MAX_DIFF_LINES):
  """Build a context packet for a set of branch changes.

  Returns:
    A string with diffs, logs, and metadata for all repos in
    the branch group.
  """
  repos = repos_config.get("repos", {})
  parts = [
    f"# Branch Change Report: {branch}",
    f"\n{len(changes)} repo(s) affected.\n",
  ]

  for change in changes:
    repo_name = change["repo"]
    cfg = repos.get(repo_name, {})
    repo_path = get_repo_path(cfg.get("path", repo_name))
    parts.append(f"## {repo_name} ({change['type']})")

    if change["type"] == "deleted":
      parts.append("Branch was deleted.\n")
      continue

    old_ref = change.get("old_ref")
    new_ref = change.get("new_ref")

    # Log.
    if old_ref and new_ref:
      try:
        log = get_log(repo_path, base=old_ref, head=new_ref)
        if log:
          parts.append(f"### Commits\n```\n{log}\n```\n")
      except GitError:
        parts.append("(could not retrieve log)\n")

    # Diff.
    if old_ref and new_ref:
      try:
        diff = get_diff(
          repo_path, base=old_ref, head=new_ref,
          max_lines=max_diff_lines,
        )
        if diff:
          parts.append(f"### Diff\n```diff\n{diff}\n```\n")
      except GitError:
        parts.append("(could not retrieve diff)\n")
    elif change["type"] == "new":
      # New branch — diff against default branch.
      default_br = cfg.get(
        "default_branch", get_default_branch(repo_path),
      )
      try:
        diff = get_diff(
          repo_path, base=default_br, head=change["branch"],
          max_lines=max_diff_lines,
        )
        if diff:
          parts.append(f"### Diff (vs {default_br})\n")
          parts.append(f"```diff\n{diff}\n```\n")
      except GitError:
        parts.append("(could not retrieve diff)\n")

    # Read repo CLAUDE.md if present.
    claude_md = repo_path / "CLAUDE.md"
    if claude_md.exists():
      content = claude_md.read_text()
      parts.append(f"### CLAUDE.md\n```\n{content}\n```\n")

  return "\n".join(parts)


def pipe_to_claude(context, branch):
  """Pipe context to Claude CLI for analysis.

  Uses stdin to avoid shell argument length limits.
  """
  system_prompt = (
    "You are analyzing cross-repo branch changes in a multi-repo "
    "pipeline. The user will show you a change report. Analyze the "
    "changes and suggest:\n"
    "1. What pipeline stages are needed "
    "(feature/test/review/docs/deploy_qa)\n"
    "2. What order to run them\n"
    "3. Any risks or concerns\n"
    "4. A summary of what changed and why\n"
    "Be concise and actionable."
  )

  prompt = (
    f"Analyze these changes on branch '{branch}' and suggest "
    f"next pipeline steps:\n\n{context}"
  )

  cmd = [
    "claude", "--print",
    "--system-prompt", system_prompt,
  ]

  print(f"\nPiping to Claude CLI for analysis...")
  try:
    result = subprocess.run(
      cmd, input=prompt, capture_output=True, text=True,
    )
    if result.returncode == 0:
      print(f"\n--- Claude Analysis for '{branch}' ---")
      print(result.stdout)
      print("--- End Analysis ---\n")
    else:
      print(f"Claude CLI error: {result.stderr}")
  except FileNotFoundError:
    print("Error: 'claude' CLI not found in PATH.")
    print("Dumping context instead:\n")
    print(context)


def poll_once(repos_config, max_diff_lines=DEFAULT_MAX_DIFF_LINES):
  """Run a single poll cycle.

  Returns:
    True if changes were found and processed.
  """
  old_refs = load_refs()
  new_refs = snapshot_all_refs(repos_config)

  if not old_refs:
    print("First run — snapshotting current branch refs.")
    save_refs(new_refs)
    print(f"Stored {len(new_refs)} branch refs.")
    return False

  changes = find_changes(old_refs, new_refs)
  if not changes:
    return False

  groups = group_by_branch(changes)
  print(f"\nDetected changes in {len(groups)} branch(es):")
  for branch, branch_changes in groups.items():
    repos_affected = [c["repo"] for c in branch_changes]
    print(f"  {branch}: {', '.join(repos_affected)}")

  for branch, branch_changes in groups.items():
    # Filter out default branch changes (those are pulls, not
    # pipeline work).
    non_default = []
    repos = repos_config.get("repos", {})
    for c in branch_changes:
      cfg = repos.get(c["repo"], {})
      repo_path = get_repo_path(cfg.get("path", c["repo"]))
      default_br = cfg.get(
        "default_branch", get_default_branch(repo_path),
      )
      if c["branch"] != default_br:
        non_default.append(c)

    if not non_default:
      continue

    context = build_context(
      branch, non_default, repos_config,
      max_diff_lines=max_diff_lines,
    )

    resp = input(
      f"\nAnalyze changes on '{branch}'? [y/N/q] "
    ).lower()
    if resp == "q":
      save_refs(new_refs)
      return True
    if resp == "y":
      pipe_to_claude(context, branch)

  save_refs(new_refs)
  return True


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
    poll_once(repos_config, max_diff_lines=args.max_diff)
    return

  print(f"Watching for branch changes (interval: {interval}s)")
  print("Press Ctrl+C to stop.\n")

  try:
    while True:
      poll_once(repos_config, max_diff_lines=args.max_diff)
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
  parser.add_argument(
    "--max-diff", type=int, default=DEFAULT_MAX_DIFF_LINES,
    help="Max diff lines per repo "
    f"(default: {DEFAULT_MAX_DIFF_LINES}).",
  )
  args = parser.parse_args()
  cmd_watch(args)


if __name__ == "__main__":
  main()
