"""Tests for meta agent DB operations."""

import sqlite3

import pytest

from lib import db


@pytest.fixture
def tmp_db(tmp_path):
  """Create a temporary database and migrate it."""
  path = tmp_path / "test.db"
  db.migrate(db_path=str(path))
  return str(path)


@pytest.fixture
def agent_db(tmp_db):
  """DB with one meta agent."""
  aid = db.create_meta_agent(
    "test-agent",
    "A test agent",
    "Do something useful.",
    model="sonnet",
    timeout_secs=900,
    db_path=tmp_db,
  )
  return tmp_db, aid


class TestMetaAgentCRUD:
  """Meta agent create, read, update, delete tests."""

  def test_create_and_get(self, tmp_db):
    """create_meta_agent stores and get retrieves."""
    aid = db.create_meta_agent(
      "my-agent", "desc", "prompt text",
      model="opus", timeout_secs=600,
      db_path=tmp_db,
    )
    assert aid is not None
    agent = db.get_meta_agent(aid, db_path=tmp_db)
    assert agent["name"] == "my-agent"
    assert agent["description"] == "desc"
    assert agent["prompt"] == "prompt text"
    assert agent["model"] == "opus"
    assert agent["timeout_secs"] == 600

  def test_get_by_name(self, agent_db):
    """get_meta_agent_by_name finds by name."""
    path, _ = agent_db
    agent = db.get_meta_agent_by_name(
      "test-agent", db_path=path
    )
    assert agent is not None
    assert agent["name"] == "test-agent"

  def test_get_by_name_missing(self, tmp_db):
    """get_meta_agent_by_name returns None for unknown."""
    agent = db.get_meta_agent_by_name(
      "nope", db_path=tmp_db
    )
    assert agent is None

  def test_update(self, agent_db):
    """update_meta_agent changes fields."""
    path, aid = agent_db
    db.update_meta_agent(
      aid, db_path=path,
      description="updated desc",
      model="haiku",
    )
    agent = db.get_meta_agent(aid, db_path=path)
    assert agent["description"] == "updated desc"
    assert agent["model"] == "haiku"
    # Name unchanged.
    assert agent["name"] == "test-agent"

  def test_update_not_found(self, tmp_db):
    """update_meta_agent raises for unknown ID."""
    with pytest.raises(ValueError, match="not found"):
      db.update_meta_agent(
        9999, db_path=tmp_db, name="x"
      )

  def test_delete(self, agent_db):
    """delete_meta_agent removes agent and cascades."""
    path, aid = agent_db
    # Create a run and output first.
    run_id = db.create_meta_agent_run(
      aid, db_path=path
    )
    db.record_meta_output(run_id, [
      {"line_no": 0, "kind": "text", "content": "hi"},
    ], db_path=path)
    db.delete_meta_agent(aid, db_path=path)
    assert db.get_meta_agent(aid, db_path=path) is None
    assert db.list_meta_agent_runs(
      aid, db_path=path
    ) == []
    assert db.get_meta_output(
      run_id, db_path=path
    ) == []

  def test_list(self, tmp_db):
    """list_meta_agents returns all agents by name."""
    db.create_meta_agent(
      "bravo", "b", "p", db_path=tmp_db
    )
    db.create_meta_agent(
      "alpha", "a", "p", db_path=tmp_db
    )
    agents = db.list_meta_agents(db_path=tmp_db)
    # Defaults are seeded + 2 new ones.
    names = [a["name"] for a in agents]
    assert "alpha" in names
    assert "bravo" in names
    # Verify ordering (alphabetical).
    assert names == sorted(names)

  def test_unique_name_constraint(self, agent_db):
    """Duplicate name raises IntegrityError."""
    path, _ = agent_db
    with pytest.raises(sqlite3.IntegrityError):
      db.create_meta_agent(
        "test-agent", "dup", "p", db_path=path
      )


