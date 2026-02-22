"""Tests for agent_output message renderer."""

import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from claude_code_sdk import (
  AssistantMessage,
  ResultMessage,
  TextBlock,
  ThinkingBlock,
  ToolResultBlock,
  ToolUseBlock,
)
from tui.widgets.agent_output import (
  render_block,
  render_message,
  render_output_line,
)


class TestRenderBlock(unittest.TestCase):
  """Tests for render_block()."""

  def test_text_block(self):
    """TextBlock renders as plain text."""
    block = TextBlock(text="Hello world")
    result = render_block(block)
    self.assertIsNotNone(result)
    self.assertIn("Hello world", result.plain)

  def test_tool_use_block_command(self):
    """ToolUseBlock with command shows icon and arg."""
    block = ToolUseBlock(
      id="tu-1",
      name="Bash",
      input={"command": "git status"},
    )
    result = render_block(block)
    self.assertIsNotNone(result)
    self.assertIn("\u25cf", result.plain)
    self.assertIn("Bash", result.plain)
    self.assertIn("git status", result.plain)

  def test_tool_use_block_file(self):
    """ToolUseBlock with file_path shows path in parens."""
    block = ToolUseBlock(
      id="tu-2",
      name="Read",
      input={"file_path": "/tmp/foo.py"},
    )
    result = render_block(block)
    self.assertIsNotNone(result)
    self.assertIn("Read", result.plain)
    self.assertIn(" (/tmp/foo.py)", result.plain)

  def test_tool_use_block_pattern(self):
    """ToolUseBlock with pattern shows pattern in parens."""
    block = ToolUseBlock(
      id="tu-3",
      name="Glob",
      input={"pattern": "*.py"},
    )
    result = render_block(block)
    self.assertIn(" (*.py)", result.plain)

  def test_tool_use_block_generic(self):
    """ToolUseBlock with unknown keys shows first value."""
    block = ToolUseBlock(
      id="tu-4",
      name="Custom",
      input={"key": "value"},
    )
    result = render_block(block)
    self.assertIsNotNone(result)
    self.assertIn("Custom", result.plain)
    self.assertIn(" (value)", result.plain)

  def test_tool_result_success(self):
    """ToolResultBlock success renders with connector."""
    block = ToolResultBlock(
      tool_use_id="tu-1",
      content="output text",
      is_error=False,
    )
    result = render_block(block)
    self.assertIsNotNone(result)
    self.assertIn("\u23bf", result.plain)
    self.assertIn("output text", result.plain)

  def test_tool_result_error(self):
    """ToolResultBlock error renders with Error prefix."""
    block = ToolResultBlock(
      tool_use_id="tu-1",
      content="error msg",
      is_error=True,
    )
    result = render_block(block)
    self.assertIn("\u23bf", result.plain)
    self.assertIn("Error:", result.plain)
    self.assertIn("error msg", result.plain)

  def test_tool_result_truncated(self):
    """Multi-line ToolResultBlock content is truncated."""
    lines = [f"line {i}" for i in range(20)]
    long_text = "\n".join(lines)
    block = ToolResultBlock(
      tool_use_id="tu-1",
      content=long_text,
      is_error=False,
    )
    result = render_block(block)
    self.assertIn("\u2026 +12 lines", result.plain)
    self.assertNotIn("line 19", result.plain)

  def test_tool_result_list_content(self):
    """ToolResultBlock with list content renders."""
    block = ToolResultBlock(
      tool_use_id="tu-1",
      content=[{"text": "list output"}],
      is_error=False,
    )
    result = render_block(block)
    self.assertIn("list output", result.plain)

  def test_thinking_block(self):
    """ThinkingBlock renders with icon, truncated."""
    block = ThinkingBlock(
      thinking="a" * 300,
      signature="sig",
    )
    result = render_block(block)
    self.assertIn("\u2699", result.plain)
    self.assertIn("\u2026", result.plain)

  def test_thinking_block_short(self):
    """Short ThinkingBlock not truncated."""
    block = ThinkingBlock(
      thinking="short thought",
      signature="sig",
    )
    result = render_block(block)
    self.assertIn("short thought", result.plain)
    self.assertNotIn("\u2026", result.plain)

  def test_unknown_type(self):
    """Unknown block type returns None."""
    result = render_block("not a block")
    self.assertIsNone(result)


class TestRenderMessage(unittest.TestCase):
  """Tests for render_message()."""

  def test_assistant_message(self):
    """AssistantMessage renders its blocks."""
    msg = AssistantMessage(
      content=[
        TextBlock(text="Hello"),
        TextBlock(text="World"),
      ],
      model="sonnet",
    )
    result = render_message(msg)
    self.assertIsNotNone(result)
    self.assertIn("Hello", result.plain)
    self.assertIn("World", result.plain)

  def test_result_message_success(self):
    """ResultMessage success shows done icon."""
    msg = ResultMessage(
      subtype="result",
      duration_ms=5000,
      duration_api_ms=4000,
      is_error=False,
      num_turns=3,
      session_id="sess-1",
      total_cost_usd=0.0123,
      usage=None,
      result="ok",
    )
    result = render_message(msg)
    self.assertIn("\u273b", result.plain)
    self.assertIn("3 turns", result.plain)
    self.assertIn("5.0s", result.plain)
    self.assertIn("$0.0123", result.plain)

  def test_result_message_error(self):
    """ResultMessage error shows fail icon."""
    msg = ResultMessage(
      subtype="result",
      duration_ms=1000,
      duration_api_ms=500,
      is_error=True,
      num_turns=1,
      session_id="sess-2",
      total_cost_usd=None,
      usage=None,
      result=None,
    )
    result = render_message(msg)
    self.assertIn("\u2717", result.plain)
    self.assertIn("Failed", result.plain)

  def test_unknown_message_type(self):
    """Unknown message type returns None."""
    result = render_message("not a message")
    self.assertIsNone(result)


