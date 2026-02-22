"""Tests for lib.db — SQLite state layer."""

import json

import pytest

from lib import db


@pytest.fixture
def tmp_db(tmp_path):
  """Create a temporary database and migrate it."""
  path = tmp_path / "test.db"
  db.migrate(db_path=str(path))
  return str(path)


@pytest.fixture
def seeded_db(tmp_db):
  """DB with a pipeline and one run."""
  db.define_pipeline("ws1", [
    {"name": "test", "step_type": "agent"},
    {"name": "push_to_github", "step_type": "script"},
  ], db_path=tmp_db)
  run_id = db.create_run(
    "ws1", "manual", ["repo-a"],
    {"repo-a": "abc123"},
    db_path=tmp_db,
  )
  return tmp_db, run_id


class TestMigrate:
  """Schema migration tests."""

  def test_creates_tables(self, tmp_db):
    """migrate() creates all expected tables."""
    with db._connect(tmp_db) as conn:
      tables = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' ORDER BY name"
      ).fetchall()
    names = {r["name"] for r in tables}
    assert "runs" in names
    assert "steps" in names
    assert "events" in names
    assert "agent_output" in names
    assert "branch_refs" in names
    assert "pipeline_steps" in names

  def test_idempotent(self, tmp_db):
    """migrate() can be called multiple times safely."""
    db.migrate(db_path=tmp_db)
    db.migrate(db_path=tmp_db)


class TestDefinePipeline:
  """Pipeline definition tests."""

  def test_define_and_get(self, tmp_db):
    """define_pipeline stores and get_pipeline retrieves."""
    db.define_pipeline("ws1", [
      {"name": "test", "step_type": "agent",
       "timeout_secs": 900},
      {"name": "push_to_github", "step_type": "script"},
    ], db_path=tmp_db)
    steps = db.get_pipeline("ws1", db_path=tmp_db)
    assert len(steps) == 2
    assert steps[0]["name"] == "test"
    assert steps[0]["step_type"] == "agent"
    assert steps[0]["timeout_secs"] == 900
    assert steps[1]["name"] == "push_to_github"
    assert steps[1]["seq"] == 1

  def test_replace_pipeline(self, tmp_db):
    """define_pipeline replaces existing definition."""
    db.define_pipeline("ws1", [
      {"name": "test", "step_type": "agent"},
    ], db_path=tmp_db)
    db.define_pipeline("ws1", [
      {"name": "review", "step_type": "agent"},
      {"name": "create_pr", "step_type": "script"},
    ], db_path=tmp_db)
    steps = db.get_pipeline("ws1", db_path=tmp_db)
    assert len(steps) == 2
    assert steps[0]["name"] == "review"

  def test_empty_pipeline(self, tmp_db):
    """get_pipeline returns empty list for unknown ws."""
    steps = db.get_pipeline("nope", db_path=tmp_db)
    assert steps == []


class TestCreateRun:
  """Run creation tests."""

  def test_creates_run_with_steps(self, tmp_db):
    """create_run inserts run and copies pipeline steps."""
    db.define_pipeline("ws1", [
      {"name": "test", "step_type": "agent"},
      {"name": "push", "step_type": "script"},
    ], db_path=tmp_db)
    run_id = db.create_run(
      "ws1", "push", ["repo-a", "repo-b"],
      {"repo-a": "aaa", "repo-b": "bbb"},
      db_path=tmp_db,
    )
    assert run_id is not None
    run = db.get_run(run_id, db_path=tmp_db)
    assert run["workspace"] == "ws1"
    assert run["status"] == "queued"
    assert json.loads(run["repos_json"]) == [
      "repo-a", "repo-b"
    ]
    steps = db.get_run_steps(run_id, db_path=tmp_db)
    assert len(steps) == 2
    assert steps[0]["name"] == "test"
    assert steps[0]["status"] == "pending"
    assert steps[1]["name"] == "push"

  def test_idempotent_push_trigger(self, tmp_db):
    """Duplicate push trigger returns None."""
    db.define_pipeline("ws1", [
      {"name": "test", "step_type": "agent"},
    ], db_path=tmp_db)
    refs = {"repo-a": "aaa"}
    rid1 = db.create_run(
      "ws1", "push", ["repo-a"], refs, db_path=tmp_db,
    )
    rid2 = db.create_run(
      "ws1", "push", ["repo-a"], refs, db_path=tmp_db,
    )
    assert rid1 is not None
    assert rid2 is None

  def test_manual_trigger_not_deduped(self, tmp_db):
    """Manual triggers are never deduplicated."""
    db.define_pipeline("ws1", [
      {"name": "test", "step_type": "agent"},
    ], db_path=tmp_db)
    refs = {"repo-a": "aaa"}
    rid1 = db.create_run(
      "ws1", "manual", ["repo-a"], refs, db_path=tmp_db,
    )
    rid2 = db.create_run(
      "ws1", "manual", ["repo-a"], refs, db_path=tmp_db,
    )
    assert rid1 is not None
    assert rid2 is not None
    assert rid1 != rid2

  def test_logs_creation_event(self, tmp_db):
    """create_run logs a queued event."""
    db.define_pipeline("ws1", [
      {"name": "test", "step_type": "agent"},
    ], db_path=tmp_db)
    run_id = db.create_run(
      "ws1", "manual", ["repo-a"], {},
      db_path=tmp_db,
    )
    events = db.get_events(
      entity="run", entity_id=run_id,
      db_path=tmp_db,
    )
    assert len(events) == 1
    assert events[0]["new_status"] == "queued"


