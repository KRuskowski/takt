#!/usr/bin/env python3
"""takt REST API server entry point.

Serves the REST API that the einheit-ui takt adapter
proxies. Standalone process — no dependency on
takt-service for read operations.

Usage:
  bin/takt_api.py [--port 7433] [--bind 127.0.0.1]
"""

import argparse
import logging
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib.api import create_app
from aiohttp import web


def main():
  """Parse args and run the API server."""
  parser = argparse.ArgumentParser(
    description="takt REST API server.",
  )
  parser.add_argument(
    "--port", type=int, default=7433,
    help="Listen port (default: 7433).",
  )
  parser.add_argument(
    "--bind", default="127.0.0.1",
    help="Bind address (default: 127.0.0.1).",
  )
  args = parser.parse_args()

  from lib.log_setup import setup_logging
  setup_logging()
  logging.getLogger("takt.api").info(
    "Starting takt API on %s:%d",
    args.bind, args.port,
  )

  app = create_app()
  web.run_app(
    app, host=args.bind, port=args.port,
    print=lambda msg: logging.getLogger("takt.api")
      .info(msg),
  )


if __name__ == "__main__":
  main()
