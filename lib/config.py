"""Shared constants and configuration loaders."""

import os
import re
from pathlib import Path

import yaml


PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_DIR / "config"
TEMPLATES_DIR = PROJECT_DIR / "templates"
CONTEXT_DIR = PROJECT_DIR / "context"
STATE_DIR = PROJECT_DIR / ".state"
LOCKS_DIR = PROJECT_DIR / ".locks"


def load_takt_config():
  """Load config/takt.yaml and return as a dict."""
  path = CONFIG_DIR / "takt.yaml"
  if path.exists():
    with open(path) as f:
      return yaml.safe_load(f) or {}
  return {}


_takt = load_takt_config()
BASE_DIR = Path(_takt.get(
  "base_dir",
  os.environ.get("ORCH_BASE_DIR", "/home/karl/dev"),
))
ROOT_DIR = BASE_DIR / "root"
STAGES_DIR = BASE_DIR / "stages"
WORKSPACES_DIR = BASE_DIR / "workspaces"
# Ensure runtime dirs exist.
STATE_DIR.mkdir(exist_ok=True)
LOCKS_DIR.mkdir(exist_ok=True)
WORKSPACES_DIR.mkdir(exist_ok=True)
STAGES_DIR.mkdir(exist_ok=True)


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


def save_targets_config(config):
  """Write config dict back to targets.yaml."""
  path = CONFIG_DIR / "targets.yaml"
  with open(path, "w") as f:
    yaml.dump(config, f, default_flow_style=False,
              sort_keys=False)


def get_repo_path(repo_name, base_dir=None):
  """Return the absolute path to a repo.

  Args:
    repo_name: Repo name or relative path.
    base_dir: Base directory. Defaults to ROOT_DIR.
  """
  return (base_dir or ROOT_DIR) / repo_name


def parse_pipeline_roles():
  """Parse pipeline_roles.md and return role snippets.

  Parses H2 headings as role names and everything between
  headings (excluding --- separators) as the role snippet.
  Role names are normalized to lowercase slugs
  (e.g. "Deploy/QA Agent" -> "deploy_qa").

  Returns:
    Dict mapping role slug to snippet text.
  """
  roles_path = TEMPLATES_DIR / "pipeline_roles.md"
  if not roles_path.exists():
    return {}

  text = roles_path.read_text()
  roles = {}
  current_name = None
  current_lines = []

  for line in text.splitlines():
    if line.startswith("## "):
      # Save previous role.
      if current_name:
        snippet = "\n".join(current_lines).strip()
        if snippet:
          roles[current_name] = snippet
      # Parse new role name.
      raw_name = line[3:].strip()
      current_name = _slugify_role(raw_name)
      current_lines = []
    elif line.strip() == "---":
      continue
    elif current_name is not None:
      current_lines.append(line)

  # Save last role.
  if current_name:
    snippet = "\n".join(current_lines).strip()
    if snippet:
      roles[current_name] = snippet

  return roles


def parse_pipeline_roles_full():
  """Parse pipeline_roles.md preserving order and headings.

  Like parse_pipeline_roles() but returns full role metadata
  for round-trip editing.

  Returns:
    List of dicts: [{"slug", "heading", "text"}, ...].
  """
  roles_path = TEMPLATES_DIR / "pipeline_roles.md"
  if not roles_path.exists():
    return []

  text = roles_path.read_text()
  roles = []
  current_heading = None
  current_lines = []

  for line in text.splitlines():
    if line.startswith("## "):
      # Save previous role.
      if current_heading:
        snippet = "\n".join(current_lines).strip()
        roles.append({
          "slug": _slugify_role(current_heading),
          "heading": current_heading,
          "text": snippet,
        })
      # Parse new role heading.
      current_heading = line[3:].strip()
      current_lines = []
    elif line.strip() == "---":
      continue
    elif current_heading is not None:
      current_lines.append(line)

  # Save last role.
  if current_heading:
    snippet = "\n".join(current_lines).strip()
    roles.append({
      "slug": _slugify_role(current_heading),
      "heading": current_heading,
      "text": snippet,
    })

  return roles


def save_pipeline_roles(roles):
  """Write role list back to pipeline_roles.md.

  Preserves preamble text (everything before the first
  ``## `` heading) from the existing file.

  Args:
    roles: List of dicts with keys "heading" and "text".
  """
  roles_path = TEMPLATES_DIR / "pipeline_roles.md"

  # Extract preamble from existing file.
  preamble = ""
  if roles_path.exists():
    existing = roles_path.read_text()
    first_h2 = existing.find("\n## ")
    if first_h2 == -1:
      # Check if file starts with ## .
      if existing.startswith("## "):
        preamble = ""
      else:
        preamble = existing
    else:
      preamble = existing[:first_h2 + 1]

  parts = [preamble.rstrip("\n")]
  for role in roles:
    parts.append("---")
    parts.append("")
    parts.append(f"## {role['heading']}")
    parts.append("")
    parts.append(role["text"])
    parts.append("")

  roles_path.write_text("\n".join(parts) + "\n")


def _slugify_role(name):
  """Convert a role heading to a lowercase slug.

  Examples:
    "Feature Agent" -> "feature"
    "Deploy/QA Agent" -> "deploy_qa"
    "Test Agent" -> "test"
  """
  # Remove trailing "Agent" suffix.
  name = re.sub(r'\s+Agent$', '', name, flags=re.IGNORECASE)
  # Replace non-alphanumeric with underscore.
  name = re.sub(r'[^a-zA-Z0-9]+', '_', name)
  # Strip leading/trailing underscores and lowercase.
  return name.strip('_').lower()


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
    return result.stdout.strip().replace(
      "refs/remotes/origin/", "",
    )
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
  """Check that a repo exists and is a git repo.

  Handles both bare repos (directory IS the git dir) and normal
  repos (directory contains a .git subdir).

  Returns:
    True if the repo exists and is a valid git repo.
  """
  repo_path = get_repo_path(repo_name)
  if (repo_path / ".git").is_dir():
    return True
  # Bare repo: HEAD file at top level.
  return (repo_path / "HEAD").is_file()