class TestAdvanceStep:
  """Step state machine tests."""

  def test_valid_transitions(self, seeded_db):
    """Steps can move through valid transitions."""
    path, run_id = seeded_db
    steps = db.get_run_steps(run_id, db_path=path)
    sid = steps[0]["id"]
    # pending -> queued
    db.advance_step(sid, "queued", db_path=path)
    assert db.get_step(sid, db_path=path)["status"] == "queued"
    # queued -> running
    db.advance_step(sid, "running", db_path=path)
    step = db.get_step(sid, db_path=path)
    assert step["status"] == "running"
    assert step["started_at"] is not None
    # running -> completed
    db.advance_step(
      sid, "completed", result_json='{"ok":true}',
      cost_usd=0.05, num_turns=3, db_path=path,
    )
    step = db.get_step(sid, db_path=path)
    assert step["status"] == "completed"
    assert step["finished_at"] is not None
    assert step["cost_usd"] == 0.05
    assert step["num_turns"] == 3

  def test_invalid_transition_raises(self, seeded_db):
    """Invalid transitions raise ValueError."""
    path, run_id = seeded_db
    steps = db.get_run_steps(run_id, db_path=path)
    sid = steps[0]["id"]
    with pytest.raises(ValueError, match="Invalid"):
      db.advance_step(sid, "completed", db_path=path)

  def test_pause_resume(self, seeded_db):
    """Steps can be paused and resumed."""
    path, run_id = seeded_db
    steps = db.get_run_steps(run_id, db_path=path)
    sid = steps[0]["id"]
    db.advance_step(sid, "queued", db_path=path)
    db.advance_step(sid, "running", db_path=path)
    db.advance_step(sid, "paused", db_path=path)
    assert db.get_step(sid, db_path=path)["status"] == "paused"
    db.advance_step(sid, "queued", db_path=path)
    assert db.get_step(sid, db_path=path)["status"] == "queued"

  def test_retry_failed(self, seeded_db):
    """Failed steps can be retried (back to queued)."""
    path, run_id = seeded_db
    steps = db.get_run_steps(run_id, db_path=path)
    sid = steps[0]["id"]
    db.advance_step(sid, "queued", db_path=path)
    db.advance_step(sid, "running", db_path=path)
    db.advance_step(
      sid, "failed", error="timeout", db_path=path,
    )
    assert db.get_step(sid, db_path=path)["status"] == "failed"
    db.advance_step(sid, "queued", reason="retry", db_path=path)
    assert db.get_step(sid, db_path=path)["status"] == "queued"

  def test_skip(self, seeded_db):
    """Queued steps can be skipped."""
    path, run_id = seeded_db
    steps = db.get_run_steps(run_id, db_path=path)
    sid = steps[0]["id"]
    db.advance_step(sid, "queued", db_path=path)
    db.advance_step(sid, "skipped", db_path=path)
    step = db.get_step(sid, db_path=path)
    assert step["status"] == "skipped"
    assert step["finished_at"] is not None

  def test_not_found_raises(self, seeded_db):
    """Advancing a nonexistent step raises ValueError."""
    path, _ = seeded_db
    with pytest.raises(ValueError, match="not found"):
      db.advance_step(9999, "running", db_path=path)

  def test_logs_events(self, seeded_db):
    """Each transition logs an event."""
    path, run_id = seeded_db
    steps = db.get_run_steps(run_id, db_path=path)
    sid = steps[0]["id"]
    db.advance_step(sid, "queued", db_path=path)
    db.advance_step(sid, "running", db_path=path)
    events = db.get_events(
      entity="step", entity_id=sid, db_path=path,
    )
    assert len(events) == 2
    assert events[0]["old_status"] == "queued"
    assert events[0]["new_status"] == "running"


