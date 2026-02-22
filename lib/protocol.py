"""Output line serialization for agent messages.

Converts claude-code-sdk message objects into persistable
dicts for storage in JSONL files and ZMQ transport.

Output line format:
  {"line_no": 0, "kind": "text", "content": "...", "meta": {}}

Kinds: text, tool_use, tool_result, thinking, result,
  system, error.
"""

from claude_code_sdk import (
  AssistantMessage,
  ResultMessage,
  SystemMessage,
  TextBlock,
  ThinkingBlock,
  ToolResultBlock,
  ToolUseBlock,
)


def serialize_sdk_message(msg, line_no):
  """Convert an SDK message to a list of output line dicts.

  Each content block in an AssistantMessage produces one
  line. ResultMessage and SystemMessage produce one line
  each.

  Args:
    msg: SDK message (AssistantMessage, ResultMessage,
      SystemMessage, or content block).
    line_no: Starting line number for the first output line.

  Returns:
    List of dicts, each with keys: line_no, kind, content,
    meta. May be empty if the message type is unrecognized.
  """
  if isinstance(msg, AssistantMessage):
    return _serialize_assistant(msg, line_no)
  if isinstance(msg, ResultMessage):
    return [_serialize_result(msg, line_no)]
  if isinstance(msg, SystemMessage):
    return [_serialize_system(msg, line_no)]
  # Direct content blocks.
  line = _serialize_block(msg, line_no)
  if line is not None:
    return [line]
  return []


def _serialize_assistant(msg, line_no):
  """Serialize an AssistantMessage's content blocks.

  Args:
    msg: AssistantMessage with content list.
    line_no: Starting line number.

  Returns:
    List of output line dicts.
  """
  lines = []
  for block in msg.content:
    line = _serialize_block(block, line_no + len(lines))
    if line is not None:
      lines.append(line)
  return lines


def _serialize_block(block, line_no):
  """Serialize a single content block.

  Args:
    block: TextBlock, ToolUseBlock, ToolResultBlock, or
      ThinkingBlock.
    line_no: Line number for this block.

  Returns:
    Output line dict, or None if unrecognized.
  """
  if isinstance(block, TextBlock):
    return _line(line_no, "text", block.text)
  if isinstance(block, ToolUseBlock):
    return _line(line_no, "tool_use", block.name, {
      "input": block.input,
    })
  if isinstance(block, ToolResultBlock):
    content = block.content
    if isinstance(content, list):
      # Multi-part content — extract first text.
      for part in content:
        if isinstance(part, dict) and part.get("text"):
          content = part["text"]
          break
      else:
        content = str(content)
    return _line(line_no, "tool_result", content, {
      "is_error": block.is_error,
    })
  if isinstance(block, ThinkingBlock):
    return _line(line_no, "thinking", block.thinking)
  return None


def _serialize_result(msg, line_no):
  """Serialize a ResultMessage.

  Args:
    msg: ResultMessage with duration, turns, cost.
    line_no: Line number.

  Returns:
    Output line dict.
  """
  return _line(line_no, "result", str(msg.result or ""), {
    "is_error": msg.is_error,
    "duration_ms": msg.duration_ms,
    "num_turns": msg.num_turns,
    "total_cost_usd": msg.total_cost_usd,
    "session_id": msg.session_id,
  })


def _serialize_system(msg, line_no):
  """Serialize a SystemMessage.

  Args:
    msg: SystemMessage with subtype and data.
    line_no: Line number.

  Returns:
    Output line dict.
  """
  content = msg.data.get("message", "")
  return _line(line_no, "system", content, {
    "subtype": msg.subtype,
    "data": msg.data,
  })


def _line(line_no, kind, content, meta=None):
  """Build an output line dict.

  Args:
    line_no: Integer line number.
    kind: String kind identifier.
    content: String content.
    meta: Optional metadata dict.

  Returns:
    Dict with line_no, kind, content, meta keys.
  """
  return {
    "line_no": line_no,
    "kind": kind,
    "content": content,
    "meta": meta or {},
  }
