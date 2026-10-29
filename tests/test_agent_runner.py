"""Tests for agent_runner and agent_registry."""

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib.agent_runner import (
  _CLAUDE_CODE_PRESET,
  AgentInfo,
  AgentRunner,
  AgentState,
  _is_transient,
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
    from claude_agent_sdk import ResultMessage
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
    from claude_agent_sdk import TextBlock, AssistantMessage

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
    from claude_agent_sdk import (
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
    from claude_agent_sdk import ProcessError

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
    from claude_agent_sdk import ProcessError

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


class TestSdkParsesItsOwnEvents(unittest.TestCase):
  """Why the message-parser monkey-patch could be deleted.

  takt used to patch claude_code_sdk's private parser so an
  unknown event type did not raise. The event it existed for
  was rate_limit_event, which the CLI emits on every single
  run -- measured: exactly one per run. claude-agent-sdk
  parses it natively, so the patch on SDK internals is gone.

  This test pins the reason. If the SDK is ever downgraded to
  one that cannot parse it, this fails and says why, instead
  of agent runs dying on an unparseable event.
  """

  def test_rate_limit_event_parses_natively(self):
    """The event that forced the patch is now understood."""
    from claude_agent_sdk._internal.message_parser import (
      parse_message,
    )
    msg = parse_message({
      "type": "rate_limit_event",
      "uuid": "u1",
      "session_id": "s1",
      "rate_limit_info": {
        "status": "allowed",
        "rateLimitType": "five_hour",
      },
    })
    self.assertIsNotNone(msg)

  def test_no_parser_patch_is_installed(self):
    """lib.agent_runner must not touch SDK internals.

    Reaching into _internal pinned takt to a private API and
    is what made the old SDK fragile.
    """
    from claude_agent_sdk._internal import message_parser
    self.assertEqual(
      message_parser.parse_message.__module__,
      "claude_agent_sdk._internal.message_parser",
      "something replaced the SDK's parser",
    )


class TestSystemPromptIsPreserved(unittest.TestCase):
  """The silent trap in moving to claude-agent-sdk.

  The old SDK omitted --system-prompt when it was unset, so
  the CLI applied its own Claude Code system prompt. The new
  SDK sends `--system-prompt ""` for None, which starts the
  agent with NO system prompt at all. AgentRunner never set
  the option, so porting without the preset would have
  silently stripped Claude Code's behaviour from every
  pipeline agent -- and nothing would have failed loudly.
  """

  def test_runner_requests_the_claude_code_preset(self):
    """The option must be set, not left to default."""
    self.assertEqual(
      _CLAUDE_CODE_PRESET,
      {"type": "preset", "preset": "claude_code"},
    )
    captured = {}

    async def fake_query(*, prompt, options=None, **kw):
      captured["options"] = options
      if False:
        yield None

    info = AgentInfo(
      agent_id="a", workspace="w", role="test", cwd="/tmp",
    )
    with mock.patch(
      "lib.agent_runner.query", fake_query
    ):
      asyncio.run(AgentRunner(info).run("hi", None))
    self.assertEqual(
      captured["options"].system_prompt,
      {"type": "preset", "preset": "claude_code"},
    )

  def test_preset_yields_no_system_prompt_flag(self):
    """The preset means "the CLI default", i.e. no flag.

    Asserted against the SDK's real argv builder: the preset
    must add no --system-prompt, while leaving the option
    unset must add an empty one. If that ever inverts, the
    preset stops meaning what this code relies on.
    """
    from claude_agent_sdk import ClaudeAgentOptions
    from claude_agent_sdk._internal.transport.subprocess_cli \
      import SubprocessCLITransport

    def argv(**kw):
      t = SubprocessCLITransport(
        prompt="x", options=ClaudeAgentOptions(**kw)
      )
      t._cli_path = "/usr/bin/claude"
      return t._build_command()

    with_preset = argv(system_prompt=_CLAUDE_CODE_PRESET)
    self.assertNotIn("--system-prompt", with_preset)
    # The default this code must never fall back to.
    self.assertIn("--system-prompt", argv())
    unset = argv()
    self.assertEqual(
      unset[unset.index("--system-prompt") + 1], "",
      "unset should send an empty prompt -- the trap",
    )


class TestIsTransient(unittest.TestCase):
  """Tests for _is_transient helper."""

  def test_rate_limit(self):
    from claude_agent_sdk import ProcessError
    e = ProcessError("x", stderr="rate_limit hit")
    self.assertTrue(_is_transient(e))

  def test_overloaded(self):
    from claude_agent_sdk import ProcessError
    e = ProcessError("x", stderr="server overloaded")
    self.assertTrue(_is_transient(e))

  def test_529(self):
    from claude_agent_sdk import ProcessError
    e = ProcessError("x", stderr="HTTP 529 error")
    self.assertTrue(_is_transient(e))

  def test_non_transient(self):
    from claude_agent_sdk import ProcessError
    e = ProcessError("x", stderr="auth failed")
    self.assertFalse(_is_transient(e))

  def test_no_stderr(self):
    from claude_agent_sdk import ProcessError
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


class TestClaudeCliSelection(unittest.TestCase):
  """Which `claude` binary agent steps run.

  claude-agent-sdk bundles its own CLI and prefers it over
  PATH. takt must not accept that default: a worker invokes
  PATH's `claude` directly and declares the agent_steps
  capability from shutil.which("claude"), so a scheduler
  silently using a different bundled build would run the
  same pipeline step on two different Claude Code versions
  depending on where it landed, and the operator's own CLI
  upgrades would stop reaching pipeline runs.
  """

  def test_path_cli_is_preferred(self):
    from lib import agent_runner
    with mock.patch.dict(
      "os.environ", {}, clear=False
    ) as _env:
      os.environ.pop("TAKT_CLAUDE_CLI", None)
      with mock.patch(
        "lib.agent_runner.shutil.which",
        return_value="/usr/bin/claude",
      ):
        self.assertEqual(
          agent_runner.claude_cli_path(), "/usr/bin/claude"
        )

  def test_env_override_wins(self):
    from lib import agent_runner
    with mock.patch.dict(
      "os.environ", {"TAKT_CLAUDE_CLI": "/opt/c"},
    ):
      with mock.patch(
        "lib.agent_runner.shutil.which",
        return_value="/usr/bin/claude",
      ):
        self.assertEqual(
          agent_runner.claude_cli_path(), "/opt/c"
        )

  def test_falls_back_to_the_bundled_copy(self):
    """None lets the SDK use its own, for a bare machine."""
    from lib import agent_runner
    with mock.patch.dict("os.environ", {}, clear=False):
      os.environ.pop("TAKT_CLAUDE_CLI", None)
      with mock.patch(
        "lib.agent_runner.shutil.which", return_value=None
      ):
        self.assertIsNone(agent_runner.claude_cli_path())

  def test_runner_passes_it_to_the_sdk(self):
    """Set explicitly, so the SDK cannot pick for us."""
    captured = {}

    async def fake_query(*, prompt, options=None, **kw):
      captured["options"] = options
      if False:
        yield None

    info = AgentInfo(
      agent_id="a", workspace="w", role="test", cwd="/tmp",
    )
    with mock.patch(
      "lib.agent_runner.query", fake_query
    ), mock.patch(
      "lib.agent_runner.claude_cli_path",
      return_value="/usr/bin/claude",
    ):
      asyncio.run(AgentRunner(info).run("hi", None))
    self.assertEqual(
      captured["options"].cli_path, "/usr/bin/claude"
    )

  def test_the_worker_resolves_the_same_binary(self):
    """Scheduler and worker must agree on the CLI.

    lib/worker.py builds its argv from PATH. If these two
    ever disagree, a step's engine depends on where it ran.
    """
    import shutil
    from lib import agent_runner, worker
    with mock.patch.dict("os.environ", {}, clear=False):
      os.environ.pop("TAKT_CLAUDE_CLI", None)
      sched = agent_runner.claude_cli_path()
    argv = worker.claude_cli_argv("prompt", model="haiku")
    # The worker execs a bare name and lets PATH resolve it,
    # so resolve it the same way before comparing.
    worker_cli = shutil.which(argv[0]) or argv[0]
    self.assertIsNotNone(sched, "no claude on PATH to compare")
    self.assertEqual(
      os.path.realpath(worker_cli),
      os.path.realpath(sched),
      "scheduler and worker resolve different CLIs",
    )