class TestAdvanceRun:
  """Run status computation tests."""

  def test_all_completed_passes(self, seeded_db):
    """Run passes when all steps complete."""
    path, run_id = seeded_db
    steps = db.get_run_steps(run_id, db_path=path)
    for s in steps:
      db.advance_step(s["id"], "queued", db_path=path)
      db.advance_step(s["id"], "running", db_path=path)
      db.advance_step(s["id"], "completed", db_path=path)
    # Transition run from queued -> running first.
    with db._connect(path) as conn:
      conn.execute(
        "UPDATE runs SET status = 'running' WHERE id = ?",
        (run_id,),
      )
    status = db.advance_run(run_id, db_path=path)
    assert status == "passed"
    run = db.get_run(run_id, db_path=path)
    assert run["finished_at"] is not None

  def test_any_failed_fails(self, seeded_db):
    """Run fails when any step fails."""
    path, run_id = seeded_db
    steps = db.get_run_steps(run_id, db_path=path)
    db.advance_step(steps[0]["id"], "queued", db_path=path)
    db.advance_step(
      steps[0]["id"], "running", db_path=path
    )
    db.advance_step(steps[0]["id"], "failed", db_path=path)
    db.advance_step(steps[1]["id"], "queued", db_path=path)
    db.advance_step(
      steps[1]["id"], "running", db_path=path
    )
    db.advance_step(
      steps[1]["id"], "completed", db_path=path
    )
    with db._connect(path) as conn:
      conn.execute(
        "UPDATE runs SET status = 'running' WHERE id = ?",
        (run_id,),
      )
    status = db.advance_run(run_id, db_path=path)
    assert status == "failed"

  def test_queued_to_running(self, seeded_db):
    """Run transitions from queued to running."""
    path, run_id = seeded_db
    steps = db.get_run_steps(run_id, db_path=path)
    db.advance_step(steps[0]["id"], "queued", db_path=path)
    db.advance_step(
      steps[0]["id"], "running", db_path=path
    )
    status = db.advance_run(run_id, db_path=path)
    assert status == "running"
    run = db.get_run(run_id, db_path=path)
    assert run["started_at"] is not None

  def test_completed_and_skipped_passes(self, seeded_db):
    """Run passes when steps are completed or skipped."""
    path, run_id = seeded_db
    steps = db.get_run_steps(run_id, db_path=path)
    db.advance_step(steps[0]["id"], "queued", db_path=path)
    db.advance_step(
      steps[0]["id"], "running", db_path=path
    )
    db.advance_step(
      steps[0]["id"], "completed", db_path=path
    )
    db.advance_step(steps[1]["id"], "queued", db_path=path)
    db.advance_step(
      steps[1]["id"], "skipped", db_path=path
    )
    with db._connect(path) as conn:
      conn.execute(
        "UPDATE runs SET status = 'running' WHERE id = ?",
        (run_id,),
      )
    status = db.advance_run(run_id, db_path=path)
    assert status == "passed"


class TestGetNextQueuedRun:
  """Queued run retrieval tests."""

  def test_returns_oldest(self, tmp_db):
    """Returns the oldest queued run."""
    db.define_pipeline("ws1", [
      {"name": "test", "step_type": "agent"},
    ], db_path=tmp_db)
    r1 = db.create_run(
      "ws1", "manual", [], {}, db_path=tmp_db,
    )
    db.create_run(
      "ws1", "manual", [], {}, db_path=tmp_db,
    )
    result = db.get_next_queued_run(db_path=tmp_db)
    assert result["id"] == r1

  def test_returns_none_when_empty(self, tmp_db):
    """Returns None when no queued runs exist."""
    assert db.get_next_queued_run(db_path=tmp_db) is None


