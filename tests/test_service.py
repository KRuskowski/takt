"""Tests for lib/service.py — takt background service."""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import zmq
import zmq.asyncio

from lib.agent_runner import AgentInfo, AgentState
from lib.agent_store import AgentStore
from lib.service import TaktService


class TestServiceCommands(unittest.TestCase):
  """Tests for service command handling via inproc://."""

  def setUp(self):
    self._tmpdir = tempfile.TemporaryDirectory()
    self._base = Path(self._tmpdir.name)
    self._store = AgentStore(
      base_dir=self._base / "agents"
    )
    self._ctx = zmq.asyncio.Context()
    self._cmd_addr = "inproc://test-cmd"
    self._pub_addr = "inproc://test-pub"

  def tearDown(self):
    self._ctx.term()
    self._tmpdir.cleanup()

  def _make_service(self, **kwargs):
    return TaktService(
      cmd_addr=self._cmd_addr,
      pub_addr=self._pub_addr,
      zmq_ctx=self._ctx,
      store=self._store,
      **kwargs,
    )

  async def _send_cmd(self, dealer, cmd_dict):
    """Send a command and receive the reply.

    Args:
      dealer: ZMQ DEALER socket.
      cmd_dict: Command dict to send.

    Returns:
      Reply dict.
    """
    await dealer.send_multipart([
      b"", json.dumps(cmd_dict).encode()
    ])
    frames = await asyncio.wait_for(
      dealer.recv_multipart(), timeout=5
    )
    # DEALER receives [empty, payload].
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

  def test_list_agents_empty(self):
    """list_agents with no agents returns empty list."""
    async def run():
      service = self._make_service()
      result = await service._handle_list_agents({})
      self.assertEqual(result["agents"], [])

    asyncio.run(run())

  def test_list_agents_with_info(self):
    """list_agents returns stored agent infos."""
    async def run():
      service = self._make_service()
      info = AgentInfo(
        agent_id="ws/test",
        workspace="ws",
        role="test",
        cwd="/tmp",
        state=AgentState.COMPLETED,
      )
      service._agent_infos["ws/test"] = info
      result = await service._handle_list_agents({})
      agents = result["agents"]
      self.assertEqual(len(agents), 1)
      self.assertEqual(agents[0]["agent_id"], "ws/test")
      self.assertEqual(agents[0]["state"], "completed")

    asyncio.run(run())

  def test_replay_output(self):
    """replay_output returns stored lines."""
    lines = [
      {"line_no": 0, "kind": "text",
       "content": "hello", "meta": {}},
      {"line_no": 1, "kind": "text",
       "content": "world", "meta": {}},
    ]
    self._store.append_output("ws/test", lines)

    async def run():
      service = self._make_service()
      result = await service._handle_replay_output({
        "agent_id": "ws/test",
        "from_line": 0,
      })
      self.assertEqual(len(result["lines"]), 2)
      self.assertEqual(
        result["lines"][0]["content"], "hello"
      )

    asyncio.run(run())

  def test_replay_output_from_line(self):
    """replay_output respects from_line parameter."""
    lines = [
      {"line_no": 0, "kind": "text",
       "content": "a", "meta": {}},
      {"line_no": 1, "kind": "text",
       "content": "b", "meta": {}},
    ]
    self._store.append_output("ws/test", lines)

    async def run():
      service = self._make_service()
      result = await service._handle_replay_output({
        "agent_id": "ws/test",
        "from_line": 1,
      })
      self.assertEqual(len(result["lines"]), 1)
      self.assertEqual(
        result["lines"][0]["content"], "b"
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


class TestServiceAgentExecution(unittest.TestCase):
  """Tests for agent launch and execution."""

  def setUp(self):
    self._tmpdir = tempfile.TemporaryDirectory()
    self._base = Path(self._tmpdir.name)
    self._store = AgentStore(
      base_dir=self._base / "agents"
    )
    self._ctx = zmq.asyncio.Context()

  def tearDown(self):
    self._ctx.term()
    self._tmpdir.cleanup()

  def _make_service(self):
    return TaktService(
      cmd_addr="inproc://test-cmd2",
      pub_addr="inproc://test-pub2",
      zmq_ctx=self._ctx,
      store=self._store,
    )

  @mock.patch("lib.service.AgentRunner")
  def test_launch_agent(self, mock_runner_cls):
    """launch_agent creates info and starts task."""
    mock_runner = mock.AsyncMock()
    mock_runner.run = mock.AsyncMock()
    mock_runner.info = AgentInfo(
      agent_id="ws/test",
      workspace="ws",
      role="test",
      cwd="/tmp",
    )
    mock_runner_cls.return_value = mock_runner

    async def run():
      service = self._make_service()
      service._router = self._ctx.socket(zmq.ROUTER)
      service._router.bind("inproc://test-cmd2")
      service._pub = self._ctx.socket(zmq.PUB)
      service._pub.bind("inproc://test-pub2")
      service._running = True

      result = await service._handle_launch_agent({
        "agent_id": "ws/test",
        "prompt": "do stuff",
        "cwd": "/tmp",
        "workspace": "ws",
        "role": "test",
      })
      self.assertEqual(
        result["agent_id"], "ws/test"
      )
      self.assertIn("ws/test", service._agent_infos)
      # Let the task start.
      await asyncio.sleep(0.1)
      service._router.close(linger=0)
      service._pub.close(linger=0)

    asyncio.run(run())

  @mock.patch("lib.service.AgentRunner")
  def test_launch_duplicate_fails(self, mock_runner_cls):
    """Launching duplicate agent_id raises ValueError."""
    mock_runner = mock.AsyncMock()
    mock_runner.run = mock.AsyncMock(
      side_effect=asyncio.sleep(10)
    )
    mock_runner.info = AgentInfo(
      agent_id="ws/test",
      workspace="ws",
      role="test",
      cwd="/tmp",
    )
    mock_runner_cls.return_value = mock_runner

    async def run():
      service = self._make_service()
      service._router = self._ctx.socket(zmq.ROUTER)
      service._router.bind("inproc://test-cmd2")
      service._pub = self._ctx.socket(zmq.PUB)
      service._pub.bind("inproc://test-pub2")
      service._running = True

      await service._handle_launch_agent({
        "agent_id": "ws/test",
        "prompt": "do stuff",
        "cwd": "/tmp",
      })
      with self.assertRaises(ValueError):
        await service._handle_launch_agent({
          "agent_id": "ws/test",
          "prompt": "do stuff again",
          "cwd": "/tmp",
        })
      # Clean up.
      for task in service._agents.values():
        task.cancel()
      await asyncio.gather(
        *service._agents.values(),
        return_exceptions=True,
      )
      service._router.close(linger=0)
      service._pub.close(linger=0)

    asyncio.run(run())


class TestServiceOnAgentMessage(unittest.TestCase):
  """Tests for _on_agent_message serialization."""

  def setUp(self):
    self._tmpdir = tempfile.TemporaryDirectory()
    self._base = Path(self._tmpdir.name)
    self._store = AgentStore(
      base_dir=self._base / "agents"
    )
    self._ctx = zmq.asyncio.Context()

  def tearDown(self):
    self._ctx.term()
    self._tmpdir.cleanup()

  def test_on_agent_message_persists(self):
    """Messages are serialized and persisted."""
    from claude_code_sdk import TextBlock

    async def run():
      service = TaktService(
        cmd_addr="inproc://test-cmd3",
        pub_addr="inproc://test-pub3",
        zmq_ctx=self._ctx,
        store=self._store,
      )
      service._pub = self._ctx.socket(zmq.PUB)
      service._pub.bind("inproc://test-pub3")
      info = AgentInfo(
        agent_id="ws/test",
        workspace="ws",
        role="test",
        cwd="/tmp",
      )
      service._agent_infos["ws/test"] = info
      service._agent_line_counts["ws/test"] = 0

      msg = TextBlock(text="hello")
      service._on_agent_message("ws/test", msg)

      lines = self._store.load_output("ws/test")
      self.assertEqual(len(lines), 1)
      self.assertEqual(lines[0]["kind"], "text")
      self.assertEqual(lines[0]["content"], "hello")
      self.assertEqual(
        service._agent_line_counts["ws/test"], 1
      )
      service._pub.close(linger=0)

    asyncio.run(run())

  def test_line_numbers_increment(self):
    """Line numbers increment across messages."""
    from claude_code_sdk import (
      AssistantMessage,
      TextBlock,
      ToolUseBlock,
    )

    async def run():
      service = TaktService(
        cmd_addr="inproc://test-cmd4",
        pub_addr="inproc://test-pub4",
        zmq_ctx=self._ctx,
        store=self._store,
      )
      service._pub = self._ctx.socket(zmq.PUB)
      service._pub.bind("inproc://test-pub4")
      info = AgentInfo(
        agent_id="ws/test",
        workspace="ws",
        role="test",
        cwd="/tmp",
      )
      service._agent_infos["ws/test"] = info
      service._agent_line_counts["ws/test"] = 0

      msg1 = AssistantMessage(
        content=[
          TextBlock(text="a"),
          ToolUseBlock(
            id="tu_1", name="Bash",
            input={"command": "ls"},
          ),
        ],
        model="sonnet",
      )
      service._on_agent_message("ws/test", msg1)

      msg2 = TextBlock(text="b")
      service._on_agent_message("ws/test", msg2)

      lines = self._store.load_output("ws/test")
      self.assertEqual(len(lines), 3)
      self.assertEqual(lines[0]["line_no"], 0)
      self.assertEqual(lines[1]["line_no"], 1)
      self.assertEqual(lines[2]["line_no"], 2)
      service._pub.close(linger=0)

    asyncio.run(run())


class TestServicePub(unittest.TestCase):
  """Tests for PUB/SUB message delivery."""

  def setUp(self):
    self._tmpdir = tempfile.TemporaryDirectory()
    self._base = Path(self._tmpdir.name)
    self._store = AgentStore(
      base_dir=self._base / "agents"
    )
    self._ctx = zmq.asyncio.Context()

  def tearDown(self):
    self._ctx.term()
    self._tmpdir.cleanup()

  def test_publish_agent_update(self):
    """agent.update is published and receivable."""
    async def run():
      service = TaktService(
        cmd_addr="inproc://test-cmd5",
        pub_addr="inproc://test-pub5",
        zmq_ctx=self._ctx,
        store=self._store,
      )
      service._pub = self._ctx.socket(zmq.PUB)
      service._pub.bind("inproc://test-pub5")

      sub = self._ctx.socket(zmq.SUB)
      sub.connect("inproc://test-pub5")
      sub.subscribe(b"agent.update")
      # Small delay for subscription to propagate.
      await asyncio.sleep(0.1)

      info = AgentInfo(
        agent_id="ws/test",
        workspace="ws",
        role="test",
        cwd="/tmp",
        state=AgentState.COMPLETED,
      )
      await service._publish_agent_update(info)

      frames = await asyncio.wait_for(
        sub.recv_multipart(), timeout=2
      )
      self.assertEqual(frames[0], b"agent.update")
      data = json.loads(frames[1])
      self.assertEqual(data["agent_id"], "ws/test")
      self.assertEqual(data["state"], "completed")

      sub.close()
      service._pub.close(linger=0)

    asyncio.run(run())

  def test_publish_pipeline_event(self):
    """pipeline.event is published and receivable."""
    async def run():
      service = TaktService(
        cmd_addr="inproc://test-cmd6",
        pub_addr="inproc://test-pub6",
        zmq_ctx=self._ctx,
        store=self._store,
      )
      service._pub = self._ctx.socket(zmq.PUB)
      service._pub.bind("inproc://test-pub6")

      sub = self._ctx.socket(zmq.SUB)
      sub.connect("inproc://test-pub6")
      sub.subscribe(b"pipeline.event")
      await asyncio.sleep(0.1)

      await service._publish("pipeline.event", {
        "time": "12:00:00",
        "stage": "ws/test",
        "repos": "Repo1",
        "event": "triggered",
      })

      frames = await asyncio.wait_for(
        sub.recv_multipart(), timeout=2
      )
      data = json.loads(frames[1])
      self.assertEqual(data["stage"], "ws/test")
      self.assertEqual(data["event"], "triggered")

      sub.close()
      service._pub.close(linger=0)

    asyncio.run(run())


if __name__ == "__main__":
  unittest.main()
