"""Tests for agent_runner and agent_registry."""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib.agent_runner import (
  AgentInfo,
  AgentRunner,
  AgentState,
  _is_transient,
  _patched_parse,
)
from lib import agent_registry


class TestAgentInfo(unittest.TestCase):
  """Tests for AgentInfo dataclass."""

  def test_defaults(self):
    """AgentInfo has sensible defaults."""
    info = AgentInfo(
      agent_id="ws/test",
      workspace="ws",
      role="test",
      cwd="/tmp",
    )
    self.assertEqual(info.agent_id, "ws/test")
    self.assertEqual(info.model, "sonnet")
    self.assertEqual(info.state, AgentState.PENDING)
    self.assertIsNone(info.session_id)
    self.assertEqual(info.total_cost_usd, 0.0)
    self.assertEqual(info.num_turns, 0)

  def test_custom_model(self):
    """AgentInfo accepts custom model."""
    info = AgentInfo(
      agent_id="ws/review",
      workspace="ws",
      role="review",
      cwd="/tmp",
      model="opus",
    )
    self.assertEqual(info.model, "opus")

  def test_error_field_default(self):
    """AgentInfo.error defaults to None."""
    info = AgentInfo(
      agent_id="ws/test",
      workspace="ws",
      role="test",
      cwd="/tmp",
    )
    self.assertIsNone(info.error)


class TestAgentState(unittest.TestCase):
  """Tests for AgentState enum."""

  def test_values(self):
    """All expected states exist."""
    self.assertEqual(
      AgentState.PENDING.value, "pending"
    )
    self.assertEqual(
      AgentState.RUNNING.value, "running"
    )
    self.assertEqual(
      AgentState.COMPLETED.value, "completed"
    )
    self.assertEqual(
      AgentState.FAILED.value, "failed"
    )
    self.assertEqual(
      AgentState.CANCELLED.value, "cancelled"
    )


