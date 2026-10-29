"""Tests for the unified `takt` CLI.

The point of these is drift: `takt worker` and
bin/takt_worker.py must stay the same command. They share a
parser builder precisely so they cannot diverge, and these
tests fail if someone adds a flag to one path only.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))


def load_bin(name):
  """Load a bin/ script as a module."""
  spec = importlib.util.spec_from_file_location(
    f"bin_under_test_{name}",
    str(PROJECT_DIR / "bin" / f"{name}.py"),
  )
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def run_cli(*args, timeout=120):
  """Run bin/takt.py with arguments."""
  return subprocess.run(
    [sys.executable, str(PROJECT_DIR / "bin" / "takt.py"),
     *args],
    capture_output=True, text=True, timeout=timeout,
    cwd=str(PROJECT_DIR),
  )


def flags_of(parser):
  """Collect every option string a parser accepts."""
  flags = set()
  for action in parser._actions:
    flags.update(action.option_strings)
  return flags


class TestSubcommandsPresent:
  """Everything reachable from one executable."""

  def test_top_level_lists_all_groups(self):
    """The groups a user needs are all there."""
    res = run_cli("--help")
    assert res.returncode == 0
    for group in (
      "ws", "target", "pipeline", "push", "service",
      "worker", "reclaim",
    ):
      assert group in res.stdout, group

  def test_bare_invocation_prints_help(self):
    """No arguments is help, not a traceback."""
    res = run_cli()
    assert res.returncode == 0
    assert "usage: takt" in res.stdout


class TestWorkerParity:
  """`takt worker` is bin/takt_worker.py."""

  def test_same_flags_as_the_standalone_script(self):
    """Neither entry point can gain a flag alone.

    Regression guard: the two used to be separate argparse
    trees, which is exactly how a --flag ends up working
    on one and not the other.
    """
    import argparse
    worker = load_bin("takt_worker")
    standalone = worker.add_worker_args(
      argparse.ArgumentParser()
    )
    res = run_cli("worker", "--help")
    assert res.returncode == 0
    for flag in flags_of(standalone):
      if flag == "-h":
        continue
      assert flag in res.stdout, (
        f"{flag} missing from `takt worker`"
      )

  def test_required_flags_are_enforced(self):
    """The scheduler guard fires through takt."""
    res = run_cli("worker", "--id", "x")
    assert res.returncode != 0
    assert "--scheduler is required" in res.stderr

  def test_errors_are_prefixed_takt(self):
    """Usage errors name the command the user typed.

    Reporting 'takt_worker.py: error' for a `takt worker`
    invocation sends people to the wrong help page.
    """
    res = run_cli("worker", "--id", "x")
    assert res.stderr.startswith("takt: error:") or (
      "takt: error:" in res.stderr
    )
    assert "takt_worker.py: error" not in res.stderr


class TestReclaimParity:
  """`takt reclaim` is bin/reclaim.py."""

  def test_same_flags_as_the_standalone_script(self):
    """Flag sets match."""
    import argparse
    reclaim = load_bin("reclaim")
    standalone = reclaim.add_reclaim_args(
      argparse.ArgumentParser()
    )
    res = run_cli("reclaim", "--help")
    assert res.returncode == 0
    for flag in flags_of(standalone):
      if flag == "-h":
        continue
      assert flag in res.stdout, (
        f"{flag} missing from `takt reclaim`"
      )

  def test_apply_guard_fires(self):
    """The refusal to wipe every workspace survives."""
    res = run_cli("reclaim", "--apply")
    assert res.returncode != 0
    assert "refusing" in res.stderr

  def test_unknown_workspace_is_an_error(self):
    """Exit code propagates out of the subcommand."""
    res = run_cli(
      "reclaim", "--workspace", "no-such-ws-xyz"
    )
    assert res.returncode == 2


class TestStandaloneScriptsStillWork:
  """The old paths keep working for muscle memory and
  anything already wired to them (systemd, cron, docs)."""

  @pytest.mark.parametrize("script,needle", [
    ("takt_worker", "--scheduler is required"),
    ("reclaim", "refusing"),
  ])
  def test_scripts_run_and_guard(self, script, needle):
    """Each script still enforces its own guard."""
    args = (
      ["--id", "x"] if script == "takt_worker"
      else ["--apply"]
    )
    res = subprocess.run(
      [sys.executable,
       str(PROJECT_DIR / "bin" / f"{script}.py"), *args],
      capture_output=True, text=True, timeout=120,
      cwd=str(PROJECT_DIR),
    )
    assert res.returncode != 0
    assert needle in res.stderr

  def test_worker_module_exposes_both_halves(self):
    """The split API is what takt.py depends on."""
    worker = load_bin("takt_worker")
    assert callable(worker.add_worker_args)
    assert callable(worker.run_worker)
    assert callable(worker.main)


class TestShellCompletion:
  """config/takt.zsh is sourced into the user's shell."""

  def test_zsh_file_parses(self):
    """A syntax error there breaks every new shell."""
    res = subprocess.run(
      ["zsh", "-n", str(PROJECT_DIR / "config" / "takt.zsh")],
      capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, res.stderr

  def test_every_group_is_completable(self):
    """A subcommand with no completion entry is invisible.

    The group list in takt.zsh is hand-maintained, so it
    silently falls behind the parser otherwise.
    """
    text = (
      PROJECT_DIR / "config" / "takt.zsh"
    ).read_text()
    for group in (
      "ws", "target", "pipeline", "push", "service",
      "worker", "reclaim",
    ):
      assert f"'{group}:" in text, (
        f"{group} missing from takt.zsh completions"
      )


class TestWorkerNeedsNoSchedulerDeps:
  """A worker machine needs only the `claude` CLI.

  lib.commands pulls in claude_code_sdk through
  lib.pipeline -> lib.agent_runner. That is a scheduler
  dependency: a worker runs agent steps by invoking the
  `claude` binary as a subprocess, never the Python SDK. If
  bin/takt.py imports it at module scope, `takt worker`
  dies on import on precisely the machines the command is
  for — which is how this was found, on a laptop with the
  CLI installed and no venv.
  """

  def test_takt_py_does_not_import_commands_at_module_scope(
    self
  ):
    """The import must stay inside a function."""
    import ast
    source = (PROJECT_DIR / "bin" / "takt.py").read_text()
    tree = ast.parse(source)
    offenders = [
      node.module
      for node in tree.body
      if isinstance(node, ast.ImportFrom)
      and (node.module or "").startswith("lib.commands")
    ]
    assert not offenders, (
      "bin/takt.py imports lib.commands at module scope; "
      "that breaks `takt worker` on a worker machine"
    )

  def test_worker_runs_without_claude_code_sdk(self):
    """`takt worker --help` works with the SDK absent.

    Simulated by blocking the module in a subprocess, so
    the test is meaningful on a machine that happens to
    have it installed.
    """
    script = (
      "import sys\n"
      "class Blocker:\n"
      "  def find_module(self, name, path=None):\n"
      "    if name == 'claude_code_sdk':\n"
      "      raise ImportError('blocked by test')\n"
      "    return None\n"
      "  def find_spec(self, name, path=None, target=None):\n"
      "    if name == 'claude_code_sdk':\n"
      "      raise ImportError('blocked by test')\n"
      "    return None\n"
      "sys.meta_path.insert(0, Blocker())\n"
      f"sys.argv = ['takt', 'worker', '--help']\n"
      "import runpy\n"
      "try:\n"
      f"  runpy.run_path({str(PROJECT_DIR / 'bin' / 'takt.py')!r},"
      " run_name='__main__')\n"
      "except SystemExit as e:\n"
      "  sys.exit(e.code or 0)\n"
    )
    proc = subprocess.run(
      [sys.executable, "-c", script],
      capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, (
      f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "--scheduler" in proc.stdout
