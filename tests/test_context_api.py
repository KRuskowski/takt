"""Tests for the context engine REST endpoints.

Driven through a real aiohttp test server so the routing is
exercised, not just the handler bodies. The project has no
pytest-aiohttp plugin, so each test runs its own loop via
asyncio.run().
"""

import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from lib import api
from lib import context_store as cs
from lib import db


@pytest.fixture
def live_db(tmp_path, monkeypatch):
  """Point the whole db layer at a temporary database.

  The context handlers call context_store without an
  explicit db_path, so the override has to happen at
  lib.db.DB_PATH, which _connect() falls back to.
  """
  path = tmp_path / "api.db"
  monkeypatch.setattr(db, "DB_PATH", path)
  db.migrate(db_path=str(path))
  return path


@pytest.fixture
def corpus_dirs(tmp_path, monkeypatch):
  """Create a small corpus and point reindex() at it."""
  ctx = tmp_path / "context"
  tpl = tmp_path / "templates"
  ctx.mkdir()
  tpl.mkdir()
  (ctx / "vms.md").write_text(
    "# Virtual Machines\n\n## Clones\n\n"
    "Clones use qcow2 backing files.\n"
  )
  (tpl / "roles.md").write_text(
    "# Roles\n\n## feature\n\nImplement the feature.\n"
  )
  monkeypatch.setattr(
    cs, "INDEXED_DIRS",
    (("context", ctx), ("templates", tpl)),
  )
  monkeypatch.setattr(api_config(), "CONTEXT_DIR", ctx)
  monkeypatch.setattr(api_config(), "TEMPLATES_DIR", tpl)
  return ctx, tpl


def api_config():
  """Return the config module the handlers import."""
  from lib import config
  return config


def run(coro_fn):
  """Run an async test body against a live test client."""

  async def main():
    app = api.build_app(service=None)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
      return await coro_fn(client)
    finally:
      await client.close()

  return asyncio.run(main())


class TestRoutePrefixSharing:
  """The engine shares /api/context with the file reader.

  /api/context/{name} reads a file by name, and the engine
  adds /api/context/search and friends under the same
  prefix. aiohttp matches static paths ahead of dynamic
  ones, so they coexist — these tests pin that both halves
  still resolve to the right handler, since a change to
  either set of routes could break the other.
  """

  def test_engine_routes_reach_the_engine(
    self, live_db, corpus_dirs
  ):
    """Engine routes are not read as filenames."""

    async def body(client):
      for path in (
        "/api/context/search?q=qcow2",
        "/api/context/stats",
        "/api/context/docs",
      ):
        resp = await client.get(path)
        assert resp.status == 200, path
        payload = await resp.json()
        assert payload["status"] == "ok", path
        # The file reader answers with a 'content' key.
        # Reaching it instead would mean the prefix broke.
        assert "content" not in payload["data"], path

    run(body)

  def test_wildcard_still_reads_files(
    self, live_db, corpus_dirs
  ):
    """Reading a context file by name still works."""

    async def body(client):
      resp = await client.get("/api/context/vms.md")
      assert resp.status == 200
      payload = await resp.json()
      assert "qcow2" in payload["data"]["content"]

    run(body)


class TestSearchEndpoint:
  """GET /api/context/search."""

  def test_returns_ranked_sections(
    self, live_db, corpus_dirs
  ):
    """A hit names the file, heading and line range."""

    async def body(client):
      await client.post("/api/context/reindex")
      resp = await client.get(
        "/api/context/search?q=qcow2"
      )
      results = (await resp.json())["data"]["results"]
      assert results
      top = results[0]
      assert top["path"] == "context/vms.md"
      assert top["heading"] == "Clones"
      assert top["start_line"] >= 1
      assert "qcow2" in top["snippet"]

    run(body)

  def test_missing_query_is_rejected(self, live_db):
    """An empty q is a 400, not an empty result set."""

    async def body(client):
      for path in (
        "/api/context/search",
        "/api/context/search?q=",
        "/api/context/search?q=%20%20",
      ):
        resp = await client.get(path)
        assert resp.status == 400, path

    run(body)

  def test_invalid_scope_is_rejected(self, live_db):
    """A bad scope is a 400 with a message."""

    async def body(client):
      resp = await client.get(
        "/api/context/search?q=x&scope_kind=planet"
      )
      assert resp.status == 400
      assert "scope" in (await resp.json())["message"]

    run(body)

  def test_scope_without_value_is_rejected(self, live_db):
    """A repo scope needs a repo name."""

    async def body(client):
      resp = await client.get(
        "/api/context/search?q=x&scope_kind=repo"
      )
      assert resp.status == 400

    run(body)

  def test_hostile_query_does_not_500(self, live_db):
    """FTS5 metacharacters cannot crash the endpoint."""

    async def body(client):
      for q in ("NEAR((%22", "AND", "*", "a-b-c", "col%3Av"):
        resp = await client.get(
          f"/api/context/search?q={q}"
        )
        assert resp.status == 200, q

    run(body)

  def test_limit_is_clamped(self, live_db, corpus_dirs):
    """An absurd limit does not become an absurd query."""

    async def body(client):
      await client.post("/api/context/reindex")
      resp = await client.get(
        "/api/context/search?q=the&limit=100000"
      )
      assert resp.status == 200
      results = (await resp.json())["data"]["results"]
      assert len(results) <= 100

    run(body)


