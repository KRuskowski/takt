"""SDK message renderer for agent output.

Converts claude-code-sdk message objects into Rich Text
for display in a RichLog widget. Also renders serialized
output line dicts (from lib/protocol.py) back to Rich Text.
"""

from rich.text import Text

from claude_code_sdk import (
  AssistantMessage,
  ResultMessage,
  TextBlock,
  ThinkingBlock,
  ToolResultBlock,
  ToolUseBlock,
)

_TOOL_ICON = "\u25cf"
_RESULT_INDENT = "  \u23bf  "
_CONT_INDENT = "     "
_DONE_ICON = "\u273b"
_FAIL_ICON = "\u2717"
_THINK_ICON = "\u2699"
_WARN_ICON = "\u26a0"
_RETRY_ICON = "\u27f3"
_MAX_RESULT_LINES = 8
_MAX_THINKING = 200

# Maps tool names to their primary argument key.
_PRIMARY_ARG_KEYS = {
  "Bash": "command",
  "Read": "file_path",
  "Edit": "file_path",
  "Write": "file_path",
  "Grep": "pattern",
  "Glob": "pattern",
  "WebFetch": "url",
  "Task": "description",
}


def _extract_primary_arg(name, inp):
  """Extract the primary argument for a tool invocation.

  Args:
    name: Tool name string.
    inp: Input dict for the tool.

  Returns:
    Primary argument string, or empty string.
  """
  if not isinstance(inp, dict):
    return ""
  key = _PRIMARY_ARG_KEYS.get(name)
  if key and key in inp:
    return str(inp[key])
  # Fallback: first string value, truncated.
  for v in inp.values():
    if isinstance(v, str):
      if len(v) > 60:
        return v[:57] + "..."
      return v
  return ""


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
  """Render a ToolUseBlock as icon ToolName(arg).

  Args:
    block: ToolUseBlock with name and input.

  Returns:
    Rich Text with tool icon, name, and primary arg.
  """
  text = Text()
  text.append(f"{_TOOL_ICON} ", style="bold #42a5f5")
  text.append(block.name, style="#42a5f5")
  arg = _extract_primary_arg(block.name, block.input)
  if arg:
    text.append(f" ({arg})", style="dim")
  return text


def _render_tool_result(block):
  """Render a ToolResultBlock with connector indent.

  Args:
    block: ToolResultBlock with content and is_error.

  Returns:
    Rich Text.
  """
  is_err = block.is_error
  content = block.content
  if isinstance(content, list):
    # Multi-part content — extract first text.
    for part in content:
      if isinstance(part, dict):
        t = part.get("text", "")
        if t:
          content = t
          break
    else:
      content = ""
  if not isinstance(content, str):
    content = str(content)
  return _format_result_content(content, is_err)


def _format_result_content(content, is_err):
  """Format result content with indented lines.

  Args:
    content: Result content string.
    is_err: Whether this is an error result.

  Returns:
    Rich Text with connector and indented lines.
  """
  text = Text()
  if is_err:
    text.append(_RESULT_INDENT, style="dim")
    text.append(f"Error: {content}", style="#ef5350")
    text.append("\n")
    return text
  lines = content.split("\n")
  total = len(lines)
  show = lines[:_MAX_RESULT_LINES]
  for i, line in enumerate(show):
    if i > 0:
      text.append("\n")
    if i == 0:
      text.append(_RESULT_INDENT, style="dim")
    else:
      text.append(_CONT_INDENT)
    text.append(line)
  if total > _MAX_RESULT_LINES:
    remaining = total - _MAX_RESULT_LINES
    text.append("\n")
    text.append(_CONT_INDENT)
    text.append(
      f"\u2026 +{remaining} lines", style="dim"
    )
  text.append("\n")
  return text


def _render_thinking(block):
  """Render a ThinkingBlock as collapsed summary.

  Args:
    block: ThinkingBlock with thinking attribute.

  Returns:
    Rich Text.
  """
  text = Text()
  text.append(f"{_THINK_ICON} ", style="dim italic")
  content = block.thinking
  first_line = content.split("\n", 1)[0]
  if len(first_line) > _MAX_THINKING:
    first_line = first_line[:_MAX_THINKING] + "\u2026"
  text.append(first_line, style="dim italic")
  return text


def _render_result(msg):
  """Render a ResultMessage (final summary).

  Args:
    msg: ResultMessage with duration, turns, cost.

  Returns:
    Rich Text.
  """
  text = Text()
  duration_s = msg.duration_ms / 1000
  parts = [f"{msg.num_turns} turns"]
  parts.append(f"{duration_s:.1f}s")
  if msg.total_cost_usd is not None:
    parts.append(f"${msg.total_cost_usd:.4f}")
  summary = ", ".join(parts)
  if msg.is_error:
    text.append(
      f"{_FAIL_ICON} ", style="bold #ef5350"
    )
    text.append(
      f"Failed \u2014 {summary}", style="#ef5350"
    )
  else:
    text.append(
      f"{_DONE_ICON} ", style="bold #66bb6a"
    )
    text.append(summary, style="#66bb6a")
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
    text.append(f"{_WARN_ICON} ", style="bold #ffa726")
    text.append(content, style="#ffa726")
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
  text.append(f"{_TOOL_ICON} ", style="bold #42a5f5")
  text.append(content, style="#42a5f5")
  inp = meta.get("input", {})
  arg = _extract_primary_arg(content, inp)
  if arg:
    text.append(f" ({arg})", style="dim")
  return text


def _render_tool_result_line(content, meta):
  """Render a tool_result output line.

  Args:
    content: Result content string.
    meta: Dict with 'is_error' key.

  Returns:
    Rich Text.
  """
  is_err = meta.get("is_error", False)
  if not isinstance(content, str):
    content = str(content)
  return _format_result_content(content, is_err)


def _render_thinking_line(content):
  """Render a thinking output line.

  Args:
    content: Thinking text string.

  Returns:
    Rich Text.
  """
  text = Text()
  text.append(f"{_THINK_ICON} ", style="dim italic")
  first_line = content.split("\n", 1)[0]
  if len(first_line) > _MAX_THINKING:
    first_line = first_line[:_MAX_THINKING] + "\u2026"
  text.append(first_line, style="dim italic")
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
  duration_ms = meta.get("duration_ms", 0)
  duration_s = (duration_ms or 0) / 1000
  parts = [f"{meta.get('num_turns', 0)} turns"]
  parts.append(f"{duration_s:.1f}s")
  cost = meta.get("total_cost_usd")
  if cost is not None:
    parts.append(f"${cost:.4f}")
  summary = ", ".join(parts)
  if meta.get("is_error"):
    text.append(
      f"{_FAIL_ICON} ", style="bold #ef5350"
    )
    text.append(
      f"Failed \u2014 {summary}", style="#ef5350"
    )
  else:
    text.append(
      f"{_DONE_ICON} ", style="bold #66bb6a"
    )
    text.append(summary, style="#66bb6a")
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
  if subtype == "retry":
    text.append(f"{_RETRY_ICON} ", style="bold #ffa726")
  else:
    text.append(f"{_WARN_ICON} ", style="bold #ffa726")
  text.append(content, style="#ffa726")
  return text
