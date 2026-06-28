"""Universal workspace health checks.

Each check function takes a workspace path and returns a
dict with 'status' ('pass'|'fail'|'warn'), a 'summary'
string, and check-specific detail fields. Used by both
pipeline steps and the MCP workspace_health tool.
"""

import json
import math
import os
import re
import subprocess
from pathlib import Path

from lib.config import ROOT_DIR, load_repos_config
from lib.git_utils import get_branch_ref, run_git


def check_freshness(ws_path, repos=None):
  """Check if workspace branches are rebased on master.

  Args:
    ws_path: Path to the workspace directory.
    repos: Optional list of repo names. Auto-detected
      if None.

  Returns:
    Dict with status, behind counts per repo.
  """
  ws_path = Path(ws_path)
  if repos is None:
    repos = [
      d.name for d in ws_path.iterdir()
      if d.is_dir() and (d / ".git").exists()
    ]
  repos_config = load_repos_config().get("repos", {})
  results = []
  total_behind = 0
  for repo in repos:
    repo_path = ws_path / repo
    if not repo_path.exists():
      continue
    cfg = repos_config.get(repo, {})
    default_br = cfg.get("default_branch", "main")
    try:
      run_git(
        ["fetch", "origin"], cwd=str(repo_path),
      )
      out = run_git(
        ["rev-list", "--count",
         f"HEAD..origin/{default_br}"],
        cwd=str(repo_path),
      )
      behind = int(out.strip())
    except Exception:
      behind = -1
    results.append({
      "repo": repo, "behind": behind,
    })
    if behind > 0:
      total_behind += behind
  status = "pass" if total_behind == 0 else "fail"
  return {
    "status": status,
    "summary": (
      "up to date" if total_behind == 0
      else f"{total_behind} commits behind master"
    ),
    "repos": results,
    "total_behind": total_behind,
  }


def check_build(ws_path, repos=None):
  """Detect build system and run a build.

  Supports CMake, Cargo, npm, pip/setuptools.

  Args:
    ws_path: Path to the workspace directory.
    repos: Optional list of repo names.

  Returns:
    Dict with status, per-repo results.
  """
  ws_path = Path(ws_path)
  if repos is None:
    repos = [
      d.name for d in ws_path.iterdir()
      if d.is_dir() and (d / ".git").exists()
    ]
  results = []
  any_fail = False
  for repo in repos:
    rp = ws_path / repo
    if not rp.exists():
      continue
    cmd, build_sys = _detect_build(rp)
    if cmd is None:
      results.append({
        "repo": repo, "system": "none",
        "status": "skip",
      })
      continue
    try:
      proc = subprocess.run(
        cmd, cwd=str(rp), capture_output=True,
        text=True, timeout=300,
      )
      ok = proc.returncode == 0
      results.append({
        "repo": repo, "system": build_sys,
        "status": "pass" if ok else "fail",
        "output": proc.stderr[-500:] if not ok
                  else "",
      })
      if not ok:
        any_fail = True
    except subprocess.TimeoutExpired:
      results.append({
        "repo": repo, "system": build_sys,
        "status": "fail", "output": "timeout",
      })
      any_fail = True
    except Exception as e:
      results.append({
        "repo": repo, "system": build_sys,
        "status": "fail", "output": str(e),
      })
      any_fail = True
  return {
    "status": "fail" if any_fail else "pass",
    "summary": (
      "all builds passed" if not any_fail
      else "build failed"
    ),
    "repos": results,
  }