class TestFactEndpoints:
  """POST /api/context/facts and friends."""

  def test_records_and_retrieves_a_fact(self, live_db):
    """A recorded fact is immediately searchable."""

    async def body(client):
      resp = await client.post(
        "/api/context/facts",
        json={
          "body": "# zyzzyva quirk\n\nIt needs a flag.",
          "scope_kind": "repo",
          "scope_value": "OTC.SDK",
          "source_run": 99,
          "source_workspace": "ws-a",
        },
      )
      assert resp.status == 200
      created = (await resp.json())["data"]
      assert created["path"].startswith("facts/otc-sdk/")

      resp = await client.get(
        "/api/context/search?q=zyzzyva"
      )
      hits = (await resp.json())["data"]["results"]
      assert len(hits) == 1
      assert hits[0]["source_run"] == 99
      assert hits[0]["source_workspace"] == "ws-a"

    run(body)

  def test_empty_body_is_rejected(self, live_db):
    """An empty fact is a 400."""

    async def body(client):
      resp = await client.post(
        "/api/context/facts", json={"body": "   "}
      )
      assert resp.status == 400

    run(body)

  def test_malformed_json_is_rejected(self, live_db):
    """A non-JSON payload does not 500."""

    async def body(client):
      resp = await client.post(
        "/api/context/facts", data="not json",
        headers={"Content-Type": "application/json"},
      )
      assert resp.status == 400

    run(body)

  def test_scope_isolation_over_http(self, live_db):
    """Facts do not leak between repo scopes."""

    async def body(client):
      await client.post("/api/context/facts", json={
        "body": "# a\n\nshared term here.",
        "scope_kind": "repo", "scope_value": "OTC.SDK",
      })
      await client.post("/api/context/facts", json={
        "body": "# b\n\nshared term here.",
        "scope_kind": "repo", "scope_value": "Conveyor",
      })
      resp = await client.get(
        "/api/context/search?q=shared+term"
        "&scope_kind=repo&scope_value=OTC.SDK"
      )
      hits = (await resp.json())["data"]["results"]
      assert len(hits) == 1
      assert hits[0]["scope_value"] == "OTC.SDK"

    run(body)

  def test_supersede_hides_old_fact(self, live_db):
    """A retired fact leaves the default result set."""

    async def body(client):
      await client.post("/api/context/facts", json={
        "body": "# old\n\nglargle used to work.",
        "path": "facts/old.md",
      })
      await client.post("/api/context/facts", json={
        "body": "# new\n\nglargle works like this.",
        "path": "facts/new.md",
      })
      resp = await client.post(
        "/api/context/supersede",
        json={"path": "facts/old.md",
              "by_path": "facts/new.md"},
      )
      assert resp.status == 200
      resp = await client.get(
        "/api/context/search?q=glargle"
      )
      hits = (await resp.json())["data"]["results"]
      assert [h["path"] for h in hits] == ["facts/new.md"]

    run(body)

  def test_supersede_unknown_path_is_404(self, live_db):
    """Retiring a doc that does not exist is a 404."""

    async def body(client):
      resp = await client.post(
        "/api/context/supersede",
        json={"path": "facts/nope.md",
              "by_path": "facts/gone.md"},
      )
      assert resp.status == 404

    run(body)

  def test_supersede_requires_both_paths(self, live_db):
    """Missing fields are a 400."""

    async def body(client):
      resp = await client.post(
        "/api/context/supersede",
        json={"path": "facts/a.md"},
      )
      assert resp.status == 400

    run(body)


class TestIndexFreshness:
  """Edits through the API must not leave stale results."""

  def test_put_context_reindexes(
    self, live_db, corpus_dirs
  ):
    """An edited context file is searchable at once."""

    async def body(client):
      await client.post("/api/context/reindex")
      resp = await client.put(
        "/api/context/vms.md",
        json={"content": "# VMs\n\nNow mentions "
                         "flibbertigibbet.\n"},
      )
      assert resp.status == 200
      assert (await resp.json())["data"]["indexed"]
      resp = await client.get(
        "/api/context/search?q=flibbertigibbet"
      )
      hits = (await resp.json())["data"]["results"]
      assert len(hits) == 1
      # And the replaced text is gone.
      resp = await client.get(
        "/api/context/search?q=qcow2"
      )
      assert (await resp.json())["data"]["results"] == []

    run(body)

  def test_put_template_reindexes(
    self, live_db, corpus_dirs
  ):
    """An edited template is searchable at once."""

    async def body(client):
      await client.post("/api/context/reindex")
      resp = await client.put(
        "/api/templates/roles.md",
        json={"content": "# Roles\n\nmention "
                         "snorkelwhacker.\n"},
      )
      assert resp.status == 200
      resp = await client.get(
        "/api/context/search?q=snorkelwhacker"
      )
      assert (await resp.json())["data"]["results"]

    run(body)

  def test_reindex_reports_counts(
    self, live_db, corpus_dirs
  ):
    """POST /reindex returns what it did."""

    async def body(client):
      resp = await client.post("/api/context/reindex")
      data = (await resp.json())["data"]
      assert data["created"] == 2
      assert data["chunks"] > 0
      resp = await client.post("/api/context/reindex")
      assert (await resp.json())["data"]["unchanged"] == 2

    run(body)


class TestStatsEndpoint:
  """GET /api/context/stats."""

  def test_reports_index_size(self, live_db, corpus_dirs):
    """Stats separate docs from facts."""

    async def body(client):
      await client.post("/api/context/reindex")
      await client.post("/api/context/facts", json={
        "body": "# f\n\na fact.",
      })
      resp = await client.get("/api/context/stats")
      assert resp.status == 200
      data = (await resp.json())["data"]
      assert data["docs"] == 2
      assert data["facts"] == 1
      assert data["chunks"] > 0
      assert data["est_tokens"] > 0

    run(body)
