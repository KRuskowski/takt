"""Tests for the worker registry and run leases.

The property that matters most is that one run cannot be
claimed twice: two machines executing the same pipeline run
would push conflicting commits. The concurrency test here
hammers a real database with threads rather than asserting
the SQL looks right.
"""

import concurrent.futures as futures

import pytest

from lib import db


@pytest.fixture
def tmp_db(tmp_path):
  """A migrated temporary database."""
  path = tmp_path / "test.db"
  db.migrate(db_path=str(path))
  return str(path)


def make_run(tmp_db, workspace="ws1"):
  """Create a queued run with one step."""
  db.define_pipeline(workspace, [
    {"name": "test", "step_type": "agent"},
  ], db_path=tmp_db)
  return db.create_run(
    workspace, "manual", ["repo-a"], {"repo-a": "abc"},
    db_path=tmp_db,
  )


class TestMigration:
  """Schema additions land on an existing database."""

  def test_creates_worker_tables(self, tmp_db):
    """workers and target_locks exist."""
    with db._connect(tmp_db) as conn:
      names = {
        r["name"] for r in conn.execute(
          "SELECT name FROM sqlite_master "
          "WHERE type='table'"
        ).fetchall()
      }
    assert "workers" in names
    assert "target_locks" in names

  def test_adds_lease_columns_to_runs(self, tmp_db):
    """runs gains the lease columns."""
    with db._connect(tmp_db) as conn:
      cols = {
        r["name"] for r in conn.execute(
          "PRAGMA table_info(runs)"
        ).fetchall()
      }
    assert {"worker_id", "lease_generation",
            "lease_expires_at"} <= cols

  def test_migrate_is_idempotent(self, tmp_db):
    """Re-migrating does not fail on existing columns.

    ALTER TABLE has no IF NOT EXISTS, so a second migrate()
    would throw if the columns were added unconditionally.
    """
    db.migrate(db_path=tmp_db)
    db.migrate(db_path=tmp_db)


