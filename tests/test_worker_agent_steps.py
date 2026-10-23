"""Tests for agent steps executed via the Claude CLI.

The stream-json fixtures below are real lines captured from
`claude -p --output-format stream-json --verbose` on CLI
2.1.220, not invented shapes.
"""

import json
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib import worker

# Captured verbatim (trimmed) from a real CLI run.
INIT_LINE = json.dumps({
  "type": "system", "subtype": "init",
  "session_id": "e9f9fac6", "model": "claude-sonnet-5",
  "permissionMode": "bypassPermissions",
})
RATE_LIMIT_LINE = json.dumps({
  "type": "rate_limit_event",
  "rate_limit_info": {"status": "allowed_warning"},
})
ASSISTANT_LINE = json.dumps({
  "type": "assistant",
  "message": {
    "role": "assistant",
    "content": [{"type": "text", "text": "Running tests"}],
  },
})
TOOL_USE_LINE = json.dumps({
  "type": "assistant",
  "message": {
    "role": "assistant",
    "content": [
      {"type": "tool_use", "name": "Bash", "input": {}},
    ],
  },
})
RESULT_OK_LINE = json.dumps({
  "type": "result", "subtype": "success", "is_error": False,
  "num_turns": 3, "total_cost_usd": 0.076,
  "terminal_reason": "completed", "result": "done",
  "api_error_status": None,
})
RESULT_ERR_LINE = json.dumps({
  "type": "result", "subtype": "error_during_execution",
  "is_error": True, "num_turns": 1,
  "api_error_status": "overloaded", "result": "",
})


class TestArgv:
  """Building the CLI invocation."""

  def test_print_and_stream_json_with_verbose(self):
    """--verbose is required, not optional.

    The CLI refuses stream-json under --print without it,
    so omitting it fails every agent step at launch.
    """
    argv = worker.claude_cli_argv("do the thing")
    assert "-p" in argv
    assert "--verbose" in argv
    i = argv.index("--output-format")
    assert argv[i + 1] == "stream-json"

  def test_permission_mode_is_explicit(self):
    """Permissions are passed, never assumed.

    An interactive shell may alias `claude` to add a
    permission flag; a subprocess inherits no aliases, so
    the agent would otherwise block on a prompt forever.
    """
    argv = worker.claude_cli_argv("x")
    i = argv.index("--permission-mode")
    assert argv[i + 1] == "bypassPermissions"

  def test_prompt_is_passed_as_one_argv_entry(self):
    """A multi-line prompt is a single argument.

    Passed through argv rather than a shell string, so
    backticks and quotes in a rendered prompt cannot be
    reinterpreted.
    """
    prompt = "line one\nline `two`\n\"quoted\" $(echo hi)"
    argv = worker.claude_cli_argv(prompt)
    assert argv.count(prompt) == 1

  def test_model_is_optional(self):
    """No model means the CLI default."""
    assert "--model" not in worker.claude_cli_argv("x")
    argv = worker.claude_cli_argv("x", model="opus")
    assert argv[argv.index("--model") + 1] == "opus"

  def test_add_dirs_repeat_the_flag(self):
    """Each extra directory gets its own --add-dir."""
    argv = worker.claude_cli_argv(
      "x", add_dirs=["/a", "/b"]
    )
    assert argv.count("--add-dir") == 2

  def test_bare_is_off_by_default(self):
    """Parity with the scheduler's in-process agents."""
    assert "--bare" not in worker.claude_cli_argv("x")
    assert "--bare" in worker.claude_cli_argv(
      "x", bare=True
    )


class TestEnvironment:
  """Which account the CLI authenticates as."""

  def test_claude_home_sets_config_dir(self):
    """CLAUDE_CONFIG_DIR selects the account.

    Limits attach to the account, not the machine, so this
    is the setting that actually multiplies agent
    throughput across workers.
    """
    env = worker.claude_env(
      claude_home="~/accounts/b", base_env={"PATH": "/bin"}
    )
    assert env["CLAUDE_CONFIG_DIR"].endswith("accounts/b")
    assert "~" not in env["CLAUDE_CONFIG_DIR"]
    assert env["PATH"] == "/bin"

  def test_no_claude_home_leaves_env_alone(self):
    """Unset means the CLI's own default account."""
    env = worker.claude_env(base_env={"PATH": "/bin"})
    assert "CLAUDE_CONFIG_DIR" not in env