class TestListRuns:
  """Run listing tests."""

  def test_list_all(self, tmp_db):
    """list_runs returns all runs newest first."""
    db.define_pipeline("ws1", [
      {"name": "test", "step_type": "agent"},
    ], db_path=tmp_db)
    db.create_run("ws1", "manual", [], {}, db_path=tmp_db)
    db.create_run("ws1", "manual", [], {}, db_path=tmp_db)
    runs = db.list_runs(db_path=tmp_db)
    assert len(runs) == 2
    assert runs[0]["id"] > runs[1]["id"]

  def test_filter_by_workspace(self, tmp_db):
    """list_runs filters by workspace."""
    db.define_pipeline("ws1", [
      {"name": "test", "step_type": "agent"},
    ], db_path=tmp_db)
    db.define_pipeline("ws2", [
      {"name": "test", "step_type": "agent"},
    ], db_path=tmp_db)
    db.create_run("ws1", "manual", [], {}, db_path=tmp_db)
    db.create_run("ws2", "manual", [], {}, db_path=tmp_db)
    runs = db.list_runs("ws1", db_path=tmp_db)
    assert len(runs) == 1
    assert runs[0]["workspace"] == "ws1"


class TestOutput:
  """Agent output tests."""

  def test_record_and_get(self, seeded_db):
    """record_output stores and get_output retrieves."""
    path, run_id = seeded_db
    steps = db.get_run_steps(run_id, db_path=path)
    sid = steps[0]["id"]
    lines = [
      {"line_no": 0, "kind": "text",
       "content": "hello", "meta": {}},
      {"line_no": 1, "kind": "tool_use",
       "content": "read", "meta": {"input": "/foo"}},
    ]
    db.record_output(sid, lines, db_path=path)
    result = db.get_output(sid, db_path=path)
    assert len(result) == 2
    assert result[0]["content"] == "hello"
    assert result[1]["kind"] == "tool_use"

  def test_from_line_filter(self, seeded_db):
    """get_output respects from_line parameter."""
    path, run_id = seeded_db
    steps = db.get_run_steps(run_id, db_path=path)
    sid = steps[0]["id"]
    lines = [
      {"line_no": 0, "kind": "text", "content": "a"},
      {"line_no": 1, "kind": "text", "content": "b"},
      {"line_no": 2, "kind": "text", "content": "c"},
    ]
    db.record_output(sid, lines, db_path=path)
    result = db.get_output(sid, from_line=1, db_path=path)
    assert len(result) == 2
    assert result[0]["content"] == "b"


class TestBranchRefs:
  """Branch ref snapshot tests."""

  def test_save_and_load(self, tmp_db):
    """save_refs stores and load_refs retrieves."""
    refs = {
      "repo-a:main": "aaa111",
      "repo-b:feature": "bbb222",
    }
    db.save_refs(refs, db_path=tmp_db)
    loaded = db.load_refs(db_path=tmp_db)
    assert loaded == refs

  def test_upsert(self, tmp_db):
    """save_refs updates existing refs."""
    db.save_refs({"repo-a:main": "old"}, db_path=tmp_db)
    db.save_refs({"repo-a:main": "new"}, db_path=tmp_db)
    loaded = db.load_refs(db_path=tmp_db)
    assert loaded["repo-a:main"] == "new"


class TestEvents:
  """Event log tests."""

  def test_log_and_query(self, tmp_db):
    """log_event stores and get_events retrieves."""
    db.log_event(
      "run", 1, None, "queued",
      reason="test", db_path=tmp_db,
    )
    db.log_event(
      "step", 10, "pending", "running",
      db_path=tmp_db,
    )
    all_events = db.get_events(db_path=tmp_db)
    assert len(all_events) == 2
    run_events = db.get_events(
      entity="run", db_path=tmp_db,
    )
    assert len(run_events) == 1

  def test_newest_first(self, tmp_db):
    """Events are returned newest first."""
    db.log_event("run", 1, None, "queued", db_path=tmp_db)
    db.log_event("run", 1, "queued", "running",
                 db_path=tmp_db)
    events = db.get_events(db_path=tmp_db)
    assert events[0]["new_status"] == "running"
    assert events[1]["new_status"] == "queued"
