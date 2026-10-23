"""Tests for target claims in SQLite.

A target is a real VM or a real piece of hardware. Two
workspaces believing they hold the same one means two
builds fighting over a machine, so exclusion is tested
against a real database with real threads.
"""

import concurrent.futures as futures
import json

import pytest

from lib import db, target_ops


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
  """A migrated temporary database."""
  path = tmp_path / "locks.db"
  monkeypatch.setattr(db, "DB_PATH", path)
  db.migrate(db_path=str(path))
  # Each test gets a fresh import flag, since the legacy
  # import runs once per process.
  monkeypatch.setattr(
    target_ops, "_imported_file_locks", True
  )
  return str(path)


@pytest.fixture
def locks_dir(tmp_path, monkeypatch):
  """An empty legacy .locks directory."""
  d = tmp_path / "locks"
  d.mkdir()
  monkeypatch.setattr(target_ops, "LOCKS_DIR", d)
  return d


class TestClaiming:
  """Exclusive claims."""

  def test_claim_a_free_target(self, tmp_db):
    """A free target is claimed."""
    ok, existing = db.claim_target(
      "deb-02", "ws1", db_path=tmp_db
    )
    assert ok
    assert existing is None

  def test_second_claim_is_refused(self, tmp_db):
    """A held target cannot be claimed again."""
    db.claim_target("deb-02", "ws1", db_path=tmp_db)
    ok, existing = db.claim_target(
      "deb-02", "ws2", db_path=tmp_db
    )
    assert not ok
    assert existing["workspace"] == "ws1"

  def test_release_frees_it(self, tmp_db):
    """After release the target is claimable."""
    db.claim_target("deb-02", "ws1", db_path=tmp_db)
    removed = db.release_target("deb-02", db_path=tmp_db)
    assert removed["workspace"] == "ws1"
    ok, _ = db.claim_target(
      "deb-02", "ws2", db_path=tmp_db
    )
    assert ok

  def test_release_unclaimed_is_none(self, tmp_db):
    """Releasing a free target is not an error."""
    assert db.release_target(
      "deb-02", db_path=tmp_db
    ) is None

  def test_takeover_replaces_the_holder(self, tmp_db):
    """write_lock's old behaviour is still available."""
    db.claim_target("deb-02", "ws1", db_path=tmp_db)
    ok, _ = db.claim_target(
      "deb-02", "ws2", takeover=True, db_path=tmp_db
    )
    assert ok
    assert db.get_target_lock(
      "deb-02", db_path=tmp_db
    )["workspace"] == "ws2"

  def test_records_the_worker(self, tmp_db):
    """A claim notes which worker made it.

    With runs on several machines, "which workspace" is no
    longer enough to find who is using a VM.
    """
    db.claim_target(
      "deb-02", "ws1", worker_id="build-01",
      db_path=tmp_db,
    )
    lock = db.get_target_lock("deb-02", db_path=tmp_db)
    assert lock["worker_id"] == "build-01"

  def test_lists_all_claims(self, tmp_db):
    """Claims are listable for an operator view."""
    db.claim_target("deb-02", "ws1", db_path=tmp_db)
    db.claim_target("win-01", "ws2", db_path=tmp_db)
    locks = db.list_target_locks(db_path=tmp_db)
    assert set(locks) == {"deb-02", "win-01"}
    assert locks["win-01"]["workspace"] == "ws2"

  def test_independent_targets_do_not_interfere(
    self, tmp_db
  ):
    """Claiming one target leaves others free."""
    db.claim_target("deb-02", "ws1", db_path=tmp_db)
    ok, _ = db.claim_target(
      "deb-03", "ws2", db_path=tmp_db
    )
    assert ok


class TestExclusionUnderConcurrency:
  """Only one claimant can win."""

  def test_one_winner_per_target(self, tmp_path):
    """16 threads racing for one VM yield one winner.

    The old scheme read a lock file and then wrote one, so
    two processes could both pass the check and the second
    silently took the machine.
    """
    path = str(tmp_path / "race.db")
    db.migrate(db_path=path)

    def claim(i):
      ok, _ = db.claim_target(
        "deb-02", f"ws{i}", db_path=path
      )
      return ok

    with futures.ThreadPoolExecutor(max_workers=16) as ex:
      results = list(ex.map(claim, range(16)))
    assert sum(1 for r in results if r) == 1, (
      f"{sum(1 for r in results if r)} claimants won"
    )

  def test_many_targets_many_claimants(self, tmp_path):
    """Four targets, 16 racers, four winners."""
    path = str(tmp_path / "race2.db")
    db.migrate(db_path=path)

    def claim(i):
      ok, _ = db.claim_target(
        f"deb-{i % 4}", f"ws{i}", db_path=path
      )
      return ok

    with futures.ThreadPoolExecutor(max_workers=16) as ex:
      results = list(ex.map(claim, range(16)))
    assert sum(1 for r in results if r) == 4
    assert len(
      db.list_target_locks(db_path=path)
    ) == 4


