"""Shared age-bucket styling for dashboard panels."""


def age_style(bucket):
  """Return a Rich color string for a given age bucket.

  Args:
    bucket: One of 'active', 'recent', 'stale'.

  Returns:
    Rich color string.
  """
  if bucket == "active":
    return "#66bb6a"
  if bucket == "recent":
    return "#fdd835"
  return "#666666"


def age_label(age_min):
  """Return a human-readable age string.

  Args:
    age_min: Age in minutes.

  Returns:
    Formatted string like '3m ago', '2h ago', or '1d ago'.
  """
  if age_min < 60:
    return f"{int(age_min)}m ago"
  hours = age_min / 60
  if hours < 24:
    return f"{int(hours)}h ago"
  days = hours / 24
  return f"{int(days)}d ago"


def ws_bucket(age_min):
  """Return the age bucket for workspace/stage panels.

  Buckets:
    active  — < 60 min
    recent  — 60-240 min (1-4h)
    stale   — > 240 min

  Args:
    age_min: Age in minutes.

  Returns:
    Bucket string.
  """
  if age_min < 60:
    return "active"
  if age_min < 240:
    return "recent"
  return "stale"


def agent_bucket(is_active, age_min):
  """Return the age bucket for agent panel rows.

  Buckets:
    active  — currently running
    recent  — last active < 10 min ago
    stale   — last active < 30 min ago
    idle    — everything else

  Args:
    is_active: Whether the agent session is currently active.
    age_min: Minutes since last activity.

  Returns:
    Bucket string.
  """
  if is_active:
    return "active"
  if age_min < 10:
    return "recent"
  if age_min < 30:
    return "stale"
  return "idle"
