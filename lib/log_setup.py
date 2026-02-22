"""Logging configuration for takt.

Configures a file handler writing to .state/agent.log.
"""

import logging
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent.parent / ".state"
LOG_PATH = STATE_DIR / "agent.log"


def setup_logging():
  """Configure takt logging with a file handler.

  Creates .state/ if needed. Writes to .state/agent.log
  with rotation-friendly formatting.
  """
  STATE_DIR.mkdir(parents=True, exist_ok=True)
  handler = logging.FileHandler(LOG_PATH)
  handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
  ))
  root = logging.getLogger("takt")
  root.setLevel(logging.DEBUG)
  root.addHandler(handler)