class TestEventParsing:
  """Reading the stream-json output."""

  def test_assistant_text(self):
    """Assistant text becomes readable output."""
    kind, content, _ = worker.parse_cli_event(
      ASSISTANT_LINE
    )
    assert kind == "assistant"
    assert content == "Running tests"

  def test_tool_use_is_summarised(self):
    """A tool call with no text still says something."""
    kind, content, _ = worker.parse_cli_event(TOOL_USE_LINE)
    assert kind == "assistant"
    assert "Bash" in content

  def test_result_carries_cost_and_turns(self):
    """The result line is where usage data lives."""
    kind, _, meta = worker.parse_cli_event(RESULT_OK_LINE)
    assert kind == "result"
    assert meta["num_turns"] == 3
    assert meta["total_cost_usd"] == pytest.approx(0.076)
    assert meta["is_error"] is False

  def test_error_result_is_flagged(self):
    """An errored run is distinguishable."""
    _, _, meta = worker.parse_cli_event(RESULT_ERR_LINE)
    assert meta["is_error"] is True
    assert meta["api_error_status"] == "overloaded"

  def test_unknown_event_type_is_kept(self):
    """rate_limit_event and friends are not dropped.

    Regression guard: a parser that understood only a fixed
    set of types would silently discard events the CLI
    added later. This exact type once broke the SDK path.
    """
    kind, content, _ = worker.parse_cli_event(
      RATE_LIMIT_LINE
    )
    assert kind == "rate_limit_event"
    assert "allowed_warning" in content

  def test_system_init_is_summarised(self):
    """The init line reports session and model."""
    kind, content, _ = worker.parse_cli_event(INIT_LINE)
    assert kind == "system"
    assert "e9f9fac6" in content

  def test_non_json_line_is_kept_verbatim(self):
    """Stray stderr-ish output is not lost."""
    kind, content, _ = worker.parse_cli_event(
      "something unparseable"
    )
    assert kind == "stdout"
    assert content == "something unparseable"

  def test_blank_lines_are_skipped(self):
    """Empty lines produce no output record."""
    assert worker.parse_cli_event("") is None
    assert worker.parse_cli_event("   \n") is None

  def test_long_content_is_truncated(self):
    """One enormous message cannot blow up a report."""
    line = json.dumps({
      "type": "assistant",
      "message": {"content": [
        {"type": "text", "text": "x" * 50000},
      ]},
    })
    _, content, _ = worker.parse_cli_event(line)
    assert len(content) <= 8000


class TestStageResult:
  """The agent's own verdict."""

  def test_reads_the_file(self, tmp_path):
    """A written verdict is picked up."""
    (tmp_path / ".stage-result.json").write_text(
      json.dumps({"status": "pass", "summary": "ok"})
    )
    assert worker.read_stage_result(
      tmp_path
    )["status"] == "pass"

  def test_missing_file_is_none(self, tmp_path):
    """No verdict is not a failure by itself."""
    assert worker.read_stage_result(tmp_path) is None

  def test_corrupt_file_is_none(self, tmp_path):
    """Unparseable JSON does not raise."""
    (tmp_path / ".stage-result.json").write_text("{nope")
    assert worker.read_stage_result(tmp_path) is None


