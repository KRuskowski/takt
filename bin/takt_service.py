#!/usr/bin/env python3
"""takt-service entry point.

Runs the persistent background service for pipeline
watching and agent execution.
"""

import argparse
import asyncio
import signal
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib.log_setup import setup_logging
from lib.service import (
  DEFAULT_INTERVAL,
  DEFAULT_MAX_AGENTS,
  TaktService,
)


def main():
  """Parse args and run the service."""
  parser = argparse.ArgumentParser(
    description="takt background service.",
  )
  parser.add_argument(
    "--interval", type=int, default=DEFAULT_INTERVAL,
    help=(
      "Poll interval in seconds "
      f"(default: {DEFAULT_INTERVAL})."
    ),
  )
  parser.add_argument(
    "--once", action="store_true",
    help="Run a single poll cycle and exit.",
  )
  parser.add_argument(
    "--reset", action="store_true",
    help="Clear stored branch refs and exit.",
  )
  parser.add_argument(
    "--max-agents", type=int,
    default=DEFAULT_MAX_AGENTS,
    help=(
      "Max concurrent agents "
      f"(default: {DEFAULT_MAX_AGENTS})."
    ),
  )
  args = parser.parse_args()

  if args.reset:
    from lib.config import STATE_DIR
    refs = STATE_DIR / "branch_refs.json"
    if refs.exists():
      refs.unlink()
      print("Cleared stored branch refs.")
    return

  setup_logging()

  service = TaktService(
    interval=args.interval,
    max_agents=args.max_agents,
  )

  loop = asyncio.new_event_loop()
  asyncio.set_event_loop(loop)

  def handle_signal(sig, frame):
    loop.call_soon_threadsafe(
      lambda: asyncio.ensure_future(service.stop())
    )

  signal.signal(signal.SIGTERM, handle_signal)
  signal.signal(signal.SIGINT, handle_signal)

  try:
    loop.run_until_complete(
      service.start(once=args.once)
    )
  except KeyboardInterrupt:
    loop.run_until_complete(service.stop())
  finally:
    loop.close()


if __name__ == "__main__":
  main()