class TestAgentRunner(unittest.TestCase):
  """Tests for AgentRunner."""

  def _make_runner(self):
    info = AgentInfo(
      agent_id="ws/test",
      workspace="ws",
      role="test",
      cwd="/tmp",
    )
    return AgentRunner(info)

  def test_initial_state(self):
    """Runner starts in PENDING state."""
    runner = self._make_runner()
    self.assertEqual(
      runner.info.state, AgentState.PENDING
    )

  def test_cancel(self):
    """Cancel sets the cancelled flag."""
    runner = self._make_runner()
    self.assertFalse(runner._cancelled)
    runner.cancel()
    self.assertTrue(runner._cancelled)

  @mock.patch("lib.agent_runner.query")
  def test_run_success(self, mock_query):
    """Successful run transitions to COMPLETED."""
    from claude_code_sdk import ResultMessage
    result_msg = ResultMessage(
      subtype="result",
      duration_ms=5000,
      duration_api_ms=4000,
      is_error=False,
      num_turns=3,
      session_id="sess-123",
      total_cost_usd=0.05,
      usage=None,
      result="done",
    )

    async def fake_query(**kwargs):
      yield result_msg

    mock_query.return_value = fake_query()
    runner = self._make_runner()
    messages = []

    asyncio.run(
      runner.run("test prompt", messages.append)
    )

    self.assertEqual(
      runner.info.state, AgentState.COMPLETED
    )
    self.assertEqual(runner.info.session_id, "sess-123")
    self.assertEqual(runner.info.total_cost_usd, 0.05)
    self.assertEqual(runner.info.num_turns, 3)
    self.assertEqual(len(messages), 1)

  @mock.patch("lib.agent_runner.query")
  def test_run_cancelled(self, mock_query):
    """Cancelling during run transitions to CANCELLED."""
    from claude_code_sdk import TextBlock, AssistantMessage

    async def fake_query(**kwargs):
      yield AssistantMessage(
        content=[TextBlock(text="hello")],
        model="sonnet",
      )
      # Simulate slow response.
      yield AssistantMessage(
        content=[TextBlock(text="world")],
        model="sonnet",
      )

    mock_query.return_value = fake_query()
    runner = self._make_runner()
    messages = []

    def on_msg(msg):
      messages.append(msg)
      runner.cancel()

    asyncio.run(runner.run("test", on_msg))
    self.assertEqual(
      runner.info.state, AgentState.CANCELLED
    )

  @mock.patch("lib.agent_runner.query")
  def test_run_failure(self, mock_query):
    """Exception during run transitions to FAILED."""
    async def fake_query(**kwargs):
      raise RuntimeError("SDK error")
      yield  # pragma: no cover

    mock_query.return_value = fake_query()
    runner = self._make_runner()

    with self.assertRaises(RuntimeError):
      asyncio.run(runner.run("test", lambda m: None))

    self.assertEqual(
      runner.info.state, AgentState.FAILED
    )

  @mock.patch("lib.agent_runner.query")
  def test_agent_info_error_field(self, mock_query):
    """Error field is set on failure."""
    async def fake_query(**kwargs):
      raise RuntimeError("boom")
      yield  # pragma: no cover

    mock_query.return_value = fake_query()
    runner = self._make_runner()

    with self.assertRaises(RuntimeError):
      asyncio.run(runner.run("test", lambda m: None))

    self.assertEqual(
      runner.info.state, AgentState.FAILED
    )
    self.assertIn("boom", runner.info.error)

  @mock.patch("lib.agent_runner.asyncio.sleep",
              new_callable=mock.AsyncMock)
  @mock.patch("lib.agent_runner.query")
  def test_run_transient_retry_succeeds(
    self, mock_query, mock_sleep
  ):
    """Transient ProcessError retries and succeeds."""
    from claude_code_sdk import (
      ProcessError,
      ResultMessage,
    )
    call_count = 0

    async def fake_query(**kwargs):
      nonlocal call_count
      call_count += 1
      if call_count == 1:
        raise ProcessError(
          "failed",
          exit_code=1,
          stderr="rate_limit exceeded",
        )
      yield ResultMessage(
        subtype="result",
        duration_ms=1000,
        duration_api_ms=900,
        is_error=False,
        num_turns=1,
        session_id="sess-retry",
        total_cost_usd=0.01,
        usage=None,
        result="ok",
      )

    mock_query.side_effect = (
      lambda **kw: fake_query(**kw)
    )
    runner = self._make_runner()
    messages = []

    asyncio.run(
      runner.run("test", messages.append)
    )

    self.assertEqual(
      runner.info.state, AgentState.COMPLETED
    )
    self.assertEqual(call_count, 2)
    mock_sleep.assert_called_once_with(30)
    # Should have a SystemMessage for retry + ResultMessage.
    sys_msgs = [
      m for m in messages
      if hasattr(m, 'subtype') and m.subtype == "retry"
    ]
    self.assertEqual(len(sys_msgs), 1)

  @mock.patch("lib.agent_runner.asyncio.sleep",
              new_callable=mock.AsyncMock)
  @mock.patch("lib.agent_runner.query")
  def test_run_transient_retry_exhausted(
    self, mock_query, mock_sleep
  ):
    """All retry attempts fail, raises ProcessError."""
    from claude_code_sdk import ProcessError

    async def fake_query(**kwargs):
      raise ProcessError(
        "overloaded",
        exit_code=1,
        stderr="server overloaded",
      )
      yield  # pragma: no cover

    mock_query.side_effect = (
      lambda **kw: fake_query(**kw)
    )
    runner = self._make_runner()

    with self.assertRaises(ProcessError):
      asyncio.run(
        runner.run("test", lambda m: None)
      )

    self.assertEqual(
      runner.info.state, AgentState.FAILED
    )
    # 2 retries = 2 sleep calls.
    self.assertEqual(mock_sleep.call_count, 2)

  @mock.patch("lib.agent_runner.query")
  def test_run_non_transient_no_retry(self, mock_query):
    """Non-transient ProcessError does not retry."""
    from claude_code_sdk import ProcessError

    async def fake_query(**kwargs):
      raise ProcessError(
        "auth failed",
        exit_code=1,
        stderr="invalid API key",
      )
      yield  # pragma: no cover

    mock_query.return_value = fake_query()
    runner = self._make_runner()

    with self.assertRaises(ProcessError):
      asyncio.run(
        runner.run("test", lambda m: None)
      )

    self.assertEqual(
      runner.info.state, AgentState.FAILED
    )
    self.assertIn("invalid API key", runner.info.error)


class TestPatchedParse(unittest.TestCase):
  """Tests for the monkey-patched message parser."""

  def test_catches_message_parse_error(self):
    """MessageParseError is caught and returns StreamEvent."""
    from claude_code_sdk._internal.message_parser import (
      MessageParseError,
    )
    with mock.patch(
      "lib.agent_runner._original_parse",
      side_effect=MessageParseError("bad", data={}),
    ):
      result = _patched_parse({"type": "unknown"})
    self.assertIsNotNone(result)

  def test_type_error_propagates(self):
    """TypeError is not caught by the patched parser."""
    with mock.patch(
      "lib.agent_runner._original_parse",
      side_effect=TypeError("wrong type"),
    ):
      with self.assertRaises(TypeError):
        _patched_parse({"type": "bad"})


