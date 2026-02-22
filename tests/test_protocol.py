"""Tests for lib/protocol.py — output line serialization."""

import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from claude_code_sdk import (
  AssistantMessage,
  ResultMessage,
  SystemMessage,
  TextBlock,
  ThinkingBlock,
  ToolResultBlock,
  ToolUseBlock,
)

from lib.protocol import serialize_sdk_message


class TestSerializeTextBlock(unittest.TestCase):
  """Tests for TextBlock serialization."""

  def test_text_block(self):
    """TextBlock produces a text line."""
    block = TextBlock(text="hello world")
    lines = serialize_sdk_message(block, 0)
    self.assertEqual(len(lines), 1)
    self.assertEqual(lines[0]["line_no"], 0)
    self.assertEqual(lines[0]["kind"], "text")
    self.assertEqual(lines[0]["content"], "hello world")
    self.assertEqual(lines[0]["meta"], {})


class TestSerializeToolUseBlock(unittest.TestCase):
  """Tests for ToolUseBlock serialization."""

  def test_tool_use_block(self):
    """ToolUseBlock produces a tool_use line with input."""
    block = ToolUseBlock(
      id="tu_1",
      name="Bash",
      input={"command": "ls -la"},
    )
    lines = serialize_sdk_message(block, 5)
    self.assertEqual(len(lines), 1)
    line = lines[0]
    self.assertEqual(line["line_no"], 5)
    self.assertEqual(line["kind"], "tool_use")
    self.assertEqual(line["content"], "Bash")
    self.assertEqual(
      line["meta"]["input"], {"command": "ls -la"}
    )


class TestSerializeToolResultBlock(unittest.TestCase):
  """Tests for ToolResultBlock serialization."""

  def test_tool_result_success(self):
    """Successful ToolResultBlock has is_error=False."""
    block = ToolResultBlock(
      tool_use_id="tu_1",
      content="file.txt",
      is_error=False,
    )
    lines = serialize_sdk_message(block, 1)
    self.assertEqual(len(lines), 1)
    line = lines[0]
    self.assertEqual(line["kind"], "tool_result")
    self.assertEqual(line["content"], "file.txt")
    self.assertFalse(line["meta"]["is_error"])

  def test_tool_result_error(self):
    """Error ToolResultBlock has is_error=True."""
    block = ToolResultBlock(
      tool_use_id="tu_1",
      content="not found",
      is_error=True,
    )
    lines = serialize_sdk_message(block, 2)
    line = lines[0]
    self.assertTrue(line["meta"]["is_error"])
    self.assertEqual(line["content"], "not found")

  def test_tool_result_list_content(self):
    """Multi-part content extracts first text."""
    block = ToolResultBlock(
      tool_use_id="tu_1",
      content=[{"text": "first part"}],
      is_error=False,
    )
    lines = serialize_sdk_message(block, 0)
    self.assertEqual(lines[0]["content"], "first part")

  def test_tool_result_list_no_text(self):
    """Multi-part content with no text uses str()."""
    block = ToolResultBlock(
      tool_use_id="tu_1",
      content=[{"image": "data"}],
      is_error=False,
    )
    lines = serialize_sdk_message(block, 0)
    self.assertIn("image", lines[0]["content"])


class TestSerializeThinkingBlock(unittest.TestCase):
  """Tests for ThinkingBlock serialization."""

  def test_thinking_block(self):
    """ThinkingBlock produces a thinking line."""
    block = ThinkingBlock(
      thinking="let me consider...",
      signature="sig",
    )
    lines = serialize_sdk_message(block, 3)
    self.assertEqual(len(lines), 1)
    line = lines[0]
    self.assertEqual(line["kind"], "thinking")
    self.assertEqual(
      line["content"], "let me consider..."
    )


