#!/usr/bin/env python3
"""takt agent REPL.

Conversational AI for managing takt. Uses claude-code-sdk
with the active account from config/takt.yaml.
"""

import asyncio
import os
import sys

from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from claude_code_sdk import ClaudeCodeOptions, query
from claude_code_sdk.types import (
  AssistantMessage,
  ResultMessage,
  TextBlock,
)

from lib import db
from lib.config import load_takt_config


SYSTEM = """\
You are the takt management agent running inside the takt \
CLI shell in a web browser. You help operators manage \
workspaces, targets, pipelines, and runs.

Your working directory is the takt project root. You have \
full access to the takt tools via the bin/ and lib/ \
directories. Use them to execute operations.

Be concise. When listing items, summarize counts and key \
details rather than dumping raw output.

## Panel Control

Above the terminal are toggleable monitoring panels. You \
can control them by printing special escape sequences:
  echo -ne '\\033]1337;takt-panel;show;runs\\007'
  echo -ne '\\033]1337;takt-panel;hide;runs\\007'
  echo -ne '\\033]1337;takt-panel;toggle;targets\\007'
  echo -ne '\\033]1337;takt-panel;show;all\\007'
  echo -ne '\\033]1337;takt-panel;hide;all\\007'

Available panels: summary, runs, agents, targets, workspaces

When the operator asks to see monitoring data, show the \
relevant panel."""


def _load_agent_config():
  """Load agent config from takt.yaml."""
  cfg = load_takt_config()
  agent_cfg = cfg.get("agent", {})
  model = agent_cfg.get("model", "claude-opus-4-6[1m]")
  active = agent_cfg.get("active_account", "default")
  accounts = agent_cfg.get("accounts", {})
  account = accounts.get(active, {})
  claude_home = account.get("claude_home", "~/.claude")
  claude_home = str(
    Path(claude_home).expanduser().resolve()
  )
  label = account.get("label", active)
  return {
    "model": model,
    "account": active,
    "label": label,
    "claude_home": claude_home,
  }


async def run_agent_async():
  """Async agent REPL."""
  agent_cfg = _load_agent_config()
  model = agent_cfg["model"]
  account = agent_cfg["account"]
  label = agent_cfg["label"]
  claude_home = agent_cfg["claude_home"]

  # Point claude-code-sdk at the right .claude dir.
  os.environ["CLAUDE_CONFIG_DIR"] = claude_home

  opts = ClaudeCodeOptions(
    cwd=str(PROJECT_DIR),
    system_prompt=SYSTEM,
    permission_mode="bypassPermissions",
    model=model,
  )

  db.migrate()

  sys.stdout.write(
    f"\x1b[38;5;248m"
    f"takt agent — {label} — {model}\n"
    f"type 'exit' or Ctrl-D to return to the shell\n"
    f"\x1b[0m"
  )
  sys.stdout.flush()

  loop = asyncio.get_event_loop()

  while True:
    try:
      user_input = await loop.run_in_executor(
        None,
        lambda: input(
          "\x1b[38;5;243mtakt(agent)>\x1b[0m "
        ),
      )
    except (EOFError, KeyboardInterrupt):
      sys.stdout.write("\n")
      break

    stripped = user_input.strip()
    if not stripped:
      continue
    if stripped in ("exit", "quit"):
      break

    try:
      got_text = False
      turn_cost = 0.0
      spinner_active = True

      async def spin():
        import time
        words = [
          "thinking", "reasoning", "considering",
          "analyzing", "planning", "working",
        ]
        dots = ""
        start = time.monotonic()
        while spinner_active:
          elapsed = time.monotonic() - start
          word = words[int(elapsed / 3) % len(words)]
          dots = "." * (int(elapsed * 2) % 4)
          sys.stdout.write(
            f"\r\x1b[2K\x1b[38;5;243m"
            f"  {word}{dots}\x1b[0m"
          )
          sys.stdout.flush()
          await asyncio.sleep(0.4)

      spinner_task = asyncio.create_task(spin())

      async for msg in query(
        prompt=stripped, options=opts,
      ):
        if isinstance(msg, AssistantMessage):
          for block in msg.content:
            if isinstance(block, TextBlock):
              if spinner_active:
                spinner_active = False
                spinner_task.cancel()
                sys.stdout.write("\r\x1b[2K")
                sys.stdout.flush()
              sys.stdout.write(block.text)
              sys.stdout.flush()
              got_text = True
        elif isinstance(msg, ResultMessage):
          if msg.total_cost_usd is not None:
            turn_cost = msg.total_cost_usd
      spinner_active = False
      spinner_task.cancel()
      if not got_text:
        sys.stdout.write(
          "\r\x1b[2K\x1b[38;5;243m(no response)\x1b[0m\n"
        )
      if turn_cost > 0:
        db.record_agent_usage(
          account, model, turn_cost,
        )
      if got_text:
        sys.stdout.write("\n")
      sys.stdout.flush()
    except KeyboardInterrupt:
      spinner_active = False
      spinner_task.cancel()
      sys.stdout.write(
        "\r\x1b[2K\x1b[33minterrupted\x1b[0m\n"
      )
      sys.stdout.flush()
    except Exception as e:
      spinner_active = False
      spinner_task.cancel()
      sys.stdout.write(
        f"\x1b[31merror: {e}\x1b[0m\n"
      )
      sys.stdout.flush()


def run_agent():
  """Run the agent REPL."""
  asyncio.run(run_agent_async())


if __name__ == "__main__":
  run_agent()
