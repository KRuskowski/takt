#!/usr/bin/env python3
"""Report and reclaim regenerable build output in workspaces.

Workspace clones are hardlinked against the root repos, so
the git data costs almost nothing. What accumulates is build
output that agents create and nothing ever removes. Across a
few hundred long-lived workspaces that dominates storage.

This only ever removes directories that are *provably*
regenerable, identified by a marker file that only a build
or cache directory has:

  CMakeCache.txt   a CMake binary dir. Only cmake writes it,
                   and it records its own source dir.
  CACHEDIR.TAG     the cache-directory standard. Cargo
                   writes it into target/.
  node_modules/    restored by the package manager.

Source is never a candidate: a directory without one of
those markers is not touched, and nothing inside a .git
directory is ever considered.

Dry run by default. --apply is required to delete anything.

Usage:
  bin/reclaim.py                        # survey everything
  bin/reclaim.py --older-than 30        # only idle 30+ days
  bin/reclaim.py --workspace NAME
  bin/reclaim.py --older-than 30 --apply
  bin/reclaim.py --json
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib.config import WORKSPACES_DIR
from lib.workspace_ops import list_workspaces

# A directory holding one of these is regenerable output.
# Keyed by marker, valued by what it identifies.
FILE_MARKERS = {
  "CMakeCache.txt": "cmake build dir",
  "CACHEDIR.TAG": "cache dir (cargo target, etc.)",
}
# Directories reclaimable by name alone.
NAME_MARKERS = {
  "node_modules": "node packages",
}

SECONDS_PER_DAY = 86400


def dir_size(path):
  """Sum actual disk usage under a directory.

  Uses st_blocks rather than st_size so the result matches
  du, counting sparse files and block overhead the way the
  filesystem does.

  Args:
    path: Directory to measure.

  Returns:
    Size in bytes.
  """
  total = 0
  stack = [path]
  while stack:
    current = stack.pop()
    try:
      with os.scandir(current) as entries:
        for entry in entries:
          try:
            if entry.is_dir(follow_symlinks=False):
              stack.append(entry.path)
            elif entry.is_file(follow_symlinks=False):
              total += entry.stat(
                follow_symlinks=False
              ).st_blocks * 512
          except OSError:
            continue
    except OSError:
      continue
  return total


def classify(path):
  """Identify a directory as regenerable, or not.

  Args:
    path: Directory path.

  Returns:
    A short description of what it is, or None if the
    directory is not provably regenerable.
  """
  name = os.path.basename(path)
  if name in NAME_MARKERS:
    return NAME_MARKERS[name]
  for marker, desc in FILE_MARKERS.items():
    if os.path.isfile(os.path.join(path, marker)):
      return desc
  return None


def find_reclaimable(root):
  """Find regenerable directories under a workspace.

  Does not descend into a directory once it is identified —
  a build tree's subdirectories are already covered by it.
  Skips .git entirely.

  Args:
    root: Workspace directory.

  Returns:
    List of dicts with path, kind and bytes.
  """
  found = []
  stack = [str(root)]
  while stack:
    current = stack.pop()
    try:
      with os.scandir(current) as entries:
        children = [
          e for e in entries
          if e.is_dir(follow_symlinks=False)
        ]
    except OSError:
      continue
    for entry in children:
      if entry.name == ".git":
        continue
      kind = classify(entry.path)
      if kind:
        found.append({
          "path": entry.path,
          "kind": kind,
          "bytes": dir_size(entry.path),
        })
        continue
      stack.append(entry.path)
  return found


def human(num_bytes):
  """Format a byte count for reading.

  Args:
    num_bytes: Size in bytes.

  Returns:
    Short string such as '4.9G'.
  """
  size = float(num_bytes)
  for unit in ("B", "K", "M", "G", "T"):
    if abs(size) < 1024 or unit == "T":
      if unit == "B":
        return f"{int(size)}B"
      return f"{size:.1f}{unit}"
    size /= 1024
  return f"{size:.1f}T"


def idle_days(last_active, now):
  """Days since a workspace was last touched.

  Args:
    last_active: Epoch seconds, or 0 if unknown.
    now: Current epoch seconds.

  Returns:
    Whole days idle, or None if unknown.
  """
  if not last_active:
    return None
  return int((now - last_active) / SECONDS_PER_DAY)


def active_workspaces():
  """Workspaces with a run in flight, which must be spared.

  Queries the local takt service. If it cannot be reached
  the answer is unknown, and the caller must treat that as
  "cannot safely delete" rather than "nothing is running".

  Returns:
    Set of workspace names, or None if the service did not
    answer.
  """
  import urllib.error
  import urllib.request
  base = os.environ.get(
    "TAKT_API_URL", "http://127.0.0.1:7433"
  )
  token = os.environ.get("TAKT_API_TOKEN", "")
  req = urllib.request.Request(
    f"{base}/api/runs?limit=200",
    headers=(
      {"Authorization": f"Bearer {token}"} if token else {}
    ),
  )
  try:
    with urllib.request.urlopen(req, timeout=10) as resp:
      payload = json.loads(resp.read())
  except (urllib.error.URLError, OSError, ValueError):
    return None
  runs = payload.get("data", {}).get("runs", [])
  return {
    r.get("workspace") for r in runs
    if r.get("status") in ("queued", "running")
  }


def survey(selected, now, on_progress=None):
  """Measure reclaimable output for the given workspaces.

  Args:
    selected: List of workspace dicts from list_workspaces.
    now: Current epoch seconds.
    on_progress: Optional callback(index, total, name,
      size_bytes) invoked after each workspace. Surveying a
      few hundred workspaces means stat-ing every file in
      them, which takes minutes; without feedback the tool
      looks hung.

  Returns:
    List of per-workspace dicts, largest first.
  """
  rows = []
  total = len(selected)
  for index, ws in enumerate(selected, start=1):
    items = find_reclaimable(Path(ws["path"]))
    size = sum(i["bytes"] for i in items)
    if on_progress:
      on_progress(index, total, ws["name"], size)
    if not items:
      continue
    rows.append({
      "workspace": ws["name"],
      "path": ws["path"],
      "idle_days": idle_days(ws.get("last_active"), now),
      "bytes": size,
      "items": sorted(
        items, key=lambda i: i["bytes"], reverse=True
      ),
    })
  return sorted(rows, key=lambda r: r["bytes"], reverse=True)


def has_tracked_files(path):
  """Check whether git tracks anything inside a directory.

  The markers are strong evidence but not a guarantee: if a
  generated directory was ever committed, deleting it would
  destroy content that only exists in the working tree of
  that branch. Refusing on tracked content costs one git
  call per candidate and removes the last way this tool
  could lose work.

  A directory outside any repo counts as untracked — it is
  still inside a workspace, so the other guards apply.

  Args:
    path: Directory to check.

  Returns:
    True if git reports tracked files under path.
  """
  try:
    res = subprocess.run(
      ["git", "-C", path, "ls-files", "--", "."],
      capture_output=True, text=True, timeout=60,
    )
  except (OSError, subprocess.SubprocessError):
    # Cannot tell; assume the worst and keep the directory.
    return True
  if res.returncode != 0:
    return False
  return bool(res.stdout.strip())


def is_within(child, parent):
  """Check that a path really sits under a directory.

  Resolves both sides, so a symlink cannot be used to point
  deletion somewhere outside the workspaces tree.

  Args:
    child: Path to test.
    parent: Directory it must be under.

  Returns:
    True if child is inside parent.
  """
  try:
    child_real = Path(child).resolve()
    parent_real = Path(parent).resolve()
  except OSError:
    return False
  return (
    child_real == parent_real
    or parent_real in child_real.parents
  )


def delete(item):
  """Remove one reclaimable directory, re-checking first.

  The marker is verified again at deletion time: the survey
  may have run minutes ago, and a directory that is no
  longer a build dir must not be removed on the strength of
  a stale reading. Containment is re-checked for the same
  reason.

  Args:
    item: Dict with path and kind.

  Returns:
    (ok, message) tuple.
  """
  path = item["path"]
  if not is_within(path, WORKSPACES_DIR):
    return (False, "outside the workspaces tree")
  if not os.path.isdir(path):
    return (False, "gone already")
  if not classify(path):
    return (False, "no longer a build dir")
  if has_tracked_files(path):
    return (False, "contains git-tracked files")
  try:
    shutil.rmtree(path)
  except OSError as e:
    return (False, str(e))
  return (True, "removed")


def add_reclaim_args(parser):
  """Attach reclaim's flags to a parser.

  Shared by `takt reclaim` and this script, so the two
  entry points cannot drift apart.

  Args:
    parser: An ArgumentParser or subparser to populate.

  Returns:
    The same parser.
  """
  parser.add_argument(
    "--workspace", action="append", default=[],
    help="Limit to this workspace (repeatable).",
  )
  parser.add_argument(
    "--older-than", type=int, metavar="DAYS",
    help="Only workspaces idle at least this many days.",
  )
  parser.add_argument(
    "--min-size", type=int, default=0, metavar="MB",
    help="Ignore workspaces below this total.",
  )
  parser.add_argument(
    "--apply", action="store_true",
    help="Actually delete. Requires --older-than or "
         "--workspace, so a bare --apply cannot wipe "
         "every workspace at once.",
  )
  parser.add_argument(
    "--json", action="store_true",
    help="Emit JSON instead of a table.",
  )
  parser.add_argument(
    "--quiet", action="store_true",
    help="Suppress the progress line on stderr.",
  )
  return parser


def run_reclaim(args, parser=None):
  """Survey, and optionally reclaim, build output.

  Args:
    args: Parsed arguments from add_reclaim_args().
    parser: Parser to raise usage errors through. Falls
      back to SystemExit when absent.

  Returns:
    Process exit code.
  """
  def fail(message):
    if parser is not None:
      parser.error(message)
    raise SystemExit(f"takt reclaim: error: {message}")

  # `--older-than 0` is a legitimate explicit choice, so
  # test for presence rather than truthiness. The guard
  # exists to stop an accidental bare --apply, not to argue
  # with someone who named a threshold.
  scoped = (
    args.older_than is not None or bool(args.workspace)
  )
  if args.apply and not scoped:
    fail(
      "--apply needs --older-than or --workspace; refusing "
      "to delete across all workspaces at once"
    )

  now = time.time()
  workspaces = list_workspaces()
  if args.workspace:
    wanted = set(args.workspace)
    workspaces = [
      w for w in workspaces if w["name"] in wanted
    ]
    missing = wanted - {w["name"] for w in workspaces}
    if missing:
      print(
        f"unknown workspace: {', '.join(sorted(missing))}",
        file=sys.stderr,
      )
      return 2
  if args.older_than is not None:
    workspaces = [
      w for w in workspaces
      if (idle_days(w.get("last_active"), now) or 0)
      >= args.older_than
    ]

  def progress(index, total, name, size):
    """Report survey progress on stderr.

    stderr so that --json stays machine-readable and the
    table on stdout stays pipeable.
    """
    if args.quiet:
      return
    found = f" {human(size)}" if size else ""
    print(
      f"\r[{index}/{total}] {name}{found}".ljust(70),
      end="" if index < total else "\n",
      file=sys.stderr, flush=True,
    )

  rows = survey(workspaces, now, on_progress=progress)
  if args.min_size:
    floor = args.min_size * 1024 * 1024
    rows = [r for r in rows if r["bytes"] >= floor]

  total = sum(r["bytes"] for r in rows)

  busy = None
  if args.apply:
    busy = active_workspaces()
    if busy is None:
      print(
        "takt service did not answer; cannot tell which "
        "workspaces have runs in flight. Refusing to "
        "delete.",
        file=sys.stderr,
      )
      return 3

  if args.json and not args.apply:
    print(json.dumps(
      {"total_bytes": total, "workspaces": rows}, indent=2,
    ))
    return 0

  if not rows:
    print("nothing reclaimable found")
    return 0

  if not args.apply:
    print(f"{'workspace':<40} {'idle':>6} {'reclaim':>9}")
    print("-" * 58)
    for r in rows:
      idle = (
        f"{r['idle_days']}d" if r["idle_days"] is not None
        else "?"
      )
      print(
        f"{r['workspace']:<40} {idle:>6} "
        f"{human(r['bytes']):>9}"
      )
    print("-" * 58)
    print(
      f"{len(rows)} workspaces, {human(total)} reclaimable"
    )
    print()
    print("dry run — nothing deleted. Add --apply to "
          "reclaim, with --older-than or --workspace.")
    return 0

  freed = 0
  skipped = []
  for r in rows:
    if r["workspace"] in busy:
      skipped.append(r["workspace"])
      continue
    for item in r["items"]:
      ok, msg = delete(item)
      if ok:
        freed += item["bytes"]
      else:
        print(f"  skip {item['path']}: {msg}")
    print(f"{r['workspace']}: {human(r['bytes'])}")
  print(f"\nfreed {human(freed)}")
  if skipped:
    print(
      f"spared {len(skipped)} with runs in flight: "
      f"{', '.join(sorted(skipped))}"
    )
  return 0


def main():
  """Standalone entry point: `bin/reclaim.py`."""
  parser = argparse.ArgumentParser(
    prog="reclaim.py",
    description=(
      "Report and reclaim regenerable build output in "
      "takt workspaces."
    ),
  )
  add_reclaim_args(parser)
  return run_reclaim(parser.parse_args(), parser=parser)


if __name__ == "__main__":
  sys.exit(main())