class TestRenderOutputLine(unittest.TestCase):
  """Tests for render_output_line() — dict-based rendering."""

  def test_text(self):
    """Text line renders as plain text."""
    line = {"line_no": 0, "kind": "text",
            "content": "hello", "meta": {}}
    result = render_output_line(line)
    self.assertIsNotNone(result)
    self.assertEqual(result.plain, "hello")

  def test_tool_use_command(self):
    """Tool use with command shows icon and arg."""
    line = {"line_no": 1, "kind": "tool_use",
            "content": "Bash",
            "meta": {"input": {"command": "ls"}}}
    result = render_output_line(line)
    self.assertIn("\u25cf", result.plain)
    self.assertIn("Bash", result.plain)
    self.assertIn(" (ls)", result.plain)

  def test_tool_use_file_path(self):
    """Tool use with file_path shows path in parens."""
    line = {"line_no": 1, "kind": "tool_use",
            "content": "Read",
            "meta": {"input": {"file_path": "/x"}}}
    result = render_output_line(line)
    self.assertIn("Read", result.plain)
    self.assertIn(" (/x)", result.plain)

  def test_tool_use_pattern(self):
    """Tool use with pattern shows pattern in parens."""
    line = {"line_no": 1, "kind": "tool_use",
            "content": "Glob",
            "meta": {"input": {"pattern": "*.py"}}}
    result = render_output_line(line)
    self.assertIn(" (*.py)", result.plain)

  def test_tool_use_generic(self):
    """Tool use with generic input shows first value."""
    line = {"line_no": 1, "kind": "tool_use",
            "content": "Custom",
            "meta": {"input": {"a": "val"}}}
    result = render_output_line(line)
    self.assertIn("Custom", result.plain)
    self.assertIn(" (val)", result.plain)

  def test_tool_result_success(self):
    """Successful tool result shows connector."""
    line = {"line_no": 2, "kind": "tool_result",
            "content": "output",
            "meta": {"is_error": False}}
    result = render_output_line(line)
    self.assertIn("\u23bf", result.plain)
    self.assertIn("output", result.plain)

  def test_tool_result_error(self):
    """Error tool result shows Error prefix."""
    line = {"line_no": 2, "kind": "tool_result",
            "content": "bad",
            "meta": {"is_error": True}}
    result = render_output_line(line)
    self.assertIn("\u23bf", result.plain)
    self.assertIn("Error:", result.plain)

  def test_tool_result_truncated(self):
    """Multi-line tool result content is truncated."""
    lines = "\n".join(f"L{i}" for i in range(20))
    line = {"line_no": 2, "kind": "tool_result",
            "content": lines,
            "meta": {"is_error": False}}
    result = render_output_line(line)
    self.assertIn("\u2026 +12 lines", result.plain)

  def test_thinking(self):
    """Thinking line shows icon."""
    line = {"line_no": 3, "kind": "thinking",
            "content": "hmm", "meta": {}}
    result = render_output_line(line)
    self.assertIn("\u2699", result.plain)
    self.assertIn("hmm", result.plain)

  def test_thinking_truncated(self):
    """Long thinking content is truncated."""
    line = {"line_no": 3, "kind": "thinking",
            "content": "t" * 300, "meta": {}}
    result = render_output_line(line)
    self.assertIn("\u2026", result.plain)

  def test_result_success(self):
    """Result line shows done icon."""
    line = {"line_no": 4, "kind": "result",
            "content": "",
            "meta": {"is_error": False,
                     "duration_ms": 5000,
                     "num_turns": 3,
                     "total_cost_usd": 0.05}}
    result = render_output_line(line)
    self.assertIn("\u273b", result.plain)
    self.assertIn("3 turns", result.plain)
    self.assertIn("5.0s", result.plain)
    self.assertIn("$0.0500", result.plain)

  def test_result_error(self):
    """Error result line shows fail icon."""
    line = {"line_no": 4, "kind": "result",
            "content": "",
            "meta": {"is_error": True,
                     "duration_ms": 1000,
                     "num_turns": 1,
                     "total_cost_usd": None}}
    result = render_output_line(line)
    self.assertIn("\u2717", result.plain)
    self.assertIn("Failed", result.plain)

  def test_system(self):
    """System line shows warn icon."""
    line = {"line_no": 5, "kind": "system",
            "content": "something happened",
            "meta": {"subtype": "info"}}
    result = render_output_line(line)
    self.assertIn("\u26a0", result.plain)
    self.assertIn("something happened", result.plain)

  def test_system_retry(self):
    """System retry line shows retry icon."""
    line = {"line_no": 5, "kind": "system",
            "content": "retrying",
            "meta": {"subtype": "retry"}}
    result = render_output_line(line)
    self.assertIn("\u27f3", result.plain)
    self.assertIn("retrying", result.plain)

  def test_error_kind(self):
    """Error kind shows warn icon."""
    line = {"line_no": 6, "kind": "error",
            "content": "something broke", "meta": {}}
    result = render_output_line(line)
    self.assertIn("\u26a0", result.plain)
    self.assertIn("something broke", result.plain)

  def test_unknown_kind(self):
    """Unknown kind returns None."""
    line = {"line_no": 0, "kind": "unknown",
            "content": "", "meta": {}}
    result = render_output_line(line)
    self.assertIsNone(result)


if __name__ == "__main__":
  unittest.main()
