"""Parse Claude Code JSONL session files for the dashboard.

Discovers sessions under ~/.claude/projects/, extracts metadata
and token usage, and calculates estimated costs.
"""

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Cost per 1M tokens (USD).
COST_RATES = {
  "opus": {
    "input": 15.0,
    "output": 75.0,
    "cache_read": 1.50,
    "cache_create": 18.75,
  },
  "sonnet": {
    "input": 3.0,
    "output": 15.0,
    "cache_read": 0.30,
    "cache_create": 3.75,
  },
  "haiku": {
    "input": 0.80,
    "output": 4.0,
    "cache_read": 0.08,
    "cache_create": 1.0,
  },
}

# Map model ID substrings to rate keys.
_MODEL_RATE_MAP = {
  "opus": "opus",
  "sonnet": "sonnet",
  "haiku": "haiku",
}

# Context window limits by model family.
_MODEL_CONTEXT_LIMIT = {
  "opus": 200_000,
  "sonnet": 200_000,
  "haiku": 200_000,
}

CLAUDE_DIR = Path(os.path.expanduser("~/.claude"))
PROJECTS_DIR = CLAUDE_DIR / "projects"
STATS_CACHE = CLAUDE_DIR / "stats-cache.json"


@dataclass
class SessionInfo:
  """Metadata and usage for a single Claude session."""
  session_id: str = ""
  project_path: str = ""
  cwd: str = ""
  git_branch: str = ""
  slug: str = ""
  model: str = ""
  started_at: str = ""
  last_active: str = ""
  is_active: bool = False
  message_count: int = 0
  total_input_tokens: int = 0
  total_output_tokens: int = 0
  total_cache_read: int = 0
  total_cache_create: int = 0
  estimated_cost_usd: float = 0.0
  duration_ms: int = 0
  context_tokens: int = 0
  context_limit: int = 0


@dataclass
class ModelUsage:
  """Aggregated token usage for a single model."""
  model: str = ""
  input_tokens: int = 0
  output_tokens: int = 0
  cache_read_tokens: int = 0
  cache_create_tokens: int = 0
  cost_usd: float = 0.0


@dataclass
class UsageSummary:
  """Aggregated usage across all sessions."""
  total_sessions: int = 0
  total_messages: int = 0
  by_model: dict = field(default_factory=dict)
  total_cost_usd: float = 0.0
  daily_activity: list = field(default_factory=list)


def _rate_key(model_id):
  """Map a model ID string to a cost rate key."""
  model_lower = model_id.lower()
  for substr, key in _MODEL_RATE_MAP.items():
    if substr in model_lower:
      return key
  return "sonnet"  # Default fallback.


def calculate_cost(model, input_tokens=0, output_tokens=0,
                   cache_read=0, cache_create=0):
  """Calculate estimated cost in USD for token usage.

  Args:
    model: Model ID string (e.g. 'claude-opus-4-6').
    input_tokens: Non-cached input tokens.
    output_tokens: Output tokens.
    cache_read: Cache read input tokens.
    cache_create: Cache creation input tokens.

  Returns:
    Estimated cost in USD.
  """
  key = _rate_key(model)
  rates = COST_RATES.get(key, COST_RATES["sonnet"])
  cost = (
    (input_tokens / 1_000_000) * rates["input"]
    + (output_tokens / 1_000_000) * rates["output"]
    + (cache_read / 1_000_000) * rates["cache_read"]
    + (cache_create / 1_000_000) * rates["cache_create"]
  )
  return round(cost, 4)


