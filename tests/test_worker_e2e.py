"""End-to-end: a real worker process against a real API.

Starts the scheduler's aiohttp app on a real port backed by
a real database, then runs bin/takt_worker.py as a separate
process with --once. Nothing is mocked: the worker speaks
HTTP, clones a git mirror, creates a worktree at the exact
recorded commit, reports through the API, and releases its
lease.

This is the test that says the feature works, as opposed to
the units behaving.
"""

import asyncio
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from aiohttp import web

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib import api, db


class SchedulerThread:
  """Runs the API in a background thread on a real port."""

  def __init__(self):
    self.port = None
    self._loop = None
    self._runner = None
    self._thread = None
    self._ready = threading.Event()

  def start(self):
    """Start serving and return the bound port."""
    self._thread = threading.Thread(
      target=self._serve, daemon=True
    )
    self._thread.start()
    assert self._ready.wait(timeout=30), "API did not start"
    return self.port

  def _serve(self):
    self._loop = asyncio.new_event_loop()
    asyncio.set_event_loop(self._loop)
    self._loop.run_until_complete(self._boot())
    self._loop.run_forever()

  async def _boot(self):
    app = api.build_app(service=None)
    self._runner = web.AppRunner(app)
    await self._runner.setup()
    site = web.TCPSite(self._runner, "127.0.0.1", 0)
    await site.start()
    self.port = site._server.sockets[0].getsockname()[1]
    self._ready.set()

  def stop(self):
    """Shut the server down."""
    if self._loop:
      self._loop.call_soon_threadsafe(self._loop.stop)
    if self._thread:
      self._thread.join(timeout=10)


@pytest.fixture
def scheduler(tmp_path, monkeypatch):
  """A live API on a real port with a temp database."""
  path = tmp_path / "e2e.db"
  monkeypatch.setattr(db, "DB_PATH", path)
  db.migrate(db_path=str(path))
  server = SchedulerThread()
  port = server.start()
  yield f"http://127.0.0.1:{port}", str(path)
  server.stop()


@pytest.fixture
def upstream_repo(tmp_path):
  """An upstream repo on the workspace branch."""
  up = tmp_path / "upstream" / "repo-a"
  up.mkdir(parents=True)
  g = ["git", "-C", str(up)]
  subprocess.run(
    ["git", "init", "-q", "-b", "feat-x", str(up)],
    check=True, timeout=60,
  )
  for k, v in (("user.email", "t@t"), ("user.name", "t")):
    subprocess.run(
      g + ["config", k, v], check=True, timeout=60
    )
  (up / "src.txt").write_text("code\n")
  subprocess.run(g + ["add", "-A"], check=True, timeout=60)
  subprocess.run(
    g + ["commit", "-q", "-m", "work", "--no-gpg-sign"],
    check=True, timeout=60,
  )
  sha = subprocess.run(
    g + ["rev-parse", "HEAD"], capture_output=True,
    text=True, timeout=60,
  ).stdout.strip()
  return up, sha


def run_worker(base, work_dir, upstream_dir, extra=()):
  """Run bin/takt_worker.py --once as a real process.

  Uses the {repo} template form of --upstream so the
  mirrors clone from a local path, with no SSH involved.
  """
  env = dict(os.environ)
  env["TAKT_API_URL"] = base
  env.pop("TAKT_API_TOKEN", None)
  cmd = [
    sys.executable,
    str(PROJECT_DIR / "bin" / "takt_worker.py"),
    "--id", "e2e-worker",
    "--work-dir", str(work_dir),
    "--upstream", f"{str(upstream_dir).rstrip('/')}/{{repo}}",
    "--no-agent-steps",
    "--once",
    *extra,
  ]
  return subprocess.run(
    cmd, capture_output=True, text=True, timeout=600,
    env=env, cwd=str(PROJECT_DIR),
  )


class TestWorkerEndToEnd:
  """A worker process doing a complete run."""

  def test_executes_a_run_and_releases_the_lease(
    self, scheduler, upstream_repo, tmp_path
  ):
    """Register, lease, materialise, report, finish.

    The step is push_to_github, which a worker must skip
    rather than execute — the scheduler holds the
    credentials and is the only gate on GitHub. That still
    exercises the whole loop: HTTP, a real mirror clone, a
    worktree at the exact SHA, step reporting and release.
    """
    base, db_path = scheduler
    up, sha = upstream_repo

    db.define_pipeline("feat-x", [
      {"name": "push_to_github", "step_type": "script"},
    ], db_path=db_path)
    run_id = db.create_run(
      "feat-x", "manual", ["repo-a"],
      {"repo-a:feat-x": sha}, db_path=db_path,
    )

    proc = run_worker(
      base, tmp_path / "wkr", up.parent,
      extra=["--mirror-dir", str(tmp_path / "mir")],
    )
    assert proc.returncode == 0, (
      f"worker failed\nSTDOUT:\n{proc.stdout}\n"
      f"STDERR:\n{proc.stderr}"
    )

    # Registered itself.
    workers = db.list_workers(db_path=db_path)
    assert [w["id"] for w in workers] == ["e2e-worker"]
    assert workers[0]["capabilities"][
      "agent_steps"
    ] is False

    # The step was skipped, not run, and the run advanced.
    steps = db.get_run_steps(run_id, db_path=db_path)
    assert steps[0]["status"] == "skipped", proc.stderr
    run = db.get_run(run_id, db_path=db_path)
    assert run["worker_id"] is None, "lease not released"
    # Exactly passed: a run whose steps all skip must reach
    # a terminal state, not sit in queued forever.
    assert run["status"] == "passed", run
    assert run["finished_at"]
    # The worktree was cleaned up, since build output is
    # the dominant disk cost on an unattended machine.
    runs_dir = tmp_path / "wkr" / f"feat-x-{run_id}"
    assert not (runs_dir / "repo-a").exists()

  def test_idle_worker_exits_cleanly(
    self, scheduler, upstream_repo, tmp_path
  ):
    """With an empty queue the worker just leaves."""
    base, db_path = scheduler
    up, _ = upstream_repo
    proc = run_worker(
      base, tmp_path / "wkr2", up.parent,
      extra=["--mirror-dir", str(tmp_path / "mir2")],
    )
    assert proc.returncode == 0, proc.stderr
    assert db.list_workers(db_path=db_path)

  def test_disk_floor_stops_a_full_worker(
    self, scheduler, upstream_repo, tmp_path
  ):
    """Below the floor, no work is taken.

    A worker that fills its disk mid-build fails the run it
    holds and every one after it, which is worse than one
    that stops accepting.
    """
    base, db_path = scheduler
    up, sha = upstream_repo
    db.define_pipeline("feat-x", [
      {"name": "push_to_github", "step_type": "script"},
    ], db_path=db_path)
    run_id = db.create_run(
      "feat-x", "manual", ["repo-a"],
      {"repo-a:feat-x": sha}, db_path=db_path,
    )
    proc = run_worker(
      base, tmp_path / "wkr3", up.parent,
      extra=[
        "--mirror-dir", str(tmp_path / "mir3"),
        # Absurd floor, so any real disk is "full".
        "--disk-floor-mb", "999999999",
      ],
    )
    assert proc.returncode == 0, proc.stderr
    assert "below the" in proc.stderr
    run = db.get_run(run_id, db_path=db_path)
    assert run["status"] == "queued"
    assert run["worker_id"] is None, "claimed while full"