class TestIsTransient(unittest.TestCase):
  """Tests for _is_transient helper."""

  def test_rate_limit(self):
    from claude_code_sdk import ProcessError
    e = ProcessError("x", stderr="rate_limit hit")
    self.assertTrue(_is_transient(e))

  def test_overloaded(self):
    from claude_code_sdk import ProcessError
    e = ProcessError("x", stderr="server overloaded")
    self.assertTrue(_is_transient(e))

  def test_529(self):
    from claude_code_sdk import ProcessError
    e = ProcessError("x", stderr="HTTP 529 error")
    self.assertTrue(_is_transient(e))

  def test_non_transient(self):
    from claude_code_sdk import ProcessError
    e = ProcessError("x", stderr="auth failed")
    self.assertFalse(_is_transient(e))

  def test_no_stderr(self):
    from claude_code_sdk import ProcessError
    e = ProcessError("x", stderr=None)
    self.assertFalse(_is_transient(e))


class TestAgentRegistry(unittest.TestCase):
  """Tests for agent_registry module."""

  def setUp(self):
    agent_registry._agents.clear()

  def tearDown(self):
    agent_registry._agents.clear()

  def _make_runner(self, agent_id="ws/test",
                   state=AgentState.PENDING):
    info = AgentInfo(
      agent_id=agent_id,
      workspace="ws",
      role="test",
      cwd="/tmp",
    )
    info.state = state
    return AgentRunner(info)

  def test_register_and_get(self):
    """Registered runner is retrievable."""
    runner = self._make_runner()
    agent_registry.register(runner)
    self.assertIs(
      agent_registry.get("ws/test"), runner
    )

  def test_unregister(self):
    """Unregistered runner is no longer retrievable."""
    runner = self._make_runner()
    agent_registry.register(runner)
    agent_registry.unregister("ws/test")
    self.assertIsNone(agent_registry.get("ws/test"))

  def test_is_running(self):
    """is_running returns True only for RUNNING agents."""
    runner = self._make_runner(
      state=AgentState.RUNNING
    )
    agent_registry.register(runner)
    self.assertTrue(
      agent_registry.is_running("ws/test")
    )

  def test_is_not_running(self):
    """is_running returns False for non-RUNNING agents."""
    runner = self._make_runner(
      state=AgentState.COMPLETED
    )
    agent_registry.register(runner)
    self.assertFalse(
      agent_registry.is_running("ws/test")
    )

  def test_is_running_missing(self):
    """is_running returns False for unknown agent."""
    self.assertFalse(
      agent_registry.is_running("nope")
    )

  def test_list_active(self):
    """list_active returns only RUNNING runners."""
    r1 = self._make_runner(
      "a/1", AgentState.RUNNING
    )
    r2 = self._make_runner(
      "a/2", AgentState.COMPLETED
    )
    agent_registry.register(r1)
    agent_registry.register(r2)
    active = agent_registry.list_active()
    self.assertEqual(len(active), 1)
    self.assertIs(active[0], r1)

  def test_list_all(self):
    """list_all returns all registered runners."""
    r1 = self._make_runner("a/1")
    r2 = self._make_runner("a/2")
    agent_registry.register(r1)
    agent_registry.register(r2)
    self.assertEqual(
      len(agent_registry.list_all()), 2
    )

  def test_list_failed(self):
    """list_failed returns only FAILED runners."""
    r1 = self._make_runner(
      "a/1", AgentState.FAILED
    )
    r2 = self._make_runner(
      "a/2", AgentState.RUNNING
    )
    r3 = self._make_runner(
      "a/3", AgentState.FAILED
    )
    agent_registry.register(r1)
    agent_registry.register(r2)
    agent_registry.register(r3)
    failed = agent_registry.list_failed()
    self.assertEqual(len(failed), 2)

  def test_clear_finished(self):
    """clear_finished removes non-active runners."""
    r1 = self._make_runner(
      "a/1", AgentState.COMPLETED
    )
    r2 = self._make_runner(
      "a/2", AgentState.RUNNING
    )
    r3 = self._make_runner(
      "a/3", AgentState.FAILED
    )
    r4 = self._make_runner(
      "a/4", AgentState.CANCELLED
    )
    agent_registry.register(r1)
    agent_registry.register(r2)
    agent_registry.register(r3)
    agent_registry.register(r4)
    removed = agent_registry.clear_finished()
    self.assertEqual(removed, 3)
    self.assertEqual(
      len(agent_registry.list_all()), 1
    )
    self.assertIs(
      agent_registry.get("a/2"), r2
    )


if __name__ == "__main__":
  unittest.main()
