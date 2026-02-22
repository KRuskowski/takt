"""Tests for lib/service.py — takt background service."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import zmq
import zmq.asyncio

from lib import db
from lib.service import TaktService


class TestServiceCommands(unittest.TestCase):
  """Tests for service command handling via inproc://."""

  def setUp(self):
    self._tmpdir = tempfile.TemporaryDirectory()
    self._base = Path(self._tmpdir.name)
    self._db_path = str(self._base / "test.db")
    db.migrate(db_path=self._db_path)
    self._ctx = zmq.asyncio.Context()
    self._cmd_addr = "inproc://test-cmd"
    self._pub_addr = "inproc://test-pub"

  def tearDown(self):
    self._ctx.term()
    self._tmpdir.cleanup()

  def _make_service(self, **kwargs):
    """Create a service with test ZMQ context and DB."""
    return TaktService(
      cmd_addr=self._cmd_addr,
      pub_addr=self._pub_addr,
      zmq_ctx=self._ctx,
      db_path=self._db_path,
      **kwargs,
    )

  async def _send_cmd(self, dealer, cmd_dict):
    """Send a command and receive the reply."""
    await dealer.send_multipart([
      b"", json.dumps(cmd_dict).encode()
    ])
    frames = await asyncio.wait_for(
      dealer.recv_multipart(), timeout=5
    )
    return json.loads(frames[1])

  def test_ping(self):
    """Ping command returns pong."""
    async def run():
      service = self._make_service()
      service._router = self._ctx.socket(zmq.ROUTER)
      service._router.bind(self._cmd_addr)
      service._pub = self._ctx.socket(zmq.PUB)
      service._pub.bind(self._pub_addr)
      service._running = True
      dealer = self._ctx.socket(zmq.DEALER)
      dealer.connect(self._cmd_addr)
      cmd_task = asyncio.create_task(
        service._cmd_loop()
      )
      try:
        reply = await self._send_cmd(
          dealer, {"cmd": "ping"}
        )
        self.assertEqual(reply["status"], "ok")
        self.assertTrue(reply["data"]["pong"])
      finally:
        service._running = False
        await dealer.send_multipart([
          b"", b'{"cmd":"ping"}'
        ])
        cmd_task.cancel()
        try:
          await cmd_task
        except asyncio.CancelledError:
          pass
        dealer.close()
        service._router.close(linger=0)
        service._pub.close(linger=0)

    asyncio.run(run())

  def test_list_runs_empty(self):
    """list_runs with no runs returns empty list."""
    async def run():
      service = self._make_service()
      result = await service._handle_list_runs({})
      self.assertEqual(result["runs"], [])

    asyncio.run(run())

  def test_list_runs_with_data(self):
    """list_runs returns runs from SQLite."""
    db.define_pipeline("ws1", [
      {"name": "test", "step_type": "agent"},
    ], db_path=self._db_path)
    db.create_run(
      "ws1", "manual", ["repo-a"], {},
      db_path=self._db_path,
    )

    async def run():
      service = self._make_service()
      result = await service._handle_list_runs({
        "workspace": "ws1",
      })
      self.assertEqual(len(result["runs"]), 1)
      self.assertEqual(
        result["runs"][0]["workspace"], "ws1"
      )

    asyncio.run(run())

  def test_get_run_detail(self):
    """get_run_detail returns run and steps."""
    db.define_pipeline("ws1", [
      {"name": "test", "step_type": "agent"},
      {"name": "push", "step_type": "script"},
    ], db_path=self._db_path)
    run_id = db.create_run(
      "ws1", "manual", [], {}, db_path=self._db_path,
    )

    async def run():
      service = self._make_service()
      result = await service._handle_get_run_detail({
        "run_id": run_id,
      })
      self.assertEqual(result["run"]["id"], run_id)
      self.assertEqual(len(result["steps"]), 2)

    asyncio.run(run())

  def test_get_step_detail(self):
    """get_step_detail returns step and events."""
    db.define_pipeline("ws1", [
      {"name": "test", "step_type": "agent"},
    ], db_path=self._db_path)
    run_id = db.create_run(
      "ws1", "manual", [], {}, db_path=self._db_path,
    )
    steps = db.get_run_steps(
      run_id, db_path=self._db_path,
    )
    step_id = steps[0]["id"]
    db.advance_step(
      step_id, "queued", db_path=self._db_path,
    )

    async def run():
      service = self._make_service()
      result = await service._handle_get_step_detail({
        "step_id": step_id,
      })
      self.assertEqual(result["step"]["id"], step_id)
      self.assertEqual(len(result["events"]), 1)

    asyncio.run(run())

  def test_replay_output(self):
    """replay_output returns stored output lines."""
    db.define_pipeline("ws1", [
      {"name": "test", "step_type": "agent"},
    ], db_path=self._db_path)
    run_id = db.create_run(
      "ws1", "manual", [], {}, db_path=self._db_path,
    )
    steps = db.get_run_steps(
      run_id, db_path=self._db_path,
    )
    step_id = steps[0]["id"]
    db.record_output(step_id, [
      {"line_no": 0, "kind": "text", "content": "hi"},
    ], db_path=self._db_path)

    async def run():
      service = self._make_service()
      result = await service._handle_replay_output({
        "step_id": step_id,
      })
      self.assertEqual(len(result["lines"]), 1)
      self.assertEqual(
        result["lines"][0]["content"], "hi"
      )

    asyncio.run(run())

  def test_unknown_command(self):
    """Unknown command returns error reply."""
    async def run():
      service = self._make_service()
      service._router = self._ctx.socket(zmq.ROUTER)
      service._router.bind(self._cmd_addr)
      service._pub = self._ctx.socket(zmq.PUB)
      service._pub.bind(self._pub_addr)
      service._running = True
      dealer = self._ctx.socket(zmq.DEALER)
      dealer.connect(self._cmd_addr)
      cmd_task = asyncio.create_task(
        service._cmd_loop()
      )
      try:
        reply = await self._send_cmd(
          dealer, {"cmd": "nonexistent"}
        )
        self.assertEqual(reply["status"], "error")
        self.assertIn("unknown", reply["message"])
      finally:
        service._running = False
        await dealer.send_multipart([
          b"", b'{"cmd":"ping"}'
        ])
        cmd_task.cancel()
        try:
          await cmd_task
        except asyncio.CancelledError:
          pass
        dealer.close()
        service._router.close(linger=0)
        service._pub.close(linger=0)

    asyncio.run(run())


