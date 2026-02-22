"""Tests for lib.pipeline — pipeline executor."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from lib import db, pipeline
from lib.git_utils import GitError


@pytest.fixture
def tmp_db(tmp_path):
  """Create a temporary database."""
  path = str(tmp_path / "test.db")
  db.migrate(db_path=path)
  return path


@pytest.fixture
def seeded_run(tmp_db):
  """DB with a pipeline and one queued run."""
  db.define_pipeline("ws1", [
    {"name": "test", "step_type": "agent"},
    {"name": "push_to_github", "step_type": "script"},
  ], db_path=tmp_db)
  run_id = db.create_run(
    "ws1", "manual", ["repo-a"],
    {"repo-a": "abc123"}, db_path=tmp_db,
  )
  return tmp_db, run_id


class TestRefSnapshots:
  """Ref snapshot utility tests."""

  def test_find_changes_new(self):
    """Detects new branches."""
    old = {}
    new = {"repo:feat": "aaa"}
    changes = pipeline.find_changes(old, new)
    assert len(changes) == 1
    assert changes[0]["type"] == "new"

  def test_find_changes_updated(self):
    """Detects updated branches."""
    old = {"repo:main": "aaa"}
    new = {"repo:main": "bbb"}
    changes = pipeline.find_changes(old, new)
    assert len(changes) == 1
    assert changes[0]["type"] == "updated"

  def test_find_changes_deleted(self):
    """Detects deleted branches."""
    old = {"repo:feat": "aaa"}
    new = {}
    changes = pipeline.find_changes(old, new)
    assert len(changes) == 1
    assert changes[0]["type"] == "deleted"

  def test_find_changes_none(self):
    """No changes when refs match."""
    refs = {"repo:main": "aaa"}
    changes = pipeline.find_changes(refs, refs)
    assert changes == []

  def test_group_by_branch(self):
    """Groups changes by branch name."""
    changes = [
      {"repo": "a", "branch": "feat", "type": "updated"},
      {"repo": "b", "branch": "feat", "type": "updated"},
      {"repo": "c", "branch": "main", "type": "updated"},
    ]
    groups = pipeline.group_by_branch(changes)
    assert len(groups["feat"]) == 2
    assert len(groups["main"]) == 1


class TestScriptSteps:
  """Built-in script step tests."""

  def test_push_to_github(self):
    """push_to_github calls push_branch for each repo."""
    run = {
      "workspace": "feat",
      "repos_json": '["repo-a"]',
    }
    with patch(
      "lib.pipeline.push_branch"
    ) as mock_push, patch(
      "lib.pipeline.load_repos_config",
      return_value={
        "repos": {"repo-a": {"path": "repo-a"}},
      },
    ):
      result = pipeline.script_push_to_github(run, {})
    mock_push.assert_called_once()
    assert result["status"] == "pass"
    assert "repo-a" in result["pushed"]

  def test_push_to_github_failure(self):
    """push_to_github reports errors on GitError."""
    run = {
      "workspace": "feat",
      "repos_json": '["repo-a"]',
    }
    with patch(
      "lib.pipeline.push_branch",
      side_effect=GitError("push", 1, "rejected"),
    ), patch(
      "lib.pipeline.load_repos_config",
      return_value={
        "repos": {"repo-a": {"path": "repo-a"}},
      },
    ):
      result = pipeline.script_push_to_github(run, {})
    assert result["status"] == "fail"
    assert len(result["errors"]) == 1

  def test_merge_upstream(self, tmp_path):
    """merge_upstream calls git fetch + merge."""
    run = {
      "workspace": "feat",
      "repos_json": '["repo-a"]',
      "worktree_dir": str(tmp_path),
    }
    with patch(
      "lib.pipeline.load_repos_config",
      return_value={
        "repos": {
          "repo-a": {
            "path": "repo-a",
            "default_branch": "main",
          },
        },
      },
    ), patch("subprocess.run") as mock_run:
      mock_run.return_value = MagicMock(returncode=0)
      result = pipeline.script_merge_upstream(run, {})
    assert result["status"] == "pass"
    assert "repo-a" in result["merged"]


class TestPipelineExecutor:
  """PipelineExecutor tests."""

  def test_execute_run_all_scripts(self, tmp_db):
    """Runs all script steps and passes."""
    db.define_pipeline("ws1", [
      {"name": "push_to_github", "step_type": "script"},
    ], db_path=tmp_db)
    run_id = db.create_run(
      "ws1", "manual", ["repo-a"],
      {"repo-a": "abc"}, db_path=tmp_db,
    )
    executor = pipeline.PipelineExecutor(db_path=tmp_db)
    mock_push = MagicMock(
      return_value={
        "status": "pass", "pushed": ["repo-a"],
      },
    )

    async def run():
      with patch.object(
        executor, "setup_worktrees",
        return_value=None,
      ), patch.object(
        executor, "teardown_worktrees",
      ), patch.dict(
        pipeline.SCRIPT_REGISTRY,
        {"push_to_github": mock_push},
      ):
        return await executor.execute_run(run_id)

    status = asyncio.run(run())
    assert status == "passed"
    run_row = db.get_run(run_id, db_path=tmp_db)
    assert run_row["status"] == "passed"
    assert run_row["finished_at"] is not None

  def test_execute_run_step_fails(self, tmp_db):
    """Run fails when a step fails."""
    db.define_pipeline("ws1", [
      {"name": "push_to_github", "step_type": "script"},
      {"name": "create_pr", "step_type": "script"},
    ], db_path=tmp_db)
    run_id = db.create_run(
      "ws1", "manual", ["repo-a"],
      {"repo-a": "abc"}, db_path=tmp_db,
    )
    executor = pipeline.PipelineExecutor(db_path=tmp_db)
    mock_push = MagicMock(return_value={
      "status": "fail",
      "errors": [{"repo": "a", "error": "rejected"}],
    })

    async def run():
      with patch.object(
        executor, "setup_worktrees",
        return_value=None,
      ), patch.object(
        executor, "teardown_worktrees",
      ), patch.dict(
        pipeline.SCRIPT_REGISTRY,
        {"push_to_github": mock_push},
      ):
        return await executor.execute_run(run_id)

    status = asyncio.run(run())
    assert status == "failed"
    steps = db.get_run_steps(run_id, db_path=tmp_db)
    assert steps[0]["status"] == "failed"
    # Second step should still be pending.
    assert steps[1]["status"] == "pending"

  def test_execute_run_not_found(self, tmp_db):
    """Returns failed for nonexistent run."""
    executor = pipeline.PipelineExecutor(db_path=tmp_db)
    status = asyncio.run(executor.execute_run(9999))
    assert status == "failed"

  def test_step_update_callback(self, tmp_db):
    """on_step_update is called for each transition."""
    db.define_pipeline("ws1", [
      {"name": "push_to_github", "step_type": "script"},
    ], db_path=tmp_db)
    run_id = db.create_run(
      "ws1", "manual", [], {}, db_path=tmp_db,
    )
    updates = []
    executor = pipeline.PipelineExecutor(
      on_step_update=lambda sid, s: updates.append(
        (sid, s)
      ),
      db_path=tmp_db,
    )
    mock_push = MagicMock(
      return_value={"status": "pass"},
    )

    async def run():
      with patch.object(
        executor, "setup_worktrees",
        return_value=None,
      ), patch.object(
        executor, "teardown_worktrees",
      ), patch.dict(
        pipeline.SCRIPT_REGISTRY,
        {"push_to_github": mock_push},
      ):
        return await executor.execute_run(run_id)

    asyncio.run(run())
    statuses = [s for _, s in updates]
    assert "queued" in statuses
    assert "running" in statuses
    assert "completed" in statuses

  def test_worktree_setup_failure(self, tmp_db):
    """Run fails if worktree setup fails."""
    db.define_pipeline("ws1", [
      {"name": "test", "step_type": "agent"},
    ], db_path=tmp_db)
    run_id = db.create_run(
      "ws1", "manual", ["repo-a"],
      {"repo-a": "abc"}, db_path=tmp_db,
    )
    executor = pipeline.PipelineExecutor(db_path=tmp_db)

    async def run():
      with patch.object(
        executor, "setup_worktrees",
        side_effect=RuntimeError("worktree failed"),
      ):
        return await executor.execute_run(run_id)

    status = asyncio.run(run())
    assert status == "failed"
    run_row = db.get_run(run_id, db_path=tmp_db)
    assert run_row["status"] == "failed"


class TestBuildAgentPrompt:
  """Agent prompt building tests."""

  def test_includes_role_snippet(self):
    """Prompt includes the role template."""
    executor = pipeline.PipelineExecutor()
    with patch(
      "lib.pipeline.parse_pipeline_roles",
      return_value={"test": "Run the test suite."},
    ), patch(
      "lib.pipeline.get_log", return_value="",
    ):
      prompt = executor._build_agent_prompt(
        "test", "feat", ["repo-a"], {},
        {"head_refs_json": '{}'},
      )
    assert "Run the test suite" in prompt
    assert "feat" in prompt

  def test_includes_custom_prompt(self):
    """Prompt includes config.prompt override."""
    executor = pipeline.PipelineExecutor()
    with patch(
      "lib.pipeline.parse_pipeline_roles",
      return_value={},
    ):
      prompt = executor._build_agent_prompt(
        "test", "feat", ["repo-a"],
        {"prompt": "Focus on unit tests only."},
        {"head_refs_json": '{}'},
      )
    assert "Focus on unit tests only" in prompt


class TestScriptRegistry:
  """Script registry tests."""

  def test_all_registered(self):
    """All built-in scripts are in the registry."""
    assert "push_to_github" in pipeline.SCRIPT_REGISTRY
    assert "create_pr" in pipeline.SCRIPT_REGISTRY
    assert "merge_upstream" in pipeline.SCRIPT_REGISTRY
