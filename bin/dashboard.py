#!/usr/bin/env python3
"""takt dashboard — TUI entry point."""

import sys
from pathlib import Path

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from tui.app import TaktApp


def main():
  app = TaktApp()
  app.run()


if __name__ == "__main__":
  main()