class TestWorkerRegistry:
  """Registration and liveness."""

  def test_registers_a_worker(self, tmp_db):
    """A worker appears with its capabilities."""
    db.register_worker(
      "w1", host="10.0.0.5", os_name="debian", cores=8,
      ram_mb=16000, free_disk_mb=500000,
      capabilities={"cmake": True, "claude": False},
      db_path=tmp_db,
    )
    workers = db.list_workers(db_path=tmp_db)
    assert len(workers) == 1
    assert workers[0]["id"] == "w1"
    assert workers[0]["cores"] == 8
    assert workers[0]["capabilities"]["cmake"] is True

  def test_reregistration_updates_in_place(self, tmp_db):
    """A restarted worker does not duplicate itself."""
    db.register_worker("w1", cores=8, db_path=tmp_db)
    db.register_worker("w1", cores=16, db_path=tmp_db)
    workers = db.list_workers(db_path=tmp_db)
    assert len(workers) == 1
    assert workers[0]["cores"] == 16

  def test_heartbeat_requires_registration(self, tmp_db):
    """Heartbeat from an unknown worker reports failure."""
    assert not db.worker_heartbeat("ghost", db_path=tmp_db)

  def test_heartbeat_updates_disk(self, tmp_db):
    """Free space is refreshed by the heartbeat."""
    db.register_worker(
      "w1", free_disk_mb=1000, db_path=tmp_db
    )
    assert db.worker_heartbeat(
      "w1", free_disk_mb=250, db_path=tmp_db
    )
    assert db.list_workers(
      db_path=tmp_db
    )[0]["free_disk_mb"] == 250

  def test_draining_state(self, tmp_db):
    """A worker can be retired without being killed."""
    db.register_worker("w1", db_path=tmp_db)
    assert db.set_worker_state(
      "w1", "draining", db_path=tmp_db
    )
    assert db.list_workers(
      db_path=tmp_db
    )[0]["state"] == "draining"

  def test_rejects_unknown_state(self, tmp_db):
    """A typo'd state is refused."""
    db.register_worker("w1", db_path=tmp_db)
    with pytest.raises(ValueError, match="invalid worker"):
      db.set_worker_state("w1", "sleepy", db_path=tmp_db)

  def _age(self, tmp_db, worker_id, stamp):
    """Backdate a worker's last_seen to a fixed time.

    Deliberately not done by sleeping or by passing a zero
    timeout: both make the result depend on whether two
    strftime calls land in the same millisecond, which is a
    flaky test waiting to happen.
    """
    with db._connect(tmp_db) as conn:
      conn.execute(
        "UPDATE workers SET last_seen = ? WHERE id = ?",
        (stamp, worker_id),
      )

  def test_stale_worker_goes_offline(self, tmp_db):
    """Silence past the timeout marks a worker offline."""
    db.register_worker("w1", db_path=tmp_db)
    self._age(tmp_db, "w1", "2020-01-01T00:00:00.000Z")
    marked = db.mark_stale_workers_offline(
      timeout_secs=120, db_path=tmp_db
    )
    assert marked == ["w1"]
    assert db.list_workers(
      db_path=tmp_db
    )[0]["state"] == "offline"

  def test_already_offline_is_not_remarked(self, tmp_db):
    """A worker is reported offline once, not every sweep."""
    db.register_worker("w1", db_path=tmp_db)
    self._age(tmp_db, "w1", "2020-01-01T00:00:00.000Z")
    db.mark_stale_workers_offline(
      timeout_secs=120, db_path=tmp_db
    )
    assert db.mark_stale_workers_offline(
      timeout_secs=120, db_path=tmp_db
    ) == []

  def test_heartbeat_brings_a_worker_back(self, tmp_db):
    """A returning worker goes back online."""
    db.register_worker("w1", db_path=tmp_db)
    self._age(tmp_db, "w1", "2020-01-01T00:00:00.000Z")
    db.mark_stale_workers_offline(
      timeout_secs=120, db_path=tmp_db
    )
    assert db.worker_heartbeat("w1", db_path=tmp_db)
    assert db.list_workers(
      db_path=tmp_db
    )[0]["state"] == "online"

  def test_fresh_worker_stays_online(self, tmp_db):
    """A heartbeating worker is not marked offline."""
    db.register_worker("w1", db_path=tmp_db)
    assert db.mark_stale_workers_offline(
      timeout_secs=300, db_path=tmp_db
    ) == []


class TestLeasing:
  """Claiming runs."""

  def test_leases_a_queued_run(self, tmp_db):
    """A queued run is handed out with a generation."""
    run_id = make_run(tmp_db)
    leased = db.lease_run("w1", db_path=tmp_db)
    assert leased["id"] == run_id
    assert leased["worker_id"] == "w1"
    assert leased["lease_generation"] == 1
    assert leased["lease_expires_at"]

  def test_returns_none_when_nothing_queued(self, tmp_db):
    """An empty queue yields nothing."""
    assert db.lease_run("w1", db_path=tmp_db) is None

  def test_claim_is_exclusive(self, tmp_db):
    """A second worker cannot take a claimed run."""
    make_run(tmp_db)
    assert db.lease_run("w1", db_path=tmp_db) is not None
    assert db.lease_run("w2", db_path=tmp_db) is None

  def test_leases_in_creation_order(self, tmp_db):
    """Oldest queued run goes first."""
    first = make_run(tmp_db, "ws1")
    second = make_run(tmp_db, "ws2")
    assert db.lease_run(
      "w1", db_path=tmp_db
    )["id"] == first
    assert db.lease_run(
      "w2", db_path=tmp_db
    )["id"] == second

  def test_status_is_untouched_by_claiming(self, tmp_db):
    """Leasing does not advance the run state machine.

    Claiming is ownership, not progress. Conflating them
    would mean distribution had to widen the set of legal
    status transitions.
    """
    run_id = make_run(tmp_db)
    db.lease_run("w1", db_path=tmp_db)
    assert db.get_run(
      run_id, db_path=tmp_db
    )["status"] == "queued"

  def test_returns_the_row_it_claimed(self, tmp_db):
    """Each lease returns its own run.

    Regression guard: reading the claimed row back by
    worker_id returns an arbitrary one as soon as a worker
    holds two runs.
    """
    first = make_run(tmp_db, "ws1")
    second = make_run(tmp_db, "ws2")
    a = db.lease_run("w1", db_path=tmp_db)
    b = db.lease_run("w1", db_path=tmp_db)
    assert {a["id"], b["id"]} == {first, second}
    assert a["id"] != b["id"]


