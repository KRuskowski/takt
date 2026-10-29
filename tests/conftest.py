"""Shared fixtures.

The important one here is credential isolation. lib.config
resolves the API token from the environment and from
~/.config/takt/env, so on the operator's own machine the
tests would otherwise pick up the real token and the API
suites would start 401-ing against themselves. Worse, a test
that did authenticate would be exercising a different code
path on the developer's box than in CI.

So every test runs as though no credentials are configured,
unless it sets them itself.
"""

import os
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))


@pytest.fixture(autouse=True)
def isolate_credentials(monkeypatch, tmp_path):
  """Hide the machine's real takt credentials from a test.

  TAKT_ENV_FILE points at a path inside the test's tmp dir
  that is never created, so load_env_file() reads nothing
  rather than the operator's file. A test wanting a token
  sets one explicitly; it will win, because it runs after
  this fixture.
  """
  monkeypatch.delenv("TAKT_API_TOKEN", raising=False)
  monkeypatch.delenv("TAKT_API_URL", raising=False)
  monkeypatch.setenv(
    "TAKT_ENV_FILE", str(tmp_path / "absent-env")
  )
  import lib.config as config
  monkeypatch.setattr(
    config, "ENV_FILE", tmp_path / "absent-env"
  )
  yield
