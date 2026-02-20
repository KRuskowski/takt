"""Shared constants and configuration loaders."""

import os
from pathlib import Path

import yaml


BASE_DIR = Path(os.environ.get(
  "ORCH_BASE_DIR", os.path.expanduser("~/dev")
))
WORKSPACES_DIR = BASE_DIR / "workspaces"
PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_DIR / "config"
TEMPLATES_DIR = PROJECT_DIR / "templates"
CONTEXT_DIR = PROJECT_DIR / "context"
STATE_DIR = PROJECT_DIR / ".state"
LOCKS_DIR = PROJECT_DIR / ".locks"

# Ensure runtime dirs exist.
STATE_DIR.mkdir(exist_ok=True)
LOCKS_DIR.mkdir(exist_ok=True)
WORKSPACES_DIR.mkdir(exist_ok=True)


def load_repos_config():
  """Load and return repos.yaml as a dict."""
  path = CONFIG_DIR / "repos.yaml"
  with open(path) as f:
    return yaml.safe_load(f) or {}


def load_targets_config():
  """Load and return targets.yaml as a dict."""
  path = CONFIG_DIR / "targets.yaml"
  with open(path) as f:
    data = yaml.safe_load(f)
  return data or {}


def get_repo_path(repo_name):
  """Return the absolute path to a root repo."""
  return BASE_DIR / repo_name


def get_default_branch(repo_path):
  """Auto-detect the default branch (main or master) for a repo.

  Checks origin/HEAD first, then falls back to checking if main or
  master branches exist locally.
  """
  import subprocess
  repo_path = Path(repo_path)

  # Try origin/HEAD.
  try:
    result = subprocess.run(
      ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
      capture_output=True, text=True, cwd=repo_path, check=True,
    )
    return result.stdout.strip().replace("refs/remotes/origin/", "")
  except subprocess.CalledProcessError:
    pass

  # Fall back to checking local branches.
  try:
    result = subprocess.run(
      ["git", "branch", "--list"],
      capture_output=True, text=True, cwd=repo_path, check=True,
    )
    branches = [
      b.strip().lstrip("* ") for b in result.stdout.splitlines()
    ]
    if "main" in branches:
      return "main"
    if "master" in branches:
      return "master"
  except subprocess.CalledProcessError:
    pass

  return "main"  # Default assumption.


def validate_repo(repo_name):
  """Check that a repo exists and is a git repo. Returns True/False."""
  repo_path = get_repo_path(repo_name)
  return (repo_path / ".git").is_dir()