class TestLeaseExclusionUnderConcurrency:
  """The property that stops a run executing twice."""

  def test_only_one_worker_wins_each_run(self, tmp_path):
    """20 threads racing for 5 runs produce 5 winners.

    Two machines running the same pipeline run would push
    conflicting commits, so this is tested against a real
    database with real threads rather than by reading the
    SQL.
    """
    path = str(tmp_path / "race.db")
    db.migrate(db_path=path)
    for i in range(5):
      make_run(path, f"ws{i}")

    def claim():
      try:
        return db.lease_run(
          "w%d" % id(object()), lease_secs=60,
          db_path=path,
        )
      except Exception:
        return None

    with futures.ThreadPoolExecutor(max_workers=20) as ex:
      results = list(ex.map(
        lambda _: claim(), range(20)
      ))
    won = [r for r in results if r]
    ids = [r["id"] for r in won]
    assert len(won) == 5, f"expected 5 winners, got {len(won)}"
    assert len(set(ids)) == 5, "a run was claimed twice"


class TestRenewal:
  """Keeping a lease alive."""

  def test_holder_can_renew(self, tmp_db):
    """The rightful holder extends its lease."""
    run_id = make_run(tmp_db)
    leased = db.lease_run("w1", db_path=tmp_db)
    assert db.renew_lease(
      run_id, "w1", leased["lease_generation"],
      db_path=tmp_db,
    )

  def test_other_worker_cannot_renew(self, tmp_db):
    """A worker cannot renew someone else's lease."""
    run_id = make_run(tmp_db)
    leased = db.lease_run("w1", db_path=tmp_db)
    assert not db.renew_lease(
      run_id, "w2", leased["lease_generation"],
      db_path=tmp_db,
    )

  def test_stale_generation_cannot_renew(self, tmp_db):
    """A reclaimed run rejects the old holder.

    This is how a worker that was offline long enough to
    lose its lease discovers it must discard its results
    instead of reporting them.
    """
    run_id = make_run(tmp_db)
    first = db.lease_run("w1", db_path=tmp_db)
    db.release_run(run_id, db_path=tmp_db)
    db.lease_run("w2", db_path=tmp_db)
    assert not db.renew_lease(
      run_id, "w1", first["lease_generation"],
      db_path=tmp_db,
    )

  def test_generation_increments_per_claim(self, tmp_db):
    """Each claim is distinguishable from the last."""
    run_id = make_run(tmp_db)
    assert db.lease_run(
      "w1", db_path=tmp_db
    )["lease_generation"] == 1
    db.release_run(run_id, db_path=tmp_db)
    assert db.lease_run(
      "w2", db_path=tmp_db
    )["lease_generation"] == 2


class TestRelease:
  """Giving a run back."""

  def test_release_makes_it_claimable_again(self, tmp_db):
    """A released run can be taken by another worker."""
    run_id = make_run(tmp_db)
    db.lease_run("w1", db_path=tmp_db)
    assert db.release_run(run_id, db_path=tmp_db)
    assert db.lease_run("w2", db_path=tmp_db) is not None

  def test_release_checks_the_holder(self, tmp_db):
    """A worker cannot release a run it does not hold."""
    run_id = make_run(tmp_db)
    db.lease_run("w1", db_path=tmp_db)
    assert not db.release_run(
      run_id, worker_id="w2", db_path=tmp_db
    )
    assert db.lease_run("w3", db_path=tmp_db) is None

  def test_release_checks_the_generation(self, tmp_db):
    """A stale generation cannot release."""
    run_id = make_run(tmp_db)
    db.lease_run("w1", db_path=tmp_db)
    assert not db.release_run(
      run_id, worker_id="w1", generation=99,
      db_path=tmp_db,
    )