def check_tests(ws_path, repos=None):
  """Detect test framework and run tests.

  Args:
    ws_path: Path to the workspace directory.
    repos: Optional list of repo names.

  Returns:
    Dict with status, per-repo results.
  """
  ws_path = Path(ws_path)
  if repos is None:
    repos = [
      d.name for d in ws_path.iterdir()
      if d.is_dir() and (d / ".git").exists()
    ]
  results = []
  any_fail = False
  for repo in repos:
    rp = ws_path / repo
    if not rp.exists():
      continue
    cmd, test_sys = _detect_tests(rp)
    if cmd is None:
      results.append({
        "repo": repo, "system": "none",
        "status": "skip",
      })
      continue
    try:
      proc = subprocess.run(
        cmd, cwd=str(rp), capture_output=True,
        text=True, timeout=600,
      )
      ok = proc.returncode == 0
      results.append({
        "repo": repo, "system": test_sys,
        "status": "pass" if ok else "fail",
        "output": (proc.stdout + proc.stderr)[-1000:]
                  if not ok else "",
      })
      if not ok:
        any_fail = True
    except subprocess.TimeoutExpired:
      results.append({
        "repo": repo, "system": test_sys,
        "status": "fail", "output": "timeout",
      })
      any_fail = True
  return {
    "status": "fail" if any_fail else "pass",
    "summary": (
      "all tests passed" if not any_fail
      else "tests failed"
    ),
    "repos": results,
  }


# Patterns that suggest secrets.
_SECRET_PATTERNS = [
  re.compile(
    r'(?:api[_-]?key|secret|password|token|auth)'
    r'\s*[=:]\s*["\'][^"\']{8,}',
    re.IGNORECASE,
  ),
  re.compile(
    r'-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----',
  ),
  re.compile(
    r'AKIA[0-9A-Z]{16}',
  ),
  re.compile(
    r'ghp_[A-Za-z0-9]{36}',
  ),
  re.compile(
    r'sk-[A-Za-z0-9]{32,}',
  ),
]
_SECRET_FILES = {
  ".env", ".env.local", ".env.production",
  "credentials.json", "secrets.yaml", "secrets.yml",
}


def check_secrets(ws_path, repos=None):
  """Scan diff for secrets and sensitive files.

  Args:
    ws_path: Path to the workspace directory.
    repos: Optional list of repo names.

  Returns:
    Dict with status, hits.
  """
  ws_path = Path(ws_path)
  if repos is None:
    repos = [
      d.name for d in ws_path.iterdir()
      if d.is_dir() and (d / ".git").exists()
    ]
  repos_config = load_repos_config().get("repos", {})
  hits = []
  for repo in repos:
    rp = ws_path / repo
    if not rp.exists():
      continue
    cfg = repos_config.get(repo, {})
    default_br = cfg.get("default_branch", "main")
    try:
      diff = run_git(
        ["diff", f"origin/{default_br}...HEAD",
         "--unified=0", "--no-color"],
        cwd=str(rp),
      )
    except Exception:
      continue
    for i, line in enumerate(diff.splitlines()):
      if not line.startswith("+") or \
          line.startswith("+++"):
        continue
      for pat in _SECRET_PATTERNS:
        if pat.search(line):
          hits.append({
            "repo": repo,
            "line": line[:120],
            "pattern": pat.pattern[:40],
          })
          break
      fname = _extract_filename(line)
      if fname and Path(fname).name in _SECRET_FILES:
        hits.append({
          "repo": repo,
          "line": line[:120],
          "pattern": "sensitive file",
        })
  return {
    "status": "fail" if hits else "pass",
    "summary": (
      f"{len(hits)} potential secret(s) found"
      if hits else "clean"
    ),
    "hits": hits,
  }


