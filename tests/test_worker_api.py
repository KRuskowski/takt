"""Tests for the worker protocol over HTTP.

Driven through a real aiohttp server against a real
database, including a real git bundle, because the parts
most worth testing are the refusals: a worker must not be
able to report over a lease it no longer holds, or deliver
commits into a repo it was not given.
"""

import asyncio
import json
import subprocess

import pytest
from aiohttp.test_utils import TestClient, TestServer

from lib import api, db, worker_ops


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
  """Point the db layer at a temporary database."""
  path = tmp_path / "api.db"
  monkeypatch.setattr(db, "DB_PATH", path)
  db.migrate(db_path=str(path))
  return str(path)


def make_run(tmp_db, workspace="ws1"):
  """Create a queued run with two steps."""
  db.define_pipeline(workspace, [
    {"name": "test", "step_type": "agent"},
    {"name": "push_to_github", "step_type": "script"},
  ], db_path=tmp_db)
  return db.create_run(
    workspace, "manual", ["repo-a"], {"repo-a:main": "abc"},
    db_path=tmp_db,
  )


def run_client(coro_fn):
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


async def register(client, worker_id="w1", **kw):
  """Register a worker and return the response body."""
  resp = await client.post(
    "/api/workers/register",
    json={"worker_id": worker_id, **kw},
  )
  assert resp.status == 200
  return (await resp.json())["data"]


async def take_lease(client, worker_id="w1"):
  """Lease a run, returning the work payload."""
  resp = await client.post(
    f"/api/workers/{worker_id}/lease", json={}
  )
  assert resp.status == 200
  return (await resp.json())["data"]["work"]


class TestRegistration:
  """Announcing a worker."""

  def test_registers_and_lists(self, tmp_db):
    """A registered worker shows up in the listing."""

    async def body(client):
      await register(
        client, "build-01", host="10.0.0.9", os="debian",
        cores=16, ram_mb=64000, free_disk_mb=800000,
        capabilities={"cmake": True, "claude": True},
      )
      resp = await client.get("/api/workers")
      workers = (await resp.json())["data"]["workers"]
      assert len(workers) == 1
      assert workers[0]["id"] == "build-01"
      assert workers[0]["capabilities"]["claude"] is True

    run_client(body)

  def test_worker_id_is_required(self, tmp_db):
    """Registering without an id is a 400."""

    async def body(client):
      resp = await client.post(
        "/api/workers/register", json={}
      )
      assert resp.status == 400

    run_client(body)

  def test_heartbeat_from_unknown_worker_is_404(
    self, tmp_db
  ):
    """An unregistered worker is told to register.

    Silently accepting would leave a machine believing it
    was enrolled while the scheduler never considers it.
    """

    async def body(client):
      resp = await client.post(
        "/api/workers/ghost/heartbeat", json={}
      )
      assert resp.status == 404

    run_client(body)

  def test_heartbeat_updates_free_disk(self, tmp_db):
    """Reported free space is stored."""

    async def body(client):
      await register(client, "w1", free_disk_mb=900000)
      resp = await client.post(
        "/api/workers/w1/heartbeat",
        json={"free_disk_mb": 1234},
      )
      assert resp.status == 200
      resp = await client.get("/api/workers")
      workers = (await resp.json())["data"]["workers"]
      assert workers[0]["free_disk_mb"] == 1234

    run_client(body)

  def test_draining_is_settable(self, tmp_db):
    """A worker can be drained for retirement."""

    async def body(client):
      await register(client, "w1")
      resp = await client.post(
        "/api/workers/w1/state", json={"state": "draining"}
      )
      assert resp.status == 200
      resp = await client.get("/api/workers")
      workers = (await resp.json())["data"]["workers"]
      assert workers[0]["state"] == "draining"

    run_client(body)

  def test_bad_state_is_rejected(self, tmp_db):
    """An unknown state is a 400, not stored."""

    async def body(client):
      await register(client, "w1")
      resp = await client.post(
        "/api/workers/w1/state", json={"state": "sleepy"}
      )
      assert resp.status == 400

    run_client(body)