class TestSerializeAssistantMessage(unittest.TestCase):
  """Tests for AssistantMessage serialization."""

  def test_single_block(self):
    """AssistantMessage with one block produces one line."""
    msg = AssistantMessage(
      content=[TextBlock(text="hi")],
      model="sonnet",
    )
    lines = serialize_sdk_message(msg, 0)
    self.assertEqual(len(lines), 1)
    self.assertEqual(lines[0]["content"], "hi")

  def test_multiple_blocks(self):
    """AssistantMessage with multiple blocks increments line_no."""
    msg = AssistantMessage(
      content=[
        TextBlock(text="first"),
        ToolUseBlock(
          id="tu_1", name="Read",
          input={"file_path": "/tmp/x"},
        ),
      ],
      model="sonnet",
    )
    lines = serialize_sdk_message(msg, 10)
    self.assertEqual(len(lines), 2)
    self.assertEqual(lines[0]["line_no"], 10)
    self.assertEqual(lines[1]["line_no"], 11)
    self.assertEqual(lines[0]["kind"], "text")
    self.assertEqual(lines[1]["kind"], "tool_use")

  def test_empty_content(self):
    """AssistantMessage with no blocks produces empty list."""
    msg = AssistantMessage(content=[], model="sonnet")
    lines = serialize_sdk_message(msg, 0)
    self.assertEqual(lines, [])


class TestSerializeResultMessage(unittest.TestCase):
  """Tests for ResultMessage serialization."""

  def test_success(self):
    """Successful ResultMessage has correct meta."""
    msg = ResultMessage(
      subtype="result",
      duration_ms=5000,
      duration_api_ms=4000,
      is_error=False,
      num_turns=3,
      session_id="sess-1",
      total_cost_usd=0.05,
      usage=None,
      result="all done",
    )
    lines = serialize_sdk_message(msg, 7)
    self.assertEqual(len(lines), 1)
    line = lines[0]
    self.assertEqual(line["line_no"], 7)
    self.assertEqual(line["kind"], "result")
    self.assertEqual(line["content"], "all done")
    self.assertFalse(line["meta"]["is_error"])
    self.assertEqual(line["meta"]["duration_ms"], 5000)
    self.assertEqual(line["meta"]["num_turns"], 3)
    self.assertEqual(
      line["meta"]["total_cost_usd"], 0.05
    )
    self.assertEqual(
      line["meta"]["session_id"], "sess-1"
    )

  def test_error(self):
    """Error ResultMessage has is_error=True."""
    msg = ResultMessage(
      subtype="result",
      duration_ms=1000,
      duration_api_ms=900,
      is_error=True,
      num_turns=1,
      session_id="sess-2",
      total_cost_usd=0.01,
      usage=None,
      result="failed hard",
    )
    lines = serialize_sdk_message(msg, 0)
    self.assertTrue(lines[0]["meta"]["is_error"])

  def test_none_result(self):
    """ResultMessage with None result uses empty string."""
    msg = ResultMessage(
      subtype="result",
      duration_ms=0,
      duration_api_ms=0,
      is_error=False,
      num_turns=0,
      session_id=None,
      total_cost_usd=None,
      usage=None,
      result=None,
    )
    lines = serialize_sdk_message(msg, 0)
    self.assertEqual(lines[0]["content"], "")


class TestSerializeSystemMessage(unittest.TestCase):
  """Tests for SystemMessage serialization."""

  def test_system_message(self):
    """SystemMessage produces a system line."""
    msg = SystemMessage(
      subtype="retry",
      data={"message": "retrying in 30s"},
    )
    lines = serialize_sdk_message(msg, 4)
    self.assertEqual(len(lines), 1)
    line = lines[0]
    self.assertEqual(line["kind"], "system")
    self.assertEqual(line["content"], "retrying in 30s")
    self.assertEqual(line["meta"]["subtype"], "retry")

  def test_system_no_message(self):
    """SystemMessage without 'message' in data uses empty."""
    msg = SystemMessage(subtype="info", data={"x": 1})
    lines = serialize_sdk_message(msg, 0)
    self.assertEqual(lines[0]["content"], "")


class TestSerializeUnknown(unittest.TestCase):
  """Tests for unrecognized message types."""

  def test_unknown_returns_empty(self):
    """Unrecognized type produces empty list."""
    lines = serialize_sdk_message("not a message", 0)
    self.assertEqual(lines, [])


if __name__ == "__main__":
  unittest.main()