class TestAgentStepOutcome:
  """How a CLI run turns into a step result.

  Popen is stubbed so the decision logic is covered
  without paying for a real model call. The happy path was
  verified against the real CLI separately.
  """

  @pytest.fixture
  def worker_cli(self):
    """Load bin/takt_worker.py as a module."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
      "takt_worker_under_test",
      str(PROJECT_DIR / "bin" / "takt_worker.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

  def _fake_popen(self, monkeypatch, worker_cli, lines,
                  returncode=0, stderr=""):
    class FakeProc:
      def __init__(self):
        self.stdout = iter(lines)
        self.stderr = type(
          "E", (), {"read": lambda _s: stderr}
        )()
        self.returncode = returncode

      def wait(self, timeout=None):
        return self.returncode

      def poll(self):
        return self.returncode

      def kill(self):
        pass

    monkeypatch.setattr(
      worker_cli.subprocess, "Popen",
      lambda *a, **k: FakeProc(),
    )

  def test_agent_fail_verdict_fails_the_step(
    self, worker_cli, monkeypatch, tmp_path
  ):
    """A clean CLI exit with a 'fail' verdict is a failure.

    Regression guard: treating CLI exit 0 as success would
    turn a failing test suite into a green step.
    """
    self._fake_popen(
      monkeypatch, worker_cli,
      [ASSISTANT_LINE, RESULT_OK_LINE],
    )
    (tmp_path / ".stage-result.json").write_text(
      json.dumps({
        "status": "fail", "summary": "3 tests failed",
      })
    )
    _, ok, summary = worker_cli._run_agent_step(
      {"name": "test", "step_type": "agent",
       "prompt": "go", "timeout_secs": 60},
      tmp_path,
    )
    assert ok is False
    assert "3 tests failed" in summary

  def test_cli_error_fails_the_step(
    self, worker_cli, monkeypatch, tmp_path
  ):
    """An errored CLI run fails even with no verdict."""
    self._fake_popen(
      monkeypatch, worker_cli, [RESULT_ERR_LINE],
    )
    _, ok, summary = worker_cli._run_agent_step(
      {"name": "test", "step_type": "agent",
       "prompt": "go", "timeout_secs": 60},
      tmp_path,
    )
    assert ok is False
    assert "overloaded" in summary

  def test_nonzero_exit_fails_with_stderr(
    self, worker_cli, monkeypatch, tmp_path
  ):
    """A crashed CLI reports its stderr."""
    self._fake_popen(
      monkeypatch, worker_cli, [], returncode=1,
      stderr="claude: command blew up",
    )
    _, ok, summary = worker_cli._run_agent_step(
      {"name": "test", "step_type": "agent",
       "prompt": "go", "timeout_secs": 60},
      tmp_path,
    )
    assert ok is False
    assert "blew up" in summary

  def test_missing_verdict_still_passes(
    self, worker_cli, monkeypatch, tmp_path
  ):
    """No .stage-result.json is not a failure.

    Matches the local executor: absent verdict plus a clean
    agent run counts as completed.
    """
    self._fake_popen(
      monkeypatch, worker_cli, [RESULT_OK_LINE],
    )
    _, ok, _ = worker_cli._run_agent_step(
      {"name": "test", "step_type": "agent",
       "prompt": "go", "timeout_secs": 60},
      tmp_path,
    )
    assert ok is True

  def test_empty_prompt_is_refused(
    self, worker_cli, tmp_path
  ):
    """A step with no prompt never reaches the CLI.

    Running an agent with no instructions would burn a
    model call to do something arbitrary.
    """
    with pytest.raises(
      worker_cli.WorkerError, match="no prompt"
    ):
      worker_cli._run_agent_step(
        {"name": "test", "step_type": "agent",
         "prompt": "   ", "timeout_secs": 60},
        tmp_path,
      )


class TestPromptRendering:
  """The scheduler renders; the worker never builds."""

  def test_lease_includes_a_rendered_prompt(
    self, tmp_path, monkeypatch
  ):
    """An agent step arrives with its prompt and model."""
    from lib import db, worker_ops
    path = str(tmp_path / "p.db")
    db.migrate(db_path=path)
    db.define_pipeline("feat-x", [
      {"name": "test", "step_type": "agent",
       "config": {"model": "opus"}},
    ], db_path=path)
    db.create_run(
      "feat-x", "manual", ["repo-a"],
      {"repo-a:feat-x": "abc"}, db_path=path,
    )
    work = worker_ops.lease("w1", db_path=path)
    step = work["steps"][0]
    assert step["model"] == "opus"
    assert "# Role: test" in step["prompt"]
    assert ".stage-result.json" in step["prompt"]

  def test_render_failure_does_not_fail_the_lease(
    self, tmp_path, monkeypatch
  ):
    """A broken template yields an empty prompt, not a 500.

    The worker refuses an empty prompt rather than running
    an agent with no instructions, but the lease call
    itself must still succeed.
    """
    from lib import db, worker_ops

    def boom(*a, **k):
      raise RuntimeError("template exploded")

    monkeypatch.setattr(
      "lib.pipeline.build_agent_prompt", boom
    )
    path = str(tmp_path / "q.db")
    db.migrate(db_path=path)
    db.define_pipeline("feat-y", [
      {"name": "test", "step_type": "agent"},
    ], db_path=path)
    db.create_run(
      "feat-y", "manual", [], {}, db_path=path
    )
    work = worker_ops.lease("w1", db_path=path)
    assert work["steps"][0]["prompt"] == ""
