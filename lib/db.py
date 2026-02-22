"""SQLite state layer for takt pipeline.

Single database at .state/takt.db with WAL mode.
All pipeline state — runs, steps, events, agent output,
branch refs, and pipeline definitions — lives here.

Thread-safe: one connection per call with short-lived
transactions. WAL mode allows concurrent readers.
"""

import hashlib
import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from lib.config import STATE_DIR

log = logging.getLogger("takt.db")

DB_PATH = STATE_DIR / "takt.db"

# Valid state transitions for runs and steps.
RUN_TRANSITIONS = {
  "queued": {"running", "cancelled"},
  "running": {"passed", "failed", "cancelled"},
}
STEP_TRANSITIONS = {
  "pending": {"queued"},
  "queued": {"running", "skipped", "cancelled"},
  "running": {"completed", "failed", "paused", "cancelled"},
  "paused": {"queued"},
  "failed": {"queued"},
}

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS pipeline_steps (
  id INTEGER PRIMARY KEY,
  workspace TEXT NOT NULL,
  seq INTEGER NOT NULL,
  name TEXT NOT NULL,
  step_type TEXT NOT NULL,
  config_json TEXT NOT NULL DEFAULT '{}',
  timeout_secs INTEGER NOT NULL DEFAULT 1800,
  UNIQUE(workspace, seq)
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  trigger TEXT NOT NULL DEFAULT 'push',
  repos_json TEXT NOT NULL DEFAULT '[]',
  head_refs_json TEXT NOT NULL DEFAULT '{}',
  worktree_dir TEXT,
  created_at TEXT NOT NULL DEFAULT
    (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  started_at TEXT,
  finished_at TEXT,
  trigger_key TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  seq INTEGER NOT NULL,
  name TEXT NOT NULL,
  step_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  config_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT,
  error TEXT,
  timeout_secs INTEGER NOT NULL DEFAULT 1800,
  started_at TEXT,
  finished_at TEXT,
  cost_usd REAL DEFAULT 0.0,
  num_turns INTEGER DEFAULT 0,
  UNIQUE(run_id, seq)
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL DEFAULT
    (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  entity TEXT NOT NULL,
  entity_id INTEGER NOT NULL,
  old_status TEXT,
  new_status TEXT NOT NULL,
  reason TEXT,
  context_json TEXT
);

CREATE TABLE IF NOT EXISTS agent_output (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  step_id INTEGER NOT NULL REFERENCES steps(id),
  line_no INTEGER NOT NULL,
  kind TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '',
  meta_json TEXT NOT NULL DEFAULT '{}',
  ts TEXT NOT NULL DEFAULT
    (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS branch_refs (
  repo TEXT NOT NULL,
  branch TEXT NOT NULL,
  commit_hash TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT
    (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY(repo, branch)
);
"""


@contextmanager
def _connect(db_path=None):
  """Yield a SQLite connection with WAL mode.

  Auto-commits on clean exit, rolls back on exception.

  Args:
    db_path: Override path for testing.

  Yields:
    sqlite3.Connection with row_factory=sqlite3.Row.
  """
  path = db_path or DB_PATH
  Path(path).parent.mkdir(parents=True, exist_ok=True)
  conn = sqlite3.connect(str(path))
  conn.row_factory = sqlite3.Row
  conn.execute("PRAGMA journal_mode=WAL")
  conn.execute("PRAGMA foreign_keys=ON")
  try:
    yield conn
    conn.commit()
  except Exception:
    conn.rollback()
    raise
  finally:
    conn.close()


def migrate(db_path=None):
  """Create or update the schema.

  Safe to call repeatedly — uses CREATE IF NOT EXISTS.

  Args:
    db_path: Override path for testing.
  """
  with _connect(db_path) as conn:
    conn.executescript(_SCHEMA)
  log.info("Database migrated: %s", db_path or DB_PATH)


def _trigger_key(workspace, refs):
  """Compute a dedup key for a push trigger.

  Args:
    workspace: Workspace name.
    refs: Dict mapping repo to commit hash.

  Returns:
    Hex digest string.
  """
  raw = json.dumps(
    {"ws": workspace, "refs": refs}, sort_keys=True
  )
  return hashlib.sha256(raw.encode()).hexdigest()[:16]


def create_run(workspace, trigger, repos, refs,
               db_path=None):
  """Create a pipeline run with steps from pipeline_steps.

  Idempotent for push triggers via trigger_key hash.

  Args:
    workspace: Workspace name.
    trigger: Trigger type ("push", "manual").
    repos: List of repo names.
    refs: Dict mapping repo to commit hash.
    db_path: Override path for testing.

  Returns:
    Run ID (integer), or None if duplicate.
  """
  key = _trigger_key(workspace, refs) if trigger == "push" else None
  with _connect(db_path) as conn:
    # Check for duplicate push trigger.
    if key:
      row = conn.execute(
        "SELECT id FROM runs WHERE trigger_key = ?",
        (key,),
      ).fetchone()
      if row:
        log.debug(
          "Duplicate trigger for %s, key=%s",
          workspace, key,
        )
        return None
    # Insert run.
    cur = conn.execute(
      "INSERT INTO runs "
      "(workspace, trigger, repos_json, head_refs_json,"
      " trigger_key) "
      "VALUES (?, ?, ?, ?, ?)",
      (
        workspace,
        trigger,
        json.dumps(repos),
        json.dumps(refs),
        key,
      ),
    )
    run_id = cur.lastrowid
    # Copy pipeline_steps into steps.
    psteps = conn.execute(
      "SELECT seq, name, step_type, config_json,"
      " timeout_secs "
      "FROM pipeline_steps WHERE workspace = ? "
      "ORDER BY seq",
      (workspace,),
    ).fetchall()
    for ps in psteps:
      conn.execute(
        "INSERT INTO steps "
        "(run_id, seq, name, step_type, config_json,"
        " timeout_secs) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
          run_id,
          ps["seq"],
          ps["name"],
          ps["step_type"],
          ps["config_json"],
          ps["timeout_secs"],
        ),
      )
    log_event(
      "run", run_id, None, "queued",
      f"{trigger} from {workspace}",
      conn=conn,
    )
  log.info("Created run %d for %s", run_id, workspace)
  return run_id


def advance_step(step_id, new_status, reason=None,
                 result_json=None, error=None,
                 cost_usd=None, num_turns=None,
                 db_path=None):
  """Transition a step to a new status.

  Validates the transition against STEP_TRANSITIONS.
  Logs an event. Updates timestamps and optional fields.

  Args:
    step_id: Step row ID.
    new_status: Target status string.
    reason: Human-readable reason for the transition.
    result_json: JSON string with step results.
    error: Error message string.
    cost_usd: LLM cost for this step.
    num_turns: Number of agent turns.
    db_path: Override path for testing.

  Raises:
    ValueError: If the transition is not valid.
  """
  with _connect(db_path) as conn:
    row = conn.execute(
      "SELECT status FROM steps WHERE id = ?",
      (step_id,),
    ).fetchone()
    if row is None:
      raise ValueError(f"Step {step_id} not found")
    old = row["status"]
    allowed = STEP_TRANSITIONS.get(old, set())
    if new_status not in allowed:
      raise ValueError(
        f"Invalid step transition: {old} -> {new_status}"
      )
    updates = ["status = ?"]
    params = [new_status]
    if new_status == "running":
      updates.append(
        "started_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')"
      )
    if new_status in (
      "completed", "failed", "skipped", "cancelled"
    ):
      updates.append(
        "finished_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')"
      )
    if result_json is not None:
      updates.append("result_json = ?")
      params.append(result_json)
    if error is not None:
      updates.append("error = ?")
      params.append(error)
    if cost_usd is not None:
      updates.append("cost_usd = ?")
      params.append(cost_usd)
    if num_turns is not None:
      updates.append("num_turns = ?")
      params.append(num_turns)
    params.append(step_id)
    conn.execute(
      f"UPDATE steps SET {', '.join(updates)} "
      f"WHERE id = ?",
      params,
    )
    log_event(
      "step", step_id, old, new_status, reason,
      conn=conn,
    )


def advance_run(run_id, db_path=None):
  """Recompute run status from step statuses.

  Transitions:
    - All steps completed -> passed
    - Any step failed -> failed
    - Any step cancelled -> cancelled
    - Otherwise stays running

  Also sets started_at when first entering running,
  and finished_at when reaching a terminal state.

  Args:
    run_id: Run row ID.
    db_path: Override path for testing.

  Returns:
    New run status string.
  """
  with _connect(db_path) as conn:
    run = conn.execute(
      "SELECT status FROM runs WHERE id = ?",
      (run_id,),
    ).fetchone()
    if run is None:
      raise ValueError(f"Run {run_id} not found")
    old_status = run["status"]
    steps = conn.execute(
      "SELECT status FROM steps WHERE run_id = ?",
      (run_id,),
    ).fetchall()
    statuses = {s["status"] for s in steps}
    if not steps:
      return old_status
    if statuses == {"completed"}:
      new_status = "passed"
    elif "cancelled" in statuses:
      new_status = "cancelled"
    elif "failed" in statuses:
      new_status = "failed"
    elif statuses <= {"completed", "skipped"}:
      new_status = "passed"
    else:
      new_status = "running"
    if new_status == old_status:
      return old_status
    allowed = RUN_TRANSITIONS.get(old_status, set())
    if new_status not in allowed:
      return old_status
    updates = ["status = ?"]
    params = [new_status]
    if new_status == "running" and old_status == "queued":
      updates.append(
        "started_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')"
      )
    if new_status in ("passed", "failed", "cancelled"):
      updates.append(
        "finished_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')"
      )
    params.append(run_id)
    conn.execute(
      f"UPDATE runs SET {', '.join(updates)} WHERE id = ?",
      params,
    )
    log_event(
      "run", run_id, old_status, new_status,
      f"computed from steps: {statuses}",
      conn=conn,
    )
    return new_status


def get_next_queued_run(db_path=None):
  """Get the oldest queued run.

  Args:
    db_path: Override path for testing.

  Returns:
    Dict with run fields, or None.
  """
  with _connect(db_path) as conn:
    row = conn.execute(
      "SELECT * FROM runs WHERE status = 'queued' "
      "ORDER BY created_at ASC LIMIT 1",
    ).fetchone()
    return dict(row) if row else None


def get_run(run_id, db_path=None):
  """Get a run by ID.

  Args:
    run_id: Run row ID.
    db_path: Override path for testing.

  Returns:
    Dict with run fields, or None.
  """
  with _connect(db_path) as conn:
    row = conn.execute(
      "SELECT * FROM runs WHERE id = ?", (run_id,),
    ).fetchone()
    return dict(row) if row else None


def get_run_steps(run_id, db_path=None):
  """Get all steps for a run, ordered by seq.

  Args:
    run_id: Run row ID.
    db_path: Override path for testing.

  Returns:
    List of step dicts.
  """
  with _connect(db_path) as conn:
    rows = conn.execute(
      "SELECT * FROM steps WHERE run_id = ? "
      "ORDER BY seq",
      (run_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_runs(workspace=None, limit=20, db_path=None):
  """List runs, newest first.

  Args:
    workspace: Optional workspace filter.
    limit: Max number of runs to return.
    db_path: Override path for testing.

  Returns:
    List of run dicts.
  """
  with _connect(db_path) as conn:
    if workspace:
      rows = conn.execute(
        "SELECT * FROM runs WHERE workspace = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (workspace, limit),
      ).fetchall()
    else:
      rows = conn.execute(
        "SELECT * FROM runs "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
      ).fetchall()
    return [dict(r) for r in rows]


def list_agent_steps(limit=50, db_path=None):
  """List agent steps from the latest run per workspace.

  Only returns steps from the most recent run for each
  workspace, so the same role doesn't appear twice.

  Args:
    limit: Max rows to return.
    db_path: Override path for testing.

  Returns:
    List of dicts with step fields plus workspace.
  """
  with _connect(db_path) as conn:
    rows = conn.execute(
      "SELECT s.*, r.workspace FROM steps s "
      "JOIN runs r ON s.run_id = r.id "
      "WHERE s.step_type = 'agent' "
      "AND r.id = ("
      "  SELECT MAX(r2.id) FROM runs r2 "
      "  WHERE r2.workspace = r.workspace"
      ") "
      "ORDER BY s.seq LIMIT ?",
      (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def record_output(step_id, lines, db_path=None):
  """Batch insert agent output lines.

  Args:
    step_id: Step row ID.
    lines: List of dicts with keys: line_no, kind,
      content, meta.
    db_path: Override path for testing.
  """
  with _connect(db_path) as conn:
    conn.executemany(
      "INSERT INTO agent_output "
      "(step_id, line_no, kind, content, meta_json) "
      "VALUES (?, ?, ?, ?, ?)",
      [
        (
          step_id,
          line["line_no"],
          line["kind"],
          line.get("content", ""),
          json.dumps(line.get("meta", {})),
        )
        for line in lines
      ],
    )


def get_output(step_id, from_line=0, db_path=None):
  """Get agent output lines for a step.

  Args:
    step_id: Step row ID.
    from_line: Only return lines >= this line_no.
    db_path: Override path for testing.

  Returns:
    List of output line dicts.
  """
  with _connect(db_path) as conn:
    rows = conn.execute(
      "SELECT line_no, kind, content, meta_json, ts "
      "FROM agent_output "
      "WHERE step_id = ? AND line_no >= ? "
      "ORDER BY line_no",
      (step_id, from_line),
    ).fetchall()
    return [
      {
        "line_no": r["line_no"],
        "kind": r["kind"],
        "content": r["content"],
        "meta": json.loads(r["meta_json"]),
        "ts": r["ts"],
      }
      for r in rows
    ]


def define_pipeline(workspace, steps, db_path=None):
  """Set pipeline_steps rows for a workspace.

  Replaces any existing pipeline definition.

  Args:
    workspace: Workspace name.
    steps: List of dicts with keys: name, step_type,
      and optionally config_json, timeout_secs.
    db_path: Override path for testing.
  """
  with _connect(db_path) as conn:
    conn.execute(
      "DELETE FROM pipeline_steps WHERE workspace = ?",
      (workspace,),
    )
    for i, step in enumerate(steps):
      conn.execute(
        "INSERT INTO pipeline_steps "
        "(workspace, seq, name, step_type, config_json,"
        " timeout_secs) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
          workspace,
          i,
          step["name"],
          step["step_type"],
          json.dumps(step.get("config", {})),
          step.get("timeout_secs", 1800),
        ),
      )


def get_pipeline(workspace, db_path=None):
  """Get pipeline steps for a workspace.

  Args:
    workspace: Workspace name.
    db_path: Override path for testing.

  Returns:
    List of pipeline step dicts, ordered by seq.
  """
  with _connect(db_path) as conn:
    rows = conn.execute(
      "SELECT * FROM pipeline_steps "
      "WHERE workspace = ? ORDER BY seq",
      (workspace,),
    ).fetchall()
    return [dict(r) for r in rows]


def save_refs(refs, db_path=None):
  """Save branch ref snapshots.

  Args:
    refs: Dict mapping "repo:branch" to commit hash.
    db_path: Override path for testing.
  """
  with _connect(db_path) as conn:
    for key, commit_hash in refs.items():
      repo, branch = key.split(":", 1)
      conn.execute(
        "INSERT OR REPLACE INTO branch_refs "
        "(repo, branch, commit_hash) "
        "VALUES (?, ?, ?)",
        (repo, branch, commit_hash),
      )


def load_refs(db_path=None):
  """Load branch ref snapshots.

  Args:
    db_path: Override path for testing.

  Returns:
    Dict mapping "repo:branch" to commit hash.
  """
  with _connect(db_path) as conn:
    rows = conn.execute(
      "SELECT repo, branch, commit_hash "
      "FROM branch_refs",
    ).fetchall()
    return {
      f"{r['repo']}:{r['branch']}": r["commit_hash"]
      for r in rows
    }


def get_events(entity=None, entity_id=None, limit=50,
               db_path=None):
  """Query the event log.

  Args:
    entity: Optional entity type filter ("run", "step").
    entity_id: Optional entity ID filter.
    limit: Max events to return.
    db_path: Override path for testing.

  Returns:
    List of event dicts, newest first.
  """
  with _connect(db_path) as conn:
    clauses = []
    params = []
    if entity:
      clauses.append("entity = ?")
      params.append(entity)
    if entity_id is not None:
      clauses.append("entity_id = ?")
      params.append(entity_id)
    where = ""
    if clauses:
      where = "WHERE " + " AND ".join(clauses)
    params.append(limit)
    rows = conn.execute(
      f"SELECT * FROM events {where} "
      f"ORDER BY id DESC LIMIT ?",
      params,
    ).fetchall()
    return [dict(r) for r in rows]


def log_event(entity, entity_id, old_status, new_status,
              reason=None, context=None, conn=None,
              db_path=None):
  """Insert an event into the event log.

  Can be called with an existing connection (inside a
  transaction) or standalone.

  Args:
    entity: Entity type ("run", "step").
    entity_id: Entity row ID.
    old_status: Previous status (or None).
    new_status: New status.
    reason: Human-readable reason.
    context: Optional dict for context_json.
    conn: Existing connection (skips commit).
    db_path: Override path for testing.
  """
  ctx_json = json.dumps(context) if context else None
  if conn:
    conn.execute(
      "INSERT INTO events "
      "(entity, entity_id, old_status, new_status,"
      " reason, context_json) "
      "VALUES (?, ?, ?, ?, ?, ?)",
      (entity, entity_id, old_status, new_status,
       reason, ctx_json),
    )
  else:
    with _connect(db_path) as c:
      c.execute(
        "INSERT INTO events "
        "(entity, entity_id, old_status, new_status,"
        " reason, context_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (entity, entity_id, old_status, new_status,
         reason, ctx_json),
      )


def get_step(step_id, db_path=None):
  """Get a step by ID.

  Args:
    step_id: Step row ID.
    db_path: Override path for testing.

  Returns:
    Dict with step fields, or None.
  """
  with _connect(db_path) as conn:
    row = conn.execute(
      "SELECT * FROM steps WHERE id = ?", (step_id,),
    ).fetchone()
    return dict(row) if row else None


def set_run_worktree(run_id, worktree_dir, db_path=None):
  """Set the worktree directory for a run.

  Args:
    run_id: Run row ID.
    worktree_dir: Path to the run's worktree directory.
    db_path: Override path for testing.
  """
  with _connect(db_path) as conn:
    conn.execute(
      "UPDATE runs SET worktree_dir = ? WHERE id = ?",
      (worktree_dir, run_id),
    )
