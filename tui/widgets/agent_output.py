"""SDK message renderer for agent output.

Converts claude-code-sdk message objects into Rich Text
for display in a RichLog widget. Also renders serialized
output line dicts (from lib/protocol.py) back to Rich Text.
"""

import json

from rich.text import Text

from claude_code_sdk import (
  AssistantMessage,
  ResultMessage,
  TextBlock,
  ThinkingBlock,
  ToolResultBlock,
  ToolUseBlock,
)

# Max chars for truncated blocks.
_MAX_THINKING = 200
_MAX_TOOL_RESULT = 500


def render_message(msg):
  """Convert an SDK message to a Rich Text renderable.

  Args:
    msg: One of TextBlock, ToolUseBlock, ToolResultBlock,
      ThinkingBlock, ResultMessage, or AssistantMessage.

  Returns:
    Rich Text object, or None if the message type is not
    rendered.
  """
  if isinstance(msg, AssistantMessage):
    return _render_assistant(msg)
  if isinstance(msg, ResultMessage):
    return _render_result(msg)
  return None


def render_block(block):
  """Convert an SDK content block to Rich Text.

  Args:
    block: One of TextBlock, ToolUseBlock,
      ToolResultBlock, ThinkingBlock.

  Returns:
    Rich Text object, or None.
  """
  if isinstance(block, TextBlock):
    return _render_text(block)
  if isinstance(block, ToolUseBlock):
    return _render_tool_use(block)
  if isinstance(block, ToolResultBlock):
    return _render_tool_result(block)
  if isinstance(block, ThinkingBlock):
    return _render_thinking(block)
  return None


def _render_assistant(msg):
  """Render an AssistantMessage by rendering its blocks.

  Args:
    msg: AssistantMessage with content list.

  Returns:
    Rich Text combining all rendered blocks, or None.
  """
  parts = []
  for block in msg.content:
    rendered = render_block(block)
    if rendered:
      parts.append(rendered)
  if not parts:
    return None
  result = Text()
  for i, part in enumerate(parts):
    if i > 0:
      result.append("\n")
    result.append_text(part)
  return result


def _render_text(block):
  """Render a TextBlock.

  Args:
    block: TextBlock with text attribute.

  Returns:
    Rich Text.
  """
  return Text(block.text)


def _render_tool_use(block):
  """Render a ToolUseBlock.

  Args:
    block: ToolUseBlock with name and input.

  Returns:
    Rich Text with tool name and summarized input.
  """
  text = Text()
  text.append("[tool] ", style="bold #42a5f5")
  text.append(block.name, style="#42a5f5")
  # Show a brief summary of input.
  inp = block.input
  if isinstance(inp, dict):
    if "command" in inp:
      text.append(f" {inp['command']}", style="dim")
    elif "file_path" in inp:
      text.append(
        f" {inp['file_path']}", style="dim"
      )
    elif "pattern" in inp:
      text.append(
        f" {inp['pattern']}", style="dim"
      )
    else:
      summary = json.dumps(inp)
      if len(summary) > 80:
        summary = summary[:77] + "..."
      text.append(f" {summary}", style="dim")
  return text


def _render_tool_result(block):
  """Render a ToolResultBlock.

  Args:
    block: ToolResultBlock with content and is_error.

  Returns:
    Rich Text.
  """
  text = Text()
  is_err = block.is_error
  if is_err:
    text.append("[error] ", style="bold #ef5350")
  else:
    text.append("[result] ", style="bold #66bb6a")
  content = block.content
  if isinstance(content, str):
    if len(content) > _MAX_TOOL_RESULT:
      content = content[:_MAX_TOOL_RESULT] + "..."
    style = "#ef5350" if is_err else "#66bb6a"
    text.append(content, style=style)
  elif isinstance(content, list):
    # Multi-part content — show first text.
    for part in content:
      if isinstance(part, dict):
        t = part.get("text", "")
        if t:
          if len(t) > _MAX_TOOL_RESULT:
            t = t[:_MAX_TOOL_RESULT] + "..."
          text.append(t, style="#66bb6a")
          break
  return text


def _render_thinking(block):
  """Render a ThinkingBlock (truncated).

  Args:
    block: ThinkingBlock with thinking attribute.

  Returns:
    Rich Text.
  """
  text = Text()
  text.append("[thinking] ", style="bold dim")
  content = block.thinking
  if len(content) > _MAX_THINKING:
    content = content[:_MAX_THINKING] + "..."
  text.append(content, style="dim")
  return text