class TestServiceOperatorActions(unittest.TestCase):
  """Tests for operator control commands."""

  def setUp(self):
    self._tmpdir = tempfile.TemporaryDirectory()
    self._base = Path(self._tmpdir.name)
    self._db_path = str(self._base / "test.db")
    db.migrate(db_path=self._db_path)
    self._ctx = zmq.asyncio.Context()

  def tearDown(self):
    self._ctx.term()
    self._tmpdir.cleanup()

  def _make_service(self):
    return TaktService(
      cmd_addr="inproc://test-cmd2",
      pub_addr="inproc://test-pub2",
      zmq_ctx=self._ctx,
      db_path=self._db_path,
    )

  def test_skip_step(self):
    """skip_step transitions step to skipped."""
    db.define_pipeline("ws1", [
      {"name": "test", "step_type": "agent"},
    ], db_path=self._db_path)
    run_id = db.create_run(
      "ws1", "manual", [], {}, db_path=self._db_path,
    )
    steps = db.get_run_steps(
      run_id, db_path=self._db_path,
    )
    step_id = steps[0]["id"]
    db.advance_step(
      step_id, "queued", db_path=self._db_path,
    )

    async def run():
      service = self._make_service()
      await service._handle_skip_step({
        "step_id": step_id,
      })

    asyncio.run(run())
    step = db.get_step(step_id, db_path=self._db_path)
    self.assertEqual(step["status"], "skipped")

  def test_retry_step(self):
    """retry_step transitions failed step to queued."""
    db.define_pipeline("ws1", [
      {"name": "test", "step_type": "agent"},
    ], db_path=self._db_path)
    run_id = db.create_run(
      "ws1", "manual", [], {}, db_path=self._db_path,
    )
    steps = db.get_run_steps(
      run_id, db_path=self._db_path,
    )
    step_id = steps[0]["id"]
    db.advance_step(
      step_id, "queued", db_path=self._db_path,
    )
    db.advance_step(
      step_id, "running", db_path=self._db_path,
    )
    db.advance_step(
      step_id, "failed", db_path=self._db_path,
    )

    async def run():
      service = self._make_service()
      await service._handle_retry_step({
        "step_id": step_id,
      })

    asyncio.run(run())
    step = db.get_step(step_id, db_path=self._db_path)
    self.assertEqual(step["status"], "queued")

  def test_cancel_run_in_db(self):
    """cancel_run marks queued run as cancelled."""
    db.define_pipeline("ws1", [
      {"name": "test", "step_type": "agent"},
    ], db_path=self._db_path)
    run_id = db.create_run(
      "ws1", "manual", [], {}, db_path=self._db_path,
    )

    async def run():
      service = self._make_service()
      await service._handle_cancel_run({
        "run_id": run_id,
      })

    asyncio.run(run())
    run_row = db.get_run(run_id, db_path=self._db_path)
    self.assertEqual(run_row["status"], "cancelled")

  def test_trigger_run(self):
    """trigger_run creates a run and returns run_id."""
    db.define_pipeline("ws1", [
      {"name": "test", "step_type": "agent"},
    ], db_path=self._db_path)

    async def run():
      service = self._make_service()
      service._pub = self._ctx.socket(zmq.PUB)
      service._pub.bind("inproc://test-pub2")
      with mock.patch(
        "lib.service.list_workspaces",
        return_value=[{
          "name": "ws1", "repos": ["repo-a"],
        }],
      ), mock.patch.object(
        service, "_snapshot_workspace_refs",
        return_value={"repo-a": "abc"},
      ), mock.patch.object(
        service, "_launch_run",
      ) as mock_launch:
        result = await service._handle_trigger_run({
          "workspace": "ws1",
        })
      self.assertIn("run_id", result)
      mock_launch.assert_called_once()
      service._pub.close(linger=0)

    asyncio.run(run())


