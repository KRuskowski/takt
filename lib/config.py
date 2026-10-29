"""Shared constants and configuration loaders."""

import os
import re
from pathlib import Path

import yaml


PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_DIR / "config"
TEMPLATES_DIR = PROJECT_DIR / "templates"
CONTEXT_DIR = PROJECT_DIR / "context"
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

# State lives in the checkout by default. A scheduler node
# wants it somewhere durable that does not move when the
# repo is re-cloned, so it is configurable.
STATE_DIR = Path(
  os.environ.get("TAKT_STATE_DIR")
  or _takt.get("state_dir")
  or (PROJECT_DIR / ".state")
).expanduser()

# Address the REST API binds to. Loopback by default — the
# API is unauthenticated unless api_token is set, so it must
# not become reachable merely by upgrading.
API_HOST = (
  os.environ.get("TAKT_API_HOST")
  or _takt.get("api_host")
  or "127.0.0.1"
)
API_PORT = int(
  os.environ.get("TAKT_API_PORT")
  or _takt.get("api_port")
  or 7433
)

# Ensure runtime dirs exist.
#
# Best-effort, because these are scheduler-side conveniences
# and this module is imported by things that need none of
# them. BASE_DIR defaults to a path on the scheduler, so on
# any other machine these are the wrong directories to be
# creating -- and on Windows '/home/karl/dev' becomes
# '\home\karl\dev', whose parent does not exist, so mkdir
# raised and the import died. That took `takt worker` down on
# the Windows workstation before it could parse an argument.
#
# A component that genuinely requires one of these creates it
# itself; failing to pre-make them is never the real error.
for _runtime_dir in (
  STATE_DIR, LOCKS_DIR, WORKSPACES_DIR, STAGES_DIR,
):
  try:
    _runtime_dir.mkdir(parents=True, exist_ok=True)
  except OSError:
    pass


# Credentials live outside the checkout: config/takt.yaml is
# tracked in a public repo, so a token placed there would be
# published. systemd loads this file into the service, and
# load_env_file() lets client tools find it too.
DEFAULT_ENV_FILE = Path.home() / ".config/takt/env"


def env_file_path():
  """Return the credentials file to read.

  Resolved on every call rather than captured at import, so
  TAKT_ENV_FILE works whenever it is set — including in a
  test that points it somewhere harmless after lib.config
  has already been imported by something else.

  Returns:
    Path to the credentials file. It need not exist.
  """
  return Path(
    os.environ.get("TAKT_ENV_FILE") or DEFAULT_ENV_FILE
  ).expanduser()


def load_env_file(path=None):
  """Parse a KEY=value credentials file.

  Args:
    path: File to read. Defaults to env_file_path().

  Returns:
    Dict of the keys found, empty if the file is absent or
    unreadable.
  """
  target = Path(path) if path else env_file_path()
  try:
    text = target.read_text()
  except OSError:
    return {}
  values = {}
  for line in text.splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
      continue
    key, _, value = line.partition("=")
    values[key.strip()] = value.strip()
  return values


def api_token():
  """Return the shared secret required by the REST API.

  Read fresh rather than cached at import, so rotating the
  token does not need a restart of every client in the
  process. None means the API is unauthenticated, which is
  only safe while it is bound to loopback.

  Returns:
    Token string, or None if not configured.
  """
  token = (
    os.environ.get("TAKT_API_TOKEN")
    or load_env_file().get("TAKT_API_TOKEN")
    or load_takt_config().get("api_token")
  )
  return token or None


def api_base_url():
  """Return the scheduler URL a client should call.

  Lets every client — MCP, reclaim, the CLI — agree on one
  place to look, so enabling the token does not require
  editing each tool's environment separately.

  Returns:
    Base URL string.
  """
  env = load_env_file()
  return (
    os.environ.get("TAKT_API_URL")
    or env.get("TAKT_API_URL")
    or f"http://127.0.0.1:{API_PORT}"
  )


def load_repos_config():
  """Load repos.yaml, normalised to {"repos": {...}}.

  Two shapes exist on disk. The documented one nests the
  registry under a top-level "repos" key; the file this
  project actually carries is a flat mapping of repo name to
  settings. Almost all callers do .get("repos", {}), so
  against a flat file they silently saw *no repos at all* --
  and no test caught it, because every test mocks this
  function with the nested shape instead of reading a file.
  That is how push, pr_ops and checks came to be quietly
  operating on an empty registry.

  Both shapes are accepted and returned nested, so callers
  need one form.

  Returns:
    Dict with a "repos" key mapping repo name -> settings,
    plus any other top-level keys the file carried.
  """
  path = CONFIG_DIR / "repos.yaml"
  with open(path) as f:
    data = yaml.safe_load(f) or {}
  if not isinstance(data, dict):
    return {"repos": {}}
  nested = data.get("repos")
  if isinstance(nested, dict):
    return data
  # Flat file: every top-level key is a repo. A repo named
  # "repos" would be ambiguous, which is why the nested
  # form is the one to prefer in new files.
  return {**data, "repos": data}


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
