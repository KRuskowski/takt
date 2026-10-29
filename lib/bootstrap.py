"""Make bin/ scripts find takt's dependencies.

Two ways of launching a script were both broken, in
opposite directions.

`#!/usr/bin/env python3` picks the system interpreter,
which cannot see anything installed in the project venv --
so `bin/workspace.py ...`, exactly as CLAUDE.md documents
it, died on `ModuleNotFoundError: claude_agent_sdk`.

Pinning the shebang to an absolute venv path instead, as
bin/takt.py did, fixes that one machine and breaks every
other: a worker whose checkout is elsewhere, or which has
no venv at all, gets "bad interpreter". That is why the
laptop needed `python3 bin/takt.py` rather than
`bin/takt.py`.

So neither is hardcoded. A script calls ensure_venv()
before importing anything of takt's, and it re-executes
itself under the venv interpreter when there is one and the
current interpreter is not it. Where there is no venv --
the normal state on a worker, which needs only PyYAML --
it does nothing.

Uses only os, sys and pathlib, so it is importable by the
interpreter that cannot import the dependencies.
"""

import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent

# Set across the exec so a mis-built venv cannot put the
# process in a loop.
_GUARD = "TAKT_BOOTSTRAPPED"


def venv_python(project_dir=None):
  """Path to the project venv interpreter, if present.

  Args:
    project_dir: Checkout root. Defaults to this one.

  Returns:
    Path to the interpreter, or None.
  """
  root = Path(project_dir or PROJECT_DIR)
  for relative in (
    Path(".venv/bin/python3"),
    Path(".venv/bin/python"),
    Path(".venv/Scripts/python.exe"),
  ):
    candidate = root / relative
    if candidate.exists():
      return candidate
  return None


def needs_reexec(interpreter, prefix=None, environ=None):
  """Decide whether to re-execute under the venv.

  Compares sys.prefix against the venv root, NOT the
  interpreter paths. A venv's bin/python3 is a symlink to
  the base interpreter, so resolving both paths makes the
  system interpreter and the venv one compare equal -- the
  first version of this check did exactly that and never
  re-executed. sys.prefix is the venv root only when the
  process is actually running under it, which is the real
  question.

  Args:
    interpreter: Venv interpreter, or None if there is none.
    prefix: Running interpreter's prefix. Defaults to
      sys.prefix.
    environ: Environment to check the guard in.

  Returns:
    True if the process should re-execute.
  """
  env = os.environ if environ is None else environ
  if interpreter is None or env.get(_GUARD):
    return False
  venv_root = Path(interpreter).parent.parent
  running = Path(prefix if prefix is not None
                 else sys.prefix)
  return running != venv_root


def ensure_venv(project_dir=None, argv=None):
  """Re-execute under the project venv when appropriate.

  Call before importing any of takt's own modules -- the
  point is to fix the interpreter *before* an import fails.

  Args:
    project_dir: Checkout root. Defaults to this one.
    argv: Argument vector. Defaults to sys.argv.

  Returns:
    False when nothing was done. Does not return when it
    re-executes.
  """
  interpreter = venv_python(project_dir)
  if not needs_reexec(interpreter):
    return False
  args = list(argv if argv is not None else sys.argv)
  os.environ[_GUARD] = "1"
  # execv, not a subprocess: the caller keeps its pid, its
  # file descriptors and its exit status, which matters for
  # an MCP server speaking stdio on this process's stdin.
  os.execv(str(interpreter), [str(interpreter)] + args)