class TestLeasing:
  """Handing out work."""

  def test_empty_queue_is_not_an_error(self, tmp_db):
    """Idle returns work=null with a 200."""

    async def body(client):
      await register(client, "w1")
      work = await take_lease(client)
      assert work is None

    run_client(body)

  def test_lease_describes_the_work(self, tmp_db):
    """The payload has refs, repos and ordered steps."""
    run_id = make_run(tmp_db)

    async def body(client):
      await register(client, "w1")
      work = await take_lease(client)
      assert work["run_id"] == run_id
      assert work["workspace"] == "ws1"
      assert work["generation"] == 1
      assert work["repos"] == ["repo-a"]
      assert work["refs"] == {"repo-a:main": "abc"}
      assert [s["name"] for s in work["steps"]] == [
        "test", "push_to_github",
      ]
      assert work["steps"][0]["step_id"]

    run_client(body)

  def test_second_worker_gets_nothing(self, tmp_db):
    """A claimed run is not handed out twice."""
    make_run(tmp_db)

    async def body(client):
      await register(client, "w1")
      await register(client, "w2")
      assert await take_lease(client, "w1") is not None
      assert await take_lease(client, "w2") is None

    run_client(body)


class TestStepReporting:
  """Progress from a worker."""

  def test_output_and_status_are_recorded(self, tmp_db):
    """A worker writes through the normal tables.

    Remote runs must be readable by every existing tool,
    so output lands in agent_output and status goes through
    advance_step rather than a parallel mechanism.
    """
    make_run(tmp_db)

    async def body(client):
      await register(client, "w1")
      work = await take_lease(client)
      step = work["steps"][0]
      resp = await client.post(
        "/api/workers/w1/step",
        json={
          "run_id": work["run_id"],
          "generation": work["generation"],
          "step_id": step["step_id"],
          "status": "queued",
          "output": [{
            "line_no": 1, "kind": "stdout",
            "content": "compiling", "meta": {},
          }],
        },
      )
      assert resp.status == 200, await resp.text()
      data = (await resp.json())["data"]
      assert data["output_lines"] == 1
      lines = db.get_output(step["step_id"], db_path=tmp_db)
      assert lines[0]["content"] == "compiling"
      assert db.get_step(
        step["step_id"], db_path=tmp_db
      )["status"] == "queued"

    run_client(body)

  def test_stale_generation_is_refused(self, tmp_db):
    """A worker that lost its lease cannot report.

    This is the case that matters: a machine goes dark, the
    lease is swept, another worker takes the run and
    finishes it, then the first machine wakes up. Its
    report must be refused rather than overwriting the
    work that actually happened.
    """
    run_id = make_run(tmp_db)

    async def body(client):
      await register(client, "w1")
      work = await take_lease(client)
      step_id = work["steps"][0]["step_id"]
      # Lease expires and someone else takes it.
      db.release_run(run_id, db_path=tmp_db)
      db.lease_run("w2", db_path=tmp_db)
      resp = await client.post(
        "/api/workers/w1/step",
        json={
          "run_id": run_id,
          "generation": work["generation"],
          "step_id": step_id,
          "status": "queued",
          "output": [{
            "line_no": 1, "kind": "stdout",
            "content": "from the zombie", "meta": {},
          }],
        },
      )
      assert resp.status == 409
      # The refusal has to be total: no output written and
      # no status change, not merely a complaint.
      assert db.get_output(
        step_id, db_path=tmp_db
      ) == []
      assert db.get_step(
        step_id, db_path=tmp_db
      )["status"] == "pending"

    run_client(body)

  def test_bumped_generation_is_refused(self, tmp_db):
    """Same worker, reclaimed run, is still refused.

    Covers the other branch: ownership matches because the
    run came back to this worker, but the generation moved,
    so the old attempt's reports must not be accepted.
    """
    run_id = make_run(tmp_db)

    async def body(client):
      await register(client, "w1")
      work = await take_lease(client)
      db.release_run(run_id, db_path=tmp_db)
      db.lease_run("w1", db_path=tmp_db)
      resp = await client.post(
        "/api/workers/w1/step",
        json={
          "run_id": run_id,
          "generation": work["generation"],
          "step_id": work["steps"][0]["step_id"],
          "status": "queued",
        },
      )
      assert resp.status == 409
      assert "stale lease" in (await resp.json())["message"]

    run_client(body)

  def test_other_workers_run_is_refused(self, tmp_db):
    """A worker cannot report on a run it never held."""
    run_id = make_run(tmp_db)

    async def body(client):
      await register(client, "w1")
      await register(client, "w2")
      work = await take_lease(client, "w1")
      resp = await client.post(
        "/api/workers/w2/step",
        json={
          "run_id": run_id,
          "generation": work["generation"],
          "step_id": work["steps"][0]["step_id"],
          "status": "queued",
        },
      )
      assert resp.status == 409

    run_client(body)

  def test_step_must_belong_to_the_run(self, tmp_db):
    """A step id from another run is rejected."""
    make_run(tmp_db, "ws1")
    other_run = make_run(tmp_db, "ws2")
    other_step = db.get_run_steps(
      other_run, db_path=tmp_db
    )[0]["id"]

    async def body(client):
      await register(client, "w1")
      work = await take_lease(client)
      resp = await client.post(
        "/api/workers/w1/step",
        json={
          "run_id": work["run_id"],
          "generation": work["generation"],
          "step_id": other_step,
          "status": "queued",
        },
      )
      assert resp.status == 400

    run_client(body)

  def test_missing_fields_are_rejected(self, tmp_db):
    """Partial reports are a 400."""
    make_run(tmp_db)

    async def body(client):
      await register(client, "w1")
      work = await take_lease(client)
      resp = await client.post(
        "/api/workers/w1/step",
        json={"run_id": work["run_id"]},
      )
      assert resp.status == 400

    run_client(body)