class TestMetaAgentRuns:
  """Meta agent run lifecycle tests."""

  def test_create_run(self, agent_db):
    """create_meta_agent_run creates a queued run."""
    path, aid = agent_db
    run_id = db.create_meta_agent_run(
      aid, db_path=path
    )
    assert run_id is not None
    run = db.get_meta_agent_run(
      run_id, db_path=path
    )
    assert run["status"] == "queued"
    assert run["meta_agent_id"] == aid

  def test_advance_to_running(self, agent_db):
    """Run transitions from queued to running."""
    path, aid = agent_db
    run_id = db.create_meta_agent_run(
      aid, db_path=path
    )
    db.advance_meta_run(
      run_id, "running", db_path=path
    )
    run = db.get_meta_agent_run(
      run_id, db_path=path
    )
    assert run["status"] == "running"
    assert run["started_at"] is not None

  def test_advance_to_completed(self, agent_db):
    """Run transitions from running to completed."""
    path, aid = agent_db
    run_id = db.create_meta_agent_run(
      aid, db_path=path
    )
    db.advance_meta_run(
      run_id, "running", db_path=path
    )
    db.advance_meta_run(
      run_id, "completed",
      cost_usd=0.05, num_turns=3,
      db_path=path,
    )
    run = db.get_meta_agent_run(
      run_id, db_path=path
    )
    assert run["status"] == "completed"
    assert run["finished_at"] is not None
    assert run["cost_usd"] == 0.05
    assert run["num_turns"] == 3

  def test_advance_to_failed(self, agent_db):
    """Run transitions from running to failed."""
    path, aid = agent_db
    run_id = db.create_meta_agent_run(
      aid, db_path=path
    )
    db.advance_meta_run(
      run_id, "running", db_path=path
    )
    db.advance_meta_run(
      run_id, "failed", error="boom",
      db_path=path,
    )
    run = db.get_meta_agent_run(
      run_id, db_path=path
    )
    assert run["status"] == "failed"
    assert run["error"] == "boom"

  def test_invalid_transition(self, agent_db):
    """Invalid transitions raise ValueError."""
    path, aid = agent_db
    run_id = db.create_meta_agent_run(
      aid, db_path=path
    )
    with pytest.raises(ValueError, match="Invalid"):
      db.advance_meta_run(
        run_id, "completed", db_path=path
      )

  def test_not_found(self, tmp_db):
    """Advancing nonexistent run raises ValueError."""
    with pytest.raises(ValueError, match="not found"):
      db.advance_meta_run(
        9999, "running", db_path=tmp_db
      )

  def test_list_runs(self, agent_db):
    """list_meta_agent_runs returns runs newest first."""
    path, aid = agent_db
    r1 = db.create_meta_agent_run(
      aid, db_path=path
    )
    r2 = db.create_meta_agent_run(
      aid, db_path=path
    )
    runs = db.list_meta_agent_runs(
      aid, db_path=path
    )
    assert len(runs) == 2
    assert runs[0]["id"] == r2
    assert runs[1]["id"] == r1

  def test_list_runs_limit(self, agent_db):
    """list_meta_agent_runs respects limit."""
    path, aid = agent_db
    for _ in range(5):
      db.create_meta_agent_run(aid, db_path=path)
    runs = db.list_meta_agent_runs(
      aid, limit=3, db_path=path
    )
    assert len(runs) == 3


class TestMetaAgentOutput:
  """Meta agent output recording tests."""

  def test_record_and_get(self, agent_db):
    """record_meta_output stores and get retrieves."""
    path, aid = agent_db
    run_id = db.create_meta_agent_run(
      aid, db_path=path
    )
    lines = [
      {"line_no": 0, "kind": "text",
       "content": "hello", "meta": {}},
      {"line_no": 1, "kind": "tool_use",
       "content": "Read", "meta": {"input": "/foo"}},
    ]
    db.record_meta_output(
      run_id, lines, db_path=path
    )
    result = db.get_meta_output(
      run_id, db_path=path
    )
    assert len(result) == 2
    assert result[0]["content"] == "hello"
    assert result[1]["kind"] == "tool_use"

  def test_from_line_filter(self, agent_db):
    """get_meta_output respects from_line."""
    path, aid = agent_db
    run_id = db.create_meta_agent_run(
      aid, db_path=path
    )
    lines = [
      {"line_no": 0, "kind": "text", "content": "a"},
      {"line_no": 1, "kind": "text", "content": "b"},
      {"line_no": 2, "kind": "text", "content": "c"},
    ]
    db.record_meta_output(
      run_id, lines, db_path=path
    )
    result = db.get_meta_output(
      run_id, from_line=1, db_path=path
    )
    assert len(result) == 2
    assert result[0]["content"] == "b"


class TestSeedDefaults:
  """Default meta agent seeding tests."""

  def test_seeds_on_empty(self, tmp_path):
    """seed_default_meta_agents inserts defaults."""
    path = str(tmp_path / "seed.db")
    db.migrate(db_path=path)
    agents = db.list_meta_agents(db_path=path)
    names = {a["name"] for a in agents}
    assert "write-claude-md" in names
    assert "setup-pipeline" in names
    assert "organize-templates" in names

  def test_idempotent(self, tmp_path):
    """seed_default_meta_agents is idempotent."""
    path = str(tmp_path / "seed.db")
    db.migrate(db_path=path)
    count1 = len(db.list_meta_agents(db_path=path))
    db.seed_default_meta_agents(db_path=path)
    count2 = len(db.list_meta_agents(db_path=path))
    assert count1 == count2

  def test_no_seed_when_agents_exist(self, tmp_path):
    """Seed skips when table is non-empty."""
    path = str(tmp_path / "seed.db")
    db.migrate(db_path=path)
    # Delete defaults and add a custom one.
    with db._connect(path) as conn:
      conn.execute("DELETE FROM meta_agents")
    db.create_meta_agent(
      "custom", "d", "p", db_path=path
    )
    db.seed_default_meta_agents(db_path=path)
    agents = db.list_meta_agents(db_path=path)
    names = {a["name"] for a in agents}
    # Should only have the custom agent.
    assert "custom" in names
    assert "write-claude-md" not in names


class TestSchemaIntegration:
  """Verify meta agent tables are created by migrate."""

  def test_tables_exist(self, tmp_db):
    """migrate() creates meta agent tables."""
    with db._connect(tmp_db) as conn:
      tables = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' ORDER BY name"
      ).fetchall()
    names = {r["name"] for r in tables}
    assert "meta_agents" in names
    assert "meta_agent_runs" in names
    assert "meta_agent_output" in names
