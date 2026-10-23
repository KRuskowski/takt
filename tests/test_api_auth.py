"""Tests for REST API authentication and bind safety.

The API can create workspaces and trigger runs, so the
rules that keep it from being reachable unauthenticated are
worth pinning down.
"""

import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from lib import api, config, service


def run_with_client(coro_fn):
  """Run an async body against a live test client."""

  async def main():
    app = api.build_app(service=None)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
      return await coro_fn(client)
    finally:
      await client.close()

  return asyncio.run(main())


@pytest.fixture
def no_token(monkeypatch):
  """No token configured — the historical local setup."""
  monkeypatch.delenv("TAKT_API_TOKEN", raising=False)
  monkeypatch.setattr(config, "api_token", lambda: None)
  monkeypatch.setattr(
    "lib.config.api_token", lambda: None
  )


@pytest.fixture
def with_token(monkeypatch):
  """A token is configured."""
  monkeypatch.setattr(
    "lib.config.api_token", lambda: "s3cret"
  )
  return "s3cret"


class TestTokenEnforcement:
  """Who gets in when a token is set."""

  def test_no_token_configured_allows_requests(
    self, no_token
  ):
    """A loopback-only install keeps working untouched."""

    async def body(client):
      resp = await client.get("/api/context")
      assert resp.status == 200

    run_with_client(body)

  def test_missing_header_is_rejected(self, with_token):
    """A request with no credentials gets 401."""

    async def body(client):
      resp = await client.get("/api/context")
      assert resp.status == 401
      payload = await resp.json()
      assert payload["message"] == "unauthorized"

    run_with_client(body)

  def test_correct_bearer_token_is_accepted(
    self, with_token
  ):
    """The configured token gets through."""

    async def body(client):
      resp = await client.get(
        "/api/context",
        headers={"Authorization": f"Bearer {with_token}"},
      )
      assert resp.status == 200

    run_with_client(body)

  def test_wrong_token_is_rejected(self, with_token):
    """A near-miss is still a miss."""

    async def body(client):
      resp = await client.get(
        "/api/context",
        headers={"Authorization": "Bearer s3cret-wrong"},
      )
      assert resp.status == 401

    run_with_client(body)

  def test_x_takt_token_header_works(self, with_token):
    """A plain header is accepted for simple clients."""

    async def body(client):
      resp = await client.get(
        "/api/context",
        headers={"X-Takt-Token": with_token},
      )
      assert resp.status == 200

    run_with_client(body)

  def test_bearer_prefix_is_required_not_optional(
    self, with_token
  ):
    """A bare token in Authorization does not authenticate.

    It would have to be parsed as some other scheme, and
    guessing at credentials formats is how auth bugs start.
    """

    async def body(client):
      resp = await client.get(
        "/api/context",
        headers={"Authorization": with_token},
      )
      assert resp.status == 401

    run_with_client(body)

  def test_write_endpoints_are_protected(self, with_token):
    """Auth covers mutations, not just reads.

    Regression guard: an auth check applied only to GETs
    would leave run triggering and workspace creation open.
    """

    async def body(client):
      for method, path in (
        ("post", "/api/runs"),
        ("post", "/api/workspaces"),
        ("post", "/api/context/facts"),
        ("post", "/api/issues/assign"),
        ("put", "/api/context/x.md"),
      ):
        resp = await getattr(client, method)(
          path, json={}
        )
        assert resp.status == 401, f"{method} {path}"

    run_with_client(body)

  def test_preflight_is_exempt(self, with_token):
    """OPTIONS carries no data and no Authorization."""

    async def body(client):
      resp = await client.options("/api/context")
      assert resp.status == 204

    run_with_client(body)


class TestLoopbackDetection:
  """Which addresses count as off-box."""

  @pytest.mark.parametrize("host", [
    "127.0.0.1", "localhost", "::1", "127.0.0.53", "",
  ])
  def test_loopback(self, host):
    """Loopback forms are recognised."""
    assert service._is_loopback(host)

  @pytest.mark.parametrize("host", [
    "0.0.0.0", "10.101.0.10", "192.168.1.5", "::",
    "takt.internal", "scheduler.example.com",
  ])
  def test_not_loopback(self, host):
    """Anything reachable off-box is not loopback.

    A bare hostname counts as exposed: it may resolve
    anywhere, and assuming otherwise is how an API ends up
    on the network by accident.
    """
    assert not service._is_loopback(host)


class TestBindRefusal:
  """The service must not expose an open API."""

  def _start(self, monkeypatch, host, token):
    monkeypatch.setattr(
      "lib.config.API_HOST", host, raising=False
    )
    monkeypatch.setattr(
      "lib.config.api_token", lambda: token
    )
    svc = service.TaktService.__new__(service.TaktService)
    svc._api_port = 0
    return svc

  def test_refuses_public_bind_without_token(
    self, monkeypatch
  ):
    """Binding 0.0.0.0 with no token is refused."""
    svc = self._start(monkeypatch, "0.0.0.0", None)
    with pytest.raises(RuntimeError, match="api_token"):
      asyncio.run(svc._start_http())

  def test_allows_public_bind_with_token(
    self, monkeypatch
  ):
    """A token makes a public bind intentional.

    Binds 0.0.0.0 for real, on an ephemeral port, since the
    point is that the non-loopback path is permitted.
    """
    svc = self._start(monkeypatch, "0.0.0.0", "tok")
    asyncio.run(svc._start_http())
    asyncio.run(svc._http_runner.cleanup())

  def test_allows_loopback_without_token(
    self, monkeypatch
  ):
    """The existing local setup still starts."""
    svc = self._start(monkeypatch, "127.0.0.1", None)
    asyncio.run(svc._start_http())
    asyncio.run(svc._http_runner.cleanup())