def _render_result(msg):
  """Render a ResultMessage (final summary).

  Args:
    msg: ResultMessage with duration, turns, cost.

  Returns:
    Rich Text.
  """
  text = Text()
  if msg.is_error:
    text.append("[failed] ", style="bold #ef5350")
  else:
    text.append("[done] ", style="bold #66bb6a")
  duration_s = msg.duration_ms / 1000
  parts = [f"{msg.num_turns} turns"]
  parts.append(f"{duration_s:.1f}s")
  if msg.total_cost_usd is not None:
    parts.append(f"${msg.total_cost_usd:.4f}")
  text.append(", ".join(parts))
  return text


def render_output_line(line):
  """Convert a serialized output line dict to Rich Text.

  Mirrors render_block/render_message but operates on
  dicts produced by lib/protocol.py.

  Args:
    line: Dict with keys: line_no, kind, content, meta.

  Returns:
    Rich Text object, or None if kind is unrecognized.
  """
  kind = line.get("kind")
  content = line.get("content", "")
  meta = line.get("meta", {})
  if kind == "text":
    return Text(content)
  if kind == "tool_use":
    return _render_tool_use_line(content, meta)
  if kind == "tool_result":
    return _render_tool_result_line(content, meta)
  if kind == "thinking":
    return _render_thinking_line(content)
  if kind == "result":
    return _render_result_line(meta)
  if kind == "system":
    return _render_system_line(content, meta)
  if kind == "error":
    text = Text()
    text.append("[error] ", style="bold #ef5350")
    text.append(content, style="#ef5350")
    return text
  return None


def _render_tool_use_line(content, meta):
  """Render a tool_use output line.

  Args:
    content: Tool name string.
    meta: Dict with 'input' key.

  Returns:
    Rich Text.
  """
  text = Text()
  text.append("[tool] ", style="bold #42a5f5")
  text.append(content, style="#42a5f5")
  inp = meta.get("input", {})
  if isinstance(inp, dict):
    if "command" in inp:
      text.append(f" {inp['command']}", style="dim")
    elif "file_path" in inp:
      text.append(f" {inp['file_path']}", style="dim")
    elif "pattern" in inp:
      text.append(f" {inp['pattern']}", style="dim")
    else:
      summary = json.dumps(inp)
      if len(summary) > 80:
        summary = summary[:77] + "..."
      text.append(f" {summary}", style="dim")
  return text


def _render_tool_result_line(content, meta):
  """Render a tool_result output line.

  Args:
    content: Result content string.
    meta: Dict with 'is_error' key.

  Returns:
    Rich Text.
  """
  text = Text()
  is_err = meta.get("is_error", False)
  if is_err:
    text.append("[error] ", style="bold #ef5350")
  else:
    text.append("[result] ", style="bold #66bb6a")
  if isinstance(content, str):
    if len(content) > _MAX_TOOL_RESULT:
      content = content[:_MAX_TOOL_RESULT] + "..."
    style = "#ef5350" if is_err else "#66bb6a"
    text.append(content, style=style)
  return text


def _render_thinking_line(content):
  """Render a thinking output line.

  Args:
    content: Thinking text string.

  Returns:
    Rich Text.
  """
  text = Text()
  text.append("[thinking] ", style="bold dim")
  if len(content) > _MAX_THINKING:
    content = content[:_MAX_THINKING] + "..."
  text.append(content, style="dim")
  return text


def _render_result_line(meta):
  """Render a result output line.

  Args:
    meta: Dict with is_error, duration_ms, num_turns,
      total_cost_usd.

  Returns:
    Rich Text.
  """
  text = Text()
  if meta.get("is_error"):
    text.append("[failed] ", style="bold #ef5350")
  else:
    text.append("[done] ", style="bold #66bb6a")
  duration_ms = meta.get("duration_ms", 0)
  duration_s = (duration_ms or 0) / 1000
  parts = [f"{meta.get('num_turns', 0)} turns"]
  parts.append(f"{duration_s:.1f}s")
  cost = meta.get("total_cost_usd")
  if cost is not None:
    parts.append(f"${cost:.4f}")
  text.append(", ".join(parts))
  return text


def _render_system_line(content, meta):
  """Render a system output line.

  Args:
    content: Message text.
    meta: Dict with 'subtype' key.

  Returns:
    Rich Text.
  """
  text = Text()
  subtype = meta.get("subtype", "")
  text.append(f"[{subtype}] ", style="bold dim")
  text.append(content, style="dim")
  return text