class TestRenewal:
  """Keeping a lease alive over HTTP."""

  def test_holder_renews(self, tmp_db):
    """The rightful holder gets a 200."""
    make_run(tmp_db)

    async def body(client):
      await register(client, "w1")
      work = await take_lease(client)
      resp = await client.post(
        "/api/workers/w1/renew",
        json={
          "run_id": work["run_id"],
          "generation": work["generation"],
        },
      )
      assert resp.status == 200

    run_client(body)

  def test_stale_renewal_is_409(self, tmp_db):
    """A lost lease cannot be renewed, and says so."""
    run_id = make_run(tmp_db)

    async def body(client):
      await register(client, "w1")
      work = await take_lease(client)
      db.release_run(run_id, db_path=tmp_db)
      db.lease_run("w2", db_path=tmp_db)
      resp = await client.post(
        "/api/workers/w1/renew",
        json={
          "run_id": run_id,
          "generation": work["generation"],
        },
      )
      assert resp.status == 409

    run_client(body)


class TestFinishAndAbandon:
  """Ending a run."""

  def test_abandon_requeues_the_run(self, tmp_db):
    """A given-back run is available to another worker."""
    make_run(tmp_db)

    async def body(client):
      await register(client, "w1")
      await register(client, "w2")
      work = await take_lease(client, "w1")
      resp = await client.post(
        "/api/workers/w1/abandon",
        json={
          "run_id": work["run_id"],
          "generation": work["generation"],
          "reason": "out of disk",
        },
      )
      assert resp.status == 200
      again = await take_lease(client, "w2")
      assert again["run_id"] == work["run_id"]
      assert again["generation"] == 2

    run_client(body)

  def test_finish_releases_the_claim(self, tmp_db):
    """Finishing advances the run and drops the lease."""
    run_id = make_run(tmp_db)

    async def body(client):
      await register(client, "w1")
      work = await take_lease(client)
      resp = await client.post(
        "/api/workers/w1/finish",
        json={
          "run_id": run_id,
          "generation": work["generation"],
        },
      )
      assert resp.status == 200
      run = db.get_run(run_id, db_path=tmp_db)
      assert run["worker_id"] is None

    run_client(body)

  def test_finish_with_stale_lease_is_409(self, tmp_db):
    """A stale worker cannot complete someone's run."""
    run_id = make_run(tmp_db)

    async def body(client):
      await register(client, "w1")
      work = await take_lease(client)
      db.release_run(run_id, db_path=tmp_db)
      db.lease_run("w2", db_path=tmp_db)
      resp = await client.post(
        "/api/workers/w1/finish",
        json={
          "run_id": run_id,
          "generation": work["generation"],
        },
      )
      assert resp.status == 409

    run_client(body)


