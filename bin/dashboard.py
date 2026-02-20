#!/usr/bin/env python3
"""Agent Orchestration Dashboard — TUI entry point."""

import sys
from pathlib import Path

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from tui.app import DashboardApp


def main():
  app = DashboardApp()
  app.run()


if __name__ == "__main__":
  main()