def parse_session_file(path, quick=True):
  """Parse a single JSONL session file.

  Args:
    path: Path to the .jsonl file.
    quick: If True, read only first 50 lines + last 64KB
           (for large files). Otherwise read everything.

  Returns:
    SessionInfo with extracted metadata and usage.
  """
  path = Path(path)
  info = SessionInfo(project_path=str(path.parent))

  try:
    file_size = path.stat().st_size
    mtime = path.stat().st_mtime
  except OSError:
    return info

  lines = []
  if quick and file_size > 512_000:  # >500KB, use quick mode.
    # Read first 50 lines for metadata.
    with open(path, "r", errors="replace") as f:
      for i, line in enumerate(f):
        if i >= 50:
          break
        lines.append(line)
    # Read last 64KB for recent usage.
    with open(path, "rb") as f:
      f.seek(max(0, file_size - 65536))
      chunk = f.read().decode("utf-8", errors="replace")
      # Skip partial first line.
      tail_lines = chunk.split("\n")
      if f.tell() != file_size or file_size > 65536:
        tail_lines = tail_lines[1:]  # Drop partial line.
      lines.extend(tail_lines)
  else:
    with open(path, "r", errors="replace") as f:
      lines = f.readlines()

  first_ts = None
  last_ts = None

  for raw_line in lines:
    raw_line = raw_line.strip()
    if not raw_line:
      continue
    try:
      obj = json.loads(raw_line)
    except json.JSONDecodeError:
      continue

    ts = obj.get("timestamp")
    if ts:
      if first_ts is None:
        first_ts = ts
      last_ts = ts

    # Extract session metadata from first user/assistant msg.
    if not info.session_id:
      sid = obj.get("sessionId")
      if sid:
        info.session_id = sid
    if not info.cwd:
      info.cwd = obj.get("cwd", "")
    if not info.git_branch:
      info.git_branch = obj.get("gitBranch", "")
    if not info.slug:
      info.slug = obj.get("slug", "")

    # Count messages and accumulate tokens from assistant msgs.
    msg = obj.get("message", {})
    if not isinstance(msg, dict):
      continue

    role = msg.get("role", "")
    if role in ("user", "assistant"):
      info.message_count += 1

    if role == "assistant" and "usage" in msg:
      if not info.model:
        info.model = msg.get("model", "")
      usage = msg["usage"]
      inp = usage.get("input_tokens", 0)
      out = usage.get("output_tokens", 0)
      cr = usage.get("cache_read_input_tokens", 0)
      cc = usage.get("cache_creation_input_tokens", 0)
      info.total_input_tokens += inp
      info.total_output_tokens += out
      info.total_cache_read += cr
      info.total_cache_create += cc
      # Track last turn's context size (all input tokens).
      info.context_tokens = inp + cr + cc

  if first_ts:
    info.started_at = first_ts
  if last_ts:
    info.last_active = last_ts

  # Calculate duration.
  if first_ts and last_ts:
    try:
      t0 = datetime.fromisoformat(
        first_ts.replace("Z", "+00:00")
      )
      t1 = datetime.fromisoformat(
        last_ts.replace("Z", "+00:00")
      )
      info.duration_ms = int((t1 - t0).total_seconds() * 1000)
    except (ValueError, TypeError):
      pass

  # Check if active based on mtime.
  info.is_active = (time.time() - mtime) < 120

  # Calculate cost and set context limit.
  if info.model:
    info.estimated_cost_usd = calculate_cost(
      info.model,
      info.total_input_tokens,
      info.total_output_tokens,
      info.total_cache_read,
      info.total_cache_create,
    )
    key = _rate_key(info.model)
    info.context_limit = _MODEL_CONTEXT_LIMIT.get(
      key, 200_000
    )

  return info


def discover_sessions(active_threshold_s=120):
  """Discover all Claude sessions from JSONL files.

  Globs ~/.claude/projects/ for session JSONL files, parses
  each in quick mode, and returns a list sorted by activity.

  Args:
    active_threshold_s: Seconds since last modification to
                        consider a session active.

  Returns:
    List of SessionInfo, active sessions first, then by
    last_active descending.
  """
  if not PROJECTS_DIR.exists():
    return []

  sessions = []
  # Find top-level JSONL files (not subagents).
  for jsonl in PROJECTS_DIR.glob("*/*.jsonl"):
    if "subagents" in str(jsonl):
      continue
    info = parse_session_file(jsonl, quick=True)
    if info.session_id:
      mtime = jsonl.stat().st_mtime
      info.is_active = (
        (time.time() - mtime) < active_threshold_s
      )
      sessions.append(info)

  # Sort: active first, then by last_active desc.
  sessions.sort(
    key=lambda s: (not s.is_active, s.last_active or ""),
    reverse=False,
  )
  # Reverse non-active so most recent is first.
  active = [s for s in sessions if s.is_active]
  inactive = [s for s in sessions if not s.is_active]
  inactive.sort(key=lambda s: s.last_active or "", reverse=True)
  return active + inactive


def load_stats_cache():
  """Parse ~/.claude/stats-cache.json into a UsageSummary.

  Returns:
    UsageSummary with totals and daily activity.
  """
  summary = UsageSummary()
  if not STATS_CACHE.exists():
    return summary

  try:
    with open(STATS_CACHE) as f:
      data = json.load(f)
  except (json.JSONDecodeError, OSError):
    return summary

  summary.total_sessions = data.get("totalSessions", 0)
  summary.total_messages = data.get("totalMessages", 0)

  # Parse per-model usage.
  model_usage = data.get("modelUsage", {})
  for model_id, usage in model_usage.items():
    mu = ModelUsage(model=model_id)
    mu.input_tokens = usage.get("inputTokens", 0)
    mu.output_tokens = usage.get("outputTokens", 0)
    mu.cache_read_tokens = usage.get(
      "cacheReadInputTokens", 0
    )
    mu.cache_create_tokens = usage.get(
      "cacheCreationInputTokens", 0
    )
    mu.cost_usd = calculate_cost(
      model_id,
      mu.input_tokens,
      mu.output_tokens,
      mu.cache_read_tokens,
      mu.cache_create_tokens,
    )
    summary.by_model[model_id] = mu
    summary.total_cost_usd += mu.cost_usd

  summary.total_cost_usd = round(summary.total_cost_usd, 2)

  # Parse daily activity (last 14 days for sparkline).
  daily = data.get("dailyActivity", [])
  summary.daily_activity = daily[-14:] if daily else []

  return summary