class TestBundleDelivery:
  """Commits coming back from a worker."""

  @pytest.fixture
  def root_repo(self, tmp_path, monkeypatch):
    """A root repo plus a clone that adds a commit."""
    root = tmp_path / "root" / "repo-a"
    root.mkdir(parents=True)
    git = ["git", "-C", str(root)]
    subprocess.run(
      ["git", "init", "-q", "-b", "main", str(root)],
      check=True, timeout=60,
    )
    subprocess.run(
      git + ["config", "user.email", "t@t"], check=True,
      timeout=60,
    )
    subprocess.run(
      git + ["config", "user.name", "t"], check=True,
      timeout=60,
    )
    (root / "a.txt").write_text("one\n")
    subprocess.run(
      git + ["add", "-A"], check=True, timeout=60
    )
    subprocess.run(
      git + ["commit", "-q", "-m", "first",
             "--no-gpg-sign"],
      check=True, timeout=60,
    )
    # The worker's side: clone, commit, bundle the delta.
    work = tmp_path / "work"
    subprocess.run(
      ["git", "clone", "-q", str(root), str(work)],
      check=True, timeout=120,
    )
    wgit = ["git", "-C", str(work)]
    subprocess.run(
      wgit + ["config", "user.email", "t@t"], check=True,
      timeout=60,
    )
    subprocess.run(
      wgit + ["config", "user.name", "t"], check=True,
      timeout=60,
    )
    (work / "b.txt").write_text("two\n")
    subprocess.run(
      wgit + ["add", "-A"], check=True, timeout=60
    )
    subprocess.run(
      wgit + ["commit", "-q", "-m", "worker change",
              "--no-gpg-sign"],
      check=True, timeout=60,
    )
    bundle = tmp_path / "d.bundle"
    subprocess.run(
      wgit + ["bundle", "create", str(bundle),
              "origin/main..main", "main"],
      check=True, capture_output=True, timeout=120,
    )
    monkeypatch.setattr(
      worker_ops, "load_repos_config",
      lambda: {"repos": {"repo-a": {"push_order": 1}}},
    )
    monkeypatch.setattr(
      worker_ops, "get_repo_path", lambda name: root
    )
    return root, bundle.read_bytes()

  def test_accepts_and_fast_forwards(
    self, tmp_db, root_repo
  ):
    """Delivered commits land on the root repo branch.

    The root repo is detached first, which is how a root
    repo should sit: git refuses to move a branch that is
    checked out, so a detached HEAD lets any branch be
    delivered directly.
    """
    root, bundle = root_repo
    subprocess.run(
      ["git", "-C", str(root), "checkout", "-q",
       "--detach"],
      check=True, timeout=60,
    )
    make_run(tmp_db)

    async def body(client):
      await register(client, "w1")
      work = await take_lease(client)
      resp = await client.post(
        f"/api/workers/w1/bundle?run_id={work['run_id']}"
        f"&generation={work['generation']}"
        f"&repo=repo-a&branch=main",
        data=bundle,
      )
      assert resp.status == 200, await resp.text()
      data = (await resp.json())["data"]
      assert data["head"]
      assert data["parked"] is False
      assert data["ref"] == "refs/heads/main"
      log = subprocess.run(
        ["git", "-C", str(root), "log", "--oneline",
         "main"],
        capture_output=True, text=True, timeout=60,
      ).stdout
      assert "worker change" in log

    run_client(body)

  def test_parks_when_branch_is_checked_out(
    self, tmp_db, root_repo
  ):
    """A checked-out branch parks instead of failing.

    Root repos are not consistently detached — OTC.Relay
    sits on master and PIXConnect on main — so this is
    reachable in normal use. The commits must still arrive:
    losing an hour of a worker's output because of how a
    root repo happens to be checked out is not acceptable,
    and neither is yanking a branch out from under a
    working tree.
    """
    root, bundle = root_repo
    # Leave main checked out, as OTC.Relay and PIXConnect
    # actually are.
    assert subprocess.run(
      ["git", "-C", str(root), "symbolic-ref", "-q",
       "HEAD"],
      capture_output=True, text=True, timeout=60,
    ).stdout.strip() == "refs/heads/main"
    make_run(tmp_db)

    async def body(client):
      await register(client, "w1")
      work = await take_lease(client)
      resp = await client.post(
        f"/api/workers/w1/bundle?run_id={work['run_id']}"
        f"&generation={work['generation']}"
        f"&repo=repo-a&branch=main",
        data=bundle,
      )
      assert resp.status == 200, await resp.text()
      data = (await resp.json())["data"]
      assert data["parked"] is True
      assert data["ref"] == "refs/takt/incoming/main"
      assert "checked out" in data["note"]
      # The commits are present under the side ref.
      log = subprocess.run(
        ["git", "-C", str(root), "log", "--oneline",
         "refs/takt/incoming/main"],
        capture_output=True, text=True, timeout=60,
      ).stdout
      assert "worker change" in log
      # And the checked-out branch was left alone.
      main_log = subprocess.run(
        ["git", "-C", str(root), "log", "--oneline",
         "main"],
        capture_output=True, text=True, timeout=60,
      ).stdout
      assert "worker change" not in main_log

    run_client(body)

  def test_unknown_repo_is_refused(
    self, tmp_db, root_repo
  ):
    """A worker cannot name a repo takt does not manage."""
    _, bundle = root_repo
    make_run(tmp_db)

    async def body(client):
      await register(client, "w1")
      work = await take_lease(client)
      resp = await client.post(
        f"/api/workers/w1/bundle?run_id={work['run_id']}"
        f"&generation={work['generation']}"
        f"&repo=../../etc&branch=main",
        data=bundle,
      )
      assert resp.status == 400
      assert "unknown repo" in (await resp.json())[
        "message"
      ]

    run_client(body)

  def test_the_word_repos_is_not_a_repo(
    self, tmp_db, root_repo
  ):
    """'repos' must not pass the managed-repo check.

    load_repos_config() normalises a flat repos.yaml by
    adding a "repos" key alongside the repo names. Testing
    membership against the top level would therefore accept
    repo="repos" -- a name no registry contains -- and let
    a worker past the check that exists to stop it naming
    arbitrary paths.
    """
    _, bundle = root_repo
    make_run(tmp_db)

    async def body(client):
      await register(client, "w1")
      work = await take_lease(client)
      resp = await client.post(
        f"/api/workers/w1/bundle?run_id={work['run_id']}"
        f"&generation={work['generation']}"
        f"&repo=repos&branch=main",
        data=bundle,
      )
      assert resp.status == 400
      assert "unknown repo" in (await resp.json())[
        "message"
      ]

    run_client(body)

  def test_unsafe_branch_is_refused(
    self, tmp_db, root_repo
  ):
    """A branch name that could be an option is refused."""
    _, bundle = root_repo
    make_run(tmp_db)

    async def body(client):
      await register(client, "w1")
      work = await take_lease(client)
      for branch in ("--upload-pack=sh", "a b", "-x"):
        resp = await client.post(
          f"/api/workers/w1/bundle?run_id={work['run_id']}"
          f"&generation={work['generation']}"
          f"&repo=repo-a&branch={branch}",
          data=bundle,
        )
        assert resp.status == 400, branch

    run_client(body)

  def test_garbage_bundle_is_refused(
    self, tmp_db, root_repo
  ):
    """A body that is not a bundle fails verification."""
    make_run(tmp_db)

    async def body(client):
      await register(client, "w1")
      work = await take_lease(client)
      resp = await client.post(
        f"/api/workers/w1/bundle?run_id={work['run_id']}"
        f"&generation={work['generation']}"
        f"&repo=repo-a&branch=main",
        data=b"not a git bundle at all",
      )
      assert resp.status == 400
      assert "verification" in (await resp.json())[
        "message"
      ]

    run_client(body)

  def test_empty_bundle_is_refused(
    self, tmp_db, root_repo
  ):
    """An empty body is refused."""
    make_run(tmp_db)

    async def body(client):
      await register(client, "w1")
      work = await take_lease(client)
      resp = await client.post(
        f"/api/workers/w1/bundle?run_id={work['run_id']}"
        f"&generation={work['generation']}"
        f"&repo=repo-a&branch=main",
        data=b"",
      )
      assert resp.status == 400

    run_client(body)

  def test_stale_lease_cannot_deliver(
    self, tmp_db, root_repo
  ):
    """A worker that lost its lease cannot push commits.

    Otherwise a machine that went dark could land history
    on a branch another worker has since taken over.
    """
    _, bundle = root_repo
    run_id = make_run(tmp_db)

    async def body(client):
      await register(client, "w1")
      work = await take_lease(client)
      db.release_run(run_id, db_path=tmp_db)
      db.lease_run("w2", db_path=tmp_db)
      resp = await client.post(
        f"/api/workers/w1/bundle?run_id={run_id}"
        f"&generation={work['generation']}"
        f"&repo=repo-a&branch=main",
        data=bundle,
      )
      assert resp.status == 409

    run_client(body)

  def test_missing_params_are_rejected(self, tmp_db):
    """Delivery without the query params is a 400."""
    make_run(tmp_db)

    async def body(client):
      await register(client, "w1")
      await take_lease(client)
      resp = await client.post(
        "/api/workers/w1/bundle", data=b"x"
      )
      assert resp.status == 400
      assert "missing query params" in (
        await resp.json()
      )["message"]

    run_client(body)