def check_diff_size(ws_path, repos=None,
                    threshold=2000):
  """Flag large diffs that suggest agent dumps.

  Args:
    ws_path: Path to the workspace directory.
    repos: Optional list of repo names.
    threshold: Max added lines before warning.

  Returns:
    Dict with status, per-repo line counts.
  """
  ws_path = Path(ws_path)
  if repos is None:
    repos = [
      d.name for d in ws_path.iterdir()
      if d.is_dir() and (d / ".git").exists()
    ]
  repos_config = load_repos_config().get("repos", {})
  results = []
  total_added = 0
  for repo in repos:
    rp = ws_path / repo
    if not rp.exists():
      continue
    cfg = repos_config.get(repo, {})
    default_br = cfg.get("default_branch", "main")
    try:
      out = run_git(
        ["diff", f"origin/{default_br}...HEAD",
         "--stat", "--stat-width=200"],
        cwd=str(rp),
      )
      last = out.strip().splitlines()[-1] if out.strip() \
          else ""
      m = re.search(r'(\d+) insertion', last)
      added = int(m.group(1)) if m else 0
    except Exception:
      added = 0
    results.append({"repo": repo, "added": added})
    total_added += added
  over = total_added > threshold
  return {
    "status": "warn" if over else "pass",
    "summary": (
      f"{total_added} lines added"
      + (f" (threshold: {threshold})" if over else "")
    ),
    "total_added": total_added,
    "threshold": threshold,
    "repos": results,
  }


def workspace_health(ws_path, repos=None):
  """Roll up all checks into a single health report.

  Args:
    ws_path: Path to the workspace directory.
    repos: Optional list of repo names.

  Returns:
    Dict with overall status and per-check results.
  """
  ws_path = Path(ws_path)
  freshness = check_freshness(ws_path, repos)
  secrets = check_secrets(ws_path, repos)
  diff_size = check_diff_size(ws_path, repos)

  # Last pipeline run.
  last_run = None
  run_file = ws_path / ".takt" / "last-run.json"
  if run_file.exists():
    try:
      last_run = json.loads(run_file.read_text())
    except Exception:
      pass

  checks = {
    "freshness": freshness,
    "secrets": secrets,
    "diff_size": diff_size,
  }
  statuses = [c["status"] for c in checks.values()]
  if "fail" in statuses:
    overall = "fail"
  elif "warn" in statuses:
    overall = "warn"
  else:
    overall = "pass"

  return {
    "status": overall,
    "checks": checks,
    "last_run": last_run,
  }


# -- Build/test detection helpers --

def _detect_build(repo_path):
  """Detect the build system for a repo.

  Returns:
    (command_list, system_name) or (None, None).
  """
  rp = Path(repo_path)
  if (rp / "CMakeLists.txt").exists():
    build_dir = rp / "build"
    if (build_dir / "build.ninja").exists() or \
        (build_dir / "Makefile").exists():
      return (
        ["cmake", "--build", "build",
         f"-j{os.cpu_count() or 4}"],
        "cmake",
      )
    return (
      ["cmake", "-B", "build", "-DCMAKE_BUILD_TYPE=Debug",
       "&&", "cmake", "--build", "build",
       f"-j{os.cpu_count() or 4}"],
      "cmake",
    )
  if (rp / "Cargo.toml").exists():
    return (["cargo", "build"], "cargo")
  if (rp / "package.json").exists():
    return (["npm", "run", "build"], "npm")
  if (rp / "setup.py").exists() or \
      (rp / "pyproject.toml").exists():
    return (
      ["pip", "install", "-e", ".", "-q"], "pip",
    )
  return (None, None)


def _detect_tests(repo_path):
  """Detect the test framework for a repo.

  Returns:
    (command_list, system_name) or (None, None).
  """
  rp = Path(repo_path)
  if (rp / "CMakeLists.txt").exists():
    build_dir = rp / "build"
    if build_dir.exists():
      return (
        ["ctest", "--test-dir", "build",
         "--output-on-failure"],
        "ctest",
      )
  if (rp / "Cargo.toml").exists():
    return (["cargo", "test"], "cargo")
  if (rp / "package.json").exists():
    return (["npm", "test"], "npm")
  if (rp / "pytest.ini").exists() or \
      (rp / "tests").is_dir() or \
      (rp / "test").is_dir():
    return (["python3", "-m", "pytest", "-q"], "pytest")
  return (None, None)


def _extract_filename(diff_line):
  """Extract filename from a diff +++ line."""
  if diff_line.startswith("+++ b/"):
    return diff_line[6:]
  return None