class TestSweep:
  """Recovering from a worker that vanished."""

  def test_expired_lease_is_released(self, tmp_db):
    """A lapsed claim returns to the queue."""
    run_id = make_run(tmp_db)
    db.lease_run("w1", lease_secs=-5, db_path=tmp_db)
    swept = db.sweep_expired_leases(db_path=tmp_db)
    assert swept == [(run_id, "w1")]
    assert db.lease_run("w2", db_path=tmp_db) is not None

  def test_live_lease_is_left_alone(self, tmp_db):
    """A current claim is not swept."""
    make_run(tmp_db)
    db.lease_run("w1", lease_secs=3600, db_path=tmp_db)
    assert db.sweep_expired_leases(db_path=tmp_db) == []
    assert db.lease_run("w2", db_path=tmp_db) is None

  def test_only_queued_runs_are_swept(self, tmp_db):
    """A run that already advanced is not reclaimed.

    Once a run is running or finished, releasing its claim
    would invite a second worker to pick up work that is
    underway or done.
    """
    run_id = make_run(tmp_db)
    db.lease_run("w1", lease_secs=-5, db_path=tmp_db)
    db.advance_run(run_id, db_path=tmp_db)
    status = db.get_run(run_id, db_path=tmp_db)["status"]
    assert status != "queued"
    assert db.sweep_expired_leases(db_path=tmp_db) == []

  def test_sweep_is_safe_when_nothing_expired(
    self, tmp_db
  ):
    """An empty sweep is not an error."""
    assert db.sweep_expired_leases(db_path=tmp_db) == []


class TestSchedulerDoesNotRaceWorkers:
  """The scheduler must not run what a worker holds.

  lease_run() deliberately leaves status as 'queued' --
  claiming is not a state transition. So anything selecting
  queued runs by status alone picks up runs a worker is
  already executing. get_next_queued_run() did exactly
  that, and the service loop launches whatever it returns,
  so a leased run was executed twice: once on the worker
  and once on the scheduler. The generation-stamped lease
  prevents a *second worker* taking it; nothing prevented
  the scheduler.
  """

  def test_leased_run_is_invisible_to_the_scheduler(
    self, tmp_db
  ):
    run_id = make_run(tmp_db)
    db.register_worker("w1", db_path=tmp_db)
    leased = db.lease_run("w1", db_path=tmp_db)
    assert leased["id"] == run_id
    # Still 'queued' -- that is the point.
    assert leased["status"] == "queued"
    assert db.get_next_queued_run(db_path=tmp_db) is None

  def test_unclaimed_run_is_still_offered(self, tmp_db):
    """The fix must not starve the scheduler."""
    run_id = make_run(tmp_db)
    nxt = db.get_next_queued_run(db_path=tmp_db)
    assert nxt is not None and nxt["id"] == run_id

  def test_expired_lease_returns_it_to_the_scheduler(
    self, tmp_db
  ):
    """A worker that dies must not park a run forever.

    sweep_expired_leases() clears worker_id, which is what
    makes excluding claimed runs safe rather than a leak.
    """
    run_id = make_run(tmp_db)
    db.register_worker("w1", db_path=tmp_db)
    db.lease_run("w1", lease_secs=-1, db_path=tmp_db)
    assert db.get_next_queued_run(db_path=tmp_db) is None
    swept = db.sweep_expired_leases(db_path=tmp_db)
    assert (run_id, "w1") in swept
    nxt = db.get_next_queued_run(db_path=tmp_db)
    assert nxt is not None and nxt["id"] == run_id

  def test_released_run_returns_to_the_scheduler(
    self, tmp_db
  ):
    run_id = make_run(tmp_db)
    db.register_worker("w1", db_path=tmp_db)
    leased = db.lease_run("w1", db_path=tmp_db)
    db.release_run(
      run_id, worker_id="w1",
      generation=leased["lease_generation"],
      db_path=tmp_db,
    )
    nxt = db.get_next_queued_run(db_path=tmp_db)
    assert nxt is not None and nxt["id"] == run_id
