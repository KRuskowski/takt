"""Tests for bin/takt_mcp — MCP server tool registration and basic behavior."""

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(
  0, str(Path(__file__).resolve().parent.parent)
)
from bin.takt_mcp import mcp
from lib import db


async def call(tool_name, args=None):
  """Helper to call an MCP tool and parse the result."""
  result = await mcp.call_tool(
    tool_name, args or {},
  )
  text = result[0][0].text
  try:
    return json.loads(text)
  except json.JSONDecodeError:
    return text


@pytest.fixture
def tmp_db(tmp_path):
  """Temporary database for pipeline/run tests."""
  path = tmp_path / "test.db"
  db.migrate(db_path=str(path))
  return str(path)


class TestToolRegistration:
  """All 28 tools register correctly."""

  def test_tool_count(self):
    tools = asyncio.run(mcp.list_tools())
    assert len(tools) == 28

  def test_all_tools_present(self):
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    expected = {
      "workspace_list", "workspace_create",
      "workspace_delete", "workspace_status",
      "target_list", "target_claim", "target_release",
      "target_up", "target_down", "target_run",
      "target_status",
      "pipeline_show", "pipeline_set", "pipeline_runs",
      "run_get", "run_step_output", "run_trigger",
      "run_cancel",
      "push_to_github",
      "service_status", "service_start", "service_stop",
      "repos_list", "template_list", "template_read",
      "template_write",
      "workspace_claude_md_read",
      "workspace_claude_md_write",
    }
    assert names == expected


class TestWorkspaceTools:
  """Workspace tool smoke tests."""

  def test_workspace_list(self):
    result = asyncio.run(call("workspace_list"))
    assert isinstance(result, list)

  def test_workspace_status_not_found(self):
    result = asyncio.run(
      call("workspace_status",
           {"name": "nonexistent-ws-xyz"})
    )
    assert "error" in result

  def test_workspace_delete_not_found(self):
    result = asyncio.run(
      call("workspace_delete",
           {"name": "nonexistent-ws-xyz"})
    )
    assert "error" in result


class TestTargetTools:
  """Target tool smoke tests."""

  def test_target_status_not_found(self):
    result = asyncio.run(
      call("target_status",
           {"name": "nonexistent-target"})
    )
    assert "error" in result

  def test_target_claim_not_found(self):
    result = asyncio.run(
      call("target_claim",
           {"name": "nonexistent-target",
            "workspace": "test"})
    )
    assert "error" in result

  def test_target_release_not_locked(self):
    result = asyncio.run(
      call("target_release",
           {"name": "nonexistent-target"})
    )
    assert "error" in result


class TestPipelineTools:
  """Pipeline tool tests using a temp DB."""

  def test_pipeline_show_empty(self, tmp_db):
    with patch("lib.db.DB_PATH", tmp_db):
      result = asyncio.run(
        call("pipeline_show", {"workspace": "test-ws"})
      )
      assert isinstance(result, list)

  def test_pipeline_set_and_show(self, tmp_db):
    with patch("lib.db.DB_PATH", tmp_db):
      asyncio.run(
        call("pipeline_set", {
          "workspace": "test-ws",
          "steps": ["test", "deploy"],
        })
      )
      result = asyncio.run(
        call("pipeline_show",
             {"workspace": "test-ws"})
      )
      assert isinstance(result, list)


class TestRunTools:
  """Run tool tests."""

  def test_run_get_not_found(self):
    result = asyncio.run(
      call("run_get", {"run_id": 99999})
    )
    assert "error" in result


class TestConfigTools:
  """Config/template tool tests."""

  def test_template_list(self):
    result = asyncio.run(call("template_list"))
    assert isinstance(result, list)

  def test_template_read_not_found(self):
    result = asyncio.run(
      call("template_read",
           {"name": "nonexistent-template"})
    )
    assert "error" in result

  def test_template_write_and_read(self, tmp_path):
    tpl_dir = tmp_path / "templates"
    tpl_dir.mkdir()
    with patch("bin.takt_mcp.TEMPLATES_DIR", tpl_dir):
      asyncio.run(
        call("template_write", {
          "name": "test-tpl",
          "content": "# Test template",
        })
      )
      result = asyncio.run(
        call("template_read",
             {"name": "test-tpl"})
      )
      assert result == "# Test template"

  def test_workspace_claude_md_read_not_found(self):
    result = asyncio.run(
      call("workspace_claude_md_read",
           {"workspace": "nonexistent-ws"})
    )
    assert "error" in result

  def test_workspace_claude_md_write_no_ws(self):
    result = asyncio.run(
      call("workspace_claude_md_write", {
        "workspace": "nonexistent-ws",
        "content": "test",
      })
    )
    assert "error" in result


class TestServiceTools:
  """Service tool smoke tests."""

  def test_service_status(self):
    result = asyncio.run(call("service_status"))
    assert "state" in result