class TestServicePub(unittest.TestCase):
  """Tests for PUB/SUB message delivery."""

  def setUp(self):
    self._tmpdir = tempfile.TemporaryDirectory()
    self._base = Path(self._tmpdir.name)
    self._db_path = str(self._base / "test.db")
    db.migrate(db_path=self._db_path)
    self._ctx = zmq.asyncio.Context()

  def tearDown(self):
    self._ctx.term()
    self._tmpdir.cleanup()

  def test_publish_pipeline_event(self):
    """pipeline.event is published and receivable."""
    async def run():
      service = TaktService(
        cmd_addr="inproc://test-cmd3",
        pub_addr="inproc://test-pub3",
        zmq_ctx=self._ctx,
        db_path=self._db_path,
      )
      service._pub = self._ctx.socket(zmq.PUB)
      service._pub.bind("inproc://test-pub3")
      sub = self._ctx.socket(zmq.SUB)
      sub.connect("inproc://test-pub3")
      sub.subscribe(b"pipeline.event")
      await asyncio.sleep(0.1)
      await service._publish("pipeline.event", {
        "time": "12:00:00",
        "workspace": "ws1",
        "event": "run_created",
      })
      frames = await asyncio.wait_for(
        sub.recv_multipart(), timeout=2
      )
      data = json.loads(frames[1])
      self.assertEqual(data["workspace"], "ws1")
      self.assertEqual(data["event"], "run_created")
      sub.close()
      service._pub.close(linger=0)

    asyncio.run(run())

  def test_step_update_published(self):
    """step.update is published via _on_step_update."""
    async def run():
      service = TaktService(
        cmd_addr="inproc://test-cmd4",
        pub_addr="inproc://test-pub4",
        zmq_ctx=self._ctx,
        db_path=self._db_path,
      )
      service._pub = self._ctx.socket(zmq.PUB)
      service._pub.bind("inproc://test-pub4")
      sub = self._ctx.socket(zmq.SUB)
      sub.connect("inproc://test-pub4")
      sub.subscribe(b"step.update")
      await asyncio.sleep(0.1)
      service._on_step_update(42, "running")
      frames = await asyncio.wait_for(
        sub.recv_multipart(), timeout=2
      )
      data = json.loads(frames[1])
      self.assertEqual(data["step_id"], 42)
      self.assertEqual(data["status"], "running")
      sub.close()
      service._pub.close(linger=0)

    asyncio.run(run())


class TestServicePoll(unittest.TestCase):
  """Tests for poll cycle."""

  def setUp(self):
    self._tmpdir = tempfile.TemporaryDirectory()
    self._base = Path(self._tmpdir.name)
    self._db_path = str(self._base / "test.db")
    db.migrate(db_path=self._db_path)
    self._ctx = zmq.asyncio.Context()

  def tearDown(self):
    self._ctx.term()
    self._tmpdir.cleanup()

  def test_poll_creates_run_on_branch_change(self):
    """Poll detects branch change and creates a run."""
    db.define_pipeline("feat", [
      {"name": "test", "step_type": "agent"},
    ], db_path=self._db_path)
    # Seed initial refs.
    db.save_refs(
      {"repo-a:feat": "old-hash"},
      db_path=self._db_path,
    )

    service = TaktService(
      cmd_addr="inproc://test-cmd5",
      pub_addr="inproc://test-pub5",
      zmq_ctx=self._ctx,
      db_path=self._db_path,
    )
    with mock.patch.object(
      service, "_fetch_all_root_repos",
    ), mock.patch(
      "lib.service.snapshot_all_refs",
      return_value={"repo-a:feat": "new-hash"},
    ), mock.patch(
      "lib.service.list_workspaces",
      return_value=[{
        "name": "feat",
        "repos": ["repo-a"],
      }],
    ):
      events = service._poll_sync()

    self.assertEqual(len(events), 1)
    self.assertEqual(events[0]["event"], "run_created")
    runs = db.list_runs("feat", db_path=self._db_path)
    self.assertEqual(len(runs), 1)

  def test_poll_no_change(self):
    """Poll with no changes creates no runs."""
    refs = {"repo-a:main": "same-hash"}
    db.save_refs(refs, db_path=self._db_path)

    service = TaktService(
      cmd_addr="inproc://test-cmd6",
      pub_addr="inproc://test-pub6",
      zmq_ctx=self._ctx,
      db_path=self._db_path,
    )
    with mock.patch.object(
      service, "_fetch_all_root_repos",
    ), mock.patch(
      "lib.service.snapshot_all_refs",
      return_value=refs,
    ):
      events = service._poll_sync()

    self.assertEqual(events, [])


if __name__ == "__main__":
  unittest.main()
