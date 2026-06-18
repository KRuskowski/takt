"""Tests for lib.api — REST API endpoints."""

import asyncio
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(
  0, str(Path(__file__).resolve().parent.parent)
)
from lib.api import create_app


def _run(coro):
  """Run an async function in a fresh event loop."""
  loop = asyncio.new_event_loop()
  try:
    return loop.run_until_complete(coro)
  finally:
    loop.close()


async def _make_client():
  """Create a test client."""
  app = create_app()
  server = TestServer(app)
  client = TestClient(server)
  await client.start_server()
  return client


async def _get(path):
  """GET helper returning (status, json_or_text)."""
  client = await _make_client()
  try:
    resp = await client.get(path)
    ct = resp.content_type
    if "json" in ct:
      data = await resp.json()
    else:
      data = await resp.text()
    return resp.status, data
  finally:
    await client.close()


async def _delete(path):
  """DELETE helper."""
  client = await _make_client()
  try:
    resp = await client.delete(path)
    data = await resp.json()
    return resp.status, data
  finally:
    await client.close()


def test_list_workspaces():
  status, data = _run(_get("/api/workspaces"))
  assert status == 200
  assert isinstance(data, list)


def test_workspace_status_not_found():
  status, data = _run(
    _get("/api/workspaces/nonexistent-xyz/status")
  )
  assert status == 404


def test_workspace_delete_not_found():
  status, data = _run(
    _delete("/api/workspaces/nonexistent-xyz")
  )
  assert status == 404


def test_target_status_not_found():
  status, data = _run(
    _get("/api/targets/nonexistent-tgt")
  )
  assert status in (404, 500)


def test_list_runs():
  status, data = _run(_get("/api/runs"))
  assert status == 200
  assert isinstance(data, list)


def test_run_not_found():
  status, data = _run(_get("/api/runs/99999"))
  assert status == 404


def test_templates_list():
  status, data = _run(_get("/api/templates"))
  assert status == 200
  assert isinstance(data, list)


def test_template_not_found():
  status, data = _run(
    _get("/api/templates/nonexistent-tpl")
  )
  assert status == 404


def test_pipeline_empty():
  status, data = _run(
    _get("/api/pipeline/nonexistent-ws")
  )
  assert status == 200
  assert isinstance(data, list)


def test_agents_list():
  status, data = _run(_get("/api/agents"))
  assert status == 200
  assert isinstance(data, list)
