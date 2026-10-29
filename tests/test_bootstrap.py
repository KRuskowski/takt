"""Tests for finding takt's dependencies from bin/ scripts.

Both ways of launching a script were broken, in opposite
directions: `#!/usr/bin/env python3` could not see the venv,
and an absolute venv shebang broke every machine whose
checkout was elsewhere. These cover the resolution that
replaced both.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib import bootstrap


class TestVenvDetection:
  """Finding the project interpreter."""

  def test_finds_a_posix_venv(self, tmp_path):
    exe = tmp_path / ".venv/bin/python3"
    exe.parent.mkdir(parents=True)
    exe.touch()
    assert bootstrap.venv_python(tmp_path) == exe

  def test_finds_a_windows_venv(self, tmp_path):
    exe = tmp_path / ".venv/Scripts/python.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()
    assert bootstrap.venv_python(tmp_path) == exe

  def test_no_venv_is_none(self, tmp_path):
    """The normal state on a worker, which needs no venv."""
    assert bootstrap.venv_python(tmp_path) is None


class TestReexecDecision:
  """Whether to swap interpreter."""

  def test_reexecs_when_running_outside_the_venv(
    self, tmp_path
  ):
    exe = tmp_path / ".venv/bin/python3"
    assert bootstrap.needs_reexec(
      exe, prefix="/usr", environ={}
    )

  def test_does_not_reexec_when_already_inside(
    self, tmp_path
  ):
    exe = tmp_path / ".venv/bin/python3"
    assert not bootstrap.needs_reexec(
      exe, prefix=str(tmp_path / ".venv"), environ={},
    )

  def test_symlinked_interpreters_still_reexec(self):
    """The bug that made the first version a no-op.

    A venv's bin/python3 is a symlink to the base
    interpreter, so comparing resolved interpreter paths
    makes the system python and the venv python equal --
    and nothing ever re-executed. The comparison is against
    sys.prefix for that reason.
    """
    real = PROJECT_DIR / ".venv/bin/python3"
    if not real.exists():
      pytest.skip("no venv in this checkout")
    system = Path("/usr/bin/python3")
    if not system.exists():
      pytest.skip("no system python to compare against")
    # The precondition: the two interpreters resolve to the
    # same file, so any path-equality check sees them as
    # identical.
    assert system.resolve() == real.resolve(), (
      "venv is not symlinked here; test no longer "
      "reproduces the condition"
    )
    # The decision must not depend on that coincidence.
    assert bootstrap.needs_reexec(
      real, prefix="/usr", environ={}
    )

  def test_no_venv_means_no_reexec(self):
    assert not bootstrap.needs_reexec(
      None, prefix="/usr", environ={}
    )

  def test_guard_prevents_a_loop(self, tmp_path):
    """A mis-built venv must not spin the process."""
    exe = tmp_path / ".venv/bin/python3"
    assert not bootstrap.needs_reexec(
      exe, prefix="/usr",
      environ={"TAKT_BOOTSTRAPPED": "1"},
    )


class TestScriptsRunUnderSystemPython:
  """The invocation CLAUDE.md actually documents.

  `bin/workspace.py ...` under system python died on
  ModuleNotFoundError for a dependency that was installed
  all along -- in the venv. An agent hit this and
  hand-created a workspace instead.
  """

  @pytest.mark.parametrize("args", [
    ["bin/workspace.py", "list"],
    ["bin/takt.py", "ws", "list", "--names-only"],
  ])
  def test_runs_with_the_system_interpreter(self, args):
    if not (PROJECT_DIR / ".venv/bin/python3").exists():
      pytest.skip("no venv in this checkout")
    env = dict(os.environ)
    env.pop("TAKT_BOOTSTRAPPED", None)
    proc = subprocess.run(
      ["/usr/bin/python3"] + args,
      cwd=str(PROJECT_DIR), capture_output=True, text=True,
      timeout=120, env=env,
    )
    assert proc.returncode == 0, (
      f"stdout={proc.stdout[-400:]}\n"
      f"stderr={proc.stderr[-600:]}"
    )
    assert "ModuleNotFoundError" not in proc.stderr

  def test_no_absolute_venv_shebangs(self):
    """An absolute shebang breaks every other machine.

    bin/takt.py carried
    `#!/home/karl/dev/takt/.venv/bin/python3`, which is why
    the laptop needed `python3 bin/takt.py` instead of
    running the script directly.
    """
    offenders = []
    for script in sorted(
      (PROJECT_DIR / "bin").glob("*.py")
    ):
      first = script.read_text().splitlines()[:1]
      if first and first[0].startswith("#!"):
        if ".venv" in first[0]:
          offenders.append(script.name)
    assert not offenders, (
      f"absolute venv shebangs in {offenders}"
    )


class TestMcpResolvesCredentialsPerCall:
  """Why every MCP tool returned 401.

  The server resolved the token at import. It is a
  long-lived process, one per Claude session, so a server
  started before the token existed held "" forever while
  the API answered 200 for anyone sending the current
  token.
  """

  def test_no_module_level_token(self):
    import ast
    source = (
      PROJECT_DIR / "bin" / "takt_mcp.py"
    ).read_text()
    tree = ast.parse(source)
    assigned = set()
    for node in tree.body:
      if isinstance(node, ast.Assign):
        for target in node.targets:
          if isinstance(target, ast.Name):
            assigned.add(target.id)
    assert "TAKT_TOKEN" not in assigned
    assert "TAKT_API" not in assigned

  def test_token_is_read_inside_the_request(self):
    source = (
      PROJECT_DIR / "bin" / "takt_mcp.py"
    ).read_text()
    body = source[source.index("def _api("):]
    body = body[:body.index("\n\n\n")]
    assert "api_token()" in body
    assert "api_base_url()" in body