class TestLegacyFileLockImport:
  """Claims in flight at upgrade time survive."""

  def test_imports_existing_lock_files(
    self, tmp_db, locks_dir
  ):
    """A .lock file becomes a row and the file goes."""
    (locks_dir / "dev-01.lock").write_text(json.dumps({
      "workspace": "relay-v1.1-alarms",
      "claimed_at": "2026-10-01T09:00:00+00:00",
    }))
    imported = target_ops.import_file_locks(db_path=tmp_db)
    assert imported == ["dev-01"]
    lock = db.get_target_lock("dev-01", db_path=tmp_db)
    assert lock["workspace"] == "relay-v1.1-alarms"
    assert lock["claimed_at"].startswith("2026-10-01")
    assert not (locks_dir / "dev-01.lock").exists()

  def test_import_is_idempotent(self, tmp_db, locks_dir):
    """A second import finds nothing to do."""
    (locks_dir / "dev-01.lock").write_text(
      json.dumps({"workspace": "ws1"})
    )
    target_ops.import_file_locks(db_path=tmp_db)
    assert target_ops.import_file_locks(
      db_path=tmp_db
    ) == []

  def test_live_claim_is_not_overwritten(
    self, tmp_db, locks_dir
  ):
    """A stale file does not displace a current claim."""
    db.claim_target("dev-01", "current-ws", db_path=tmp_db)
    (locks_dir / "dev-01.lock").write_text(
      json.dumps({"workspace": "stale-ws"})
    )
    target_ops.import_file_locks(db_path=tmp_db)
    assert db.get_target_lock(
      "dev-01", db_path=tmp_db
    )["workspace"] == "current-ws"

  def test_corrupt_file_does_not_block_import(
    self, tmp_db, locks_dir
  ):
    """Unreadable JSON is discarded, not fatal."""
    (locks_dir / "bad.lock").write_text("{not json")
    (locks_dir / "good.lock").write_text(
      json.dumps({"workspace": "ws1"})
    )
    imported = target_ops.import_file_locks(db_path=tmp_db)
    assert "good" in imported
    assert not (locks_dir / "bad.lock").exists()

  def test_missing_locks_dir_is_fine(
    self, tmp_db, tmp_path, monkeypatch
  ):
    """No legacy directory means nothing to import."""
    monkeypatch.setattr(
      target_ops, "LOCKS_DIR", tmp_path / "nope"
    )
    assert target_ops.import_file_locks(
      db_path=tmp_db
    ) == []


class TestTargetOpsWrappers:
  """The façade the CLI and API use."""

  def test_claim_read_release_cycle(
    self, tmp_db, locks_dir
  ):
    """claim_lock, read_lock and release_lock agree."""
    ok, _ = target_ops.claim_lock(
      "deb-02", "ws1", db_path=tmp_db
    )
    assert ok
    assert target_ops.read_lock(
      "deb-02", db_path=tmp_db
    )["workspace"] == "ws1"
    assert target_ops.release_lock(
      "deb-02", db_path=tmp_db
    )["workspace"] == "ws1"
    assert target_ops.read_lock(
      "deb-02", db_path=tmp_db
    ) is None

  def test_claim_lock_refuses_a_held_target(
    self, tmp_db, locks_dir
  ):
    """The wrapper reports the conflict, not an exception."""
    target_ops.claim_lock("deb-02", "ws1", db_path=tmp_db)
    ok, existing = target_ops.claim_lock(
      "deb-02", "ws2", db_path=tmp_db
    )
    assert not ok
    assert existing["workspace"] == "ws1"

  def test_write_lock_still_takes_over(
    self, tmp_db, locks_dir
  ):
    """The legacy helper keeps its old semantics."""
    target_ops.claim_lock("deb-02", "ws1", db_path=tmp_db)
    target_ops.write_lock("deb-02", "ws2", db_path=tmp_db)
    assert target_ops.read_lock(
      "deb-02", db_path=tmp_db
    )["workspace"] == "ws2"
