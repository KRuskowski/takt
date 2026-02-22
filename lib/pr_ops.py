"""GitHub PR operations for dashboard display.

Discovers GitHub slugs from root repo remotes and queries
open PRs via the gh CLI.
"""

import json
import re
import shutil
import subprocess

from lib.config import ROOT_DIR, load_repos_config


def _get_github_slug(repo_path):
  """Extract GitHub OWNER/REPO slug from a repo's remotes.

  Scans all remotes for a github.com URL. Supports both SSH
  (git@github.com:OWNER/REPO.git) and HTTPS
  (https://github.com/OWNER/REPO.git) formats.

  Args:
    repo_path: Path to the git repo (bare or normal).

  Returns:
    "OWNER/REPO" string or None if no GitHub remote found.
  """
  try:
    result = subprocess.run(
      ["git", "remote", "-v"],
      capture_output=True, text=True, cwd=repo_path,
      timeout=5,
    )
    if result.returncode != 0:
      return None
  except (subprocess.TimeoutExpired, OSError):
    return None

  # SSH: git@github.com:OWNER/REPO.git
  ssh_pat = re.compile(
    r'github\.com[:/]([^/]+/[^/\s]+?)(?:\.git)?\s'
  )
  # HTTPS: https://github.com/OWNER/REPO.git
  https_pat = re.compile(
    r'github\.com/([^/]+/[^/\s]+?)(?:\.git)?\s'
  )

  for line in result.stdout.splitlines():
    for pat in (ssh_pat, https_pat):
      m = pat.search(line)
      if m:
        return m.group(1)
  return None


def get_github_slugs():
  """Build a {repo_name: "OWNER/REPO"} map from repos.yaml.

  Iterates repos from config, resolves root repo paths, and
  extracts GitHub slugs. Skips repos without GitHub remotes
  or missing root repos.

  Returns:
    Dict mapping repo name to GitHub slug.
  """
  repos_config = load_repos_config().get("repos", {})
  slugs = {}
  for name, cfg in repos_config.items():
    disk_path = cfg.get("path", name)
    repo_path = ROOT_DIR / disk_path
    if not repo_path.exists():
      continue
    slug = _get_github_slug(repo_path)
    if slug:
      slugs[name] = slug
  return slugs


def list_prs_for_branch(slug, branch):
  """Query open PRs for a branch via gh CLI.

  Args:
    slug: GitHub "OWNER/REPO" string.
    branch: Branch name to match as head.

  Returns:
    List of dicts with keys: number, title, isDraft,
    mergeable, url. Empty list on error.
  """
  try:
    result = subprocess.run(
      [
        "gh", "pr", "list",
        "-R", slug,
        "--head", branch,
        "--state", "open",
        "--json", "number,title,isDraft,mergeable,url",
        "--limit", "10",
      ],
      capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
      return []
    return json.loads(result.stdout) or []
  except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
    return []


def list_all_prs():
  """Gather open PRs across all workspaces and repos.

  Returns:
    Tuple of (rows, available) where rows is a list of dicts
    with keys: workspace, repo, number, title, is_draft,
    mergeable, url; and available is False if gh CLI is not
    installed.
  """
  if not shutil.which("gh"):
    return ([], False)

  from lib.workspace_ops import list_workspaces
  slugs = get_github_slugs()
  if not slugs:
    return ([], True)

  workspaces = list_workspaces()
  rows = []
  for ws in workspaces:
    branch = ws["branch"]
    if branch == "?":
      continue
    for repo_name, slug in slugs.items():
      prs = list_prs_for_branch(slug, branch)
      for pr in prs:
        rows.append({
          "workspace": ws["name"],
          "repo": repo_name,
          "number": pr.get("number", 0),
          "title": pr.get("title", ""),
          "is_draft": pr.get("isDraft", False),
          "mergeable": pr.get("mergeable", "UNKNOWN"),
          "url": pr.get("url", ""),
        })
  return (rows, True)
