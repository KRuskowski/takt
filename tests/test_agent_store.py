"""Tests for lib/agent_store.py — agent persistence."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib.agent_runner import AgentInfo, AgentState
from lib.agent_store import AgentStore, _safe_id


class TestSafeId(unittest.TestCase):
  """Tests for _safe_id helper."""

  def test_slash_replaced(self):
    """Slashes become dashes."""
    self.assertEqual(_safe_id("ws/role"), "ws-role")

  def test_no_slash(self):
    """No slashes passes through unchanged."""
    self.assertEqual(_safe_id("simple"), "simple")

  def test_multiple_slashes(self):
    """Multiple slashes all replaced."""
    self.assertEqual(
      _safe_id("a/b/c"), "a-b-c"
    )


class TestAgentStore(unittest.TestCase):
  """Tests for AgentStore class."""

  def setUp(self):
    self._tmpdir = tempfile.TemporaryDirectory()
    self.base_dir = Path(self._tmpdir.name)
    self.store = AgentStore(base_dir=self.base_dir)

  def tearDown(self):
    self._tmpdir.cleanup()

  def _make_info(self, agent_id="ws/test", **kwargs):
    defaults = {
      "agent_id": agent_id,
      "workspace": "ws",
      "role": "test",
      "cwd": "/tmp",
      "model": "sonnet",
      "state": AgentState.RUNNING,
      "total_cost_usd": 0.05,
      "num_turns": 3,
      "started_at": 1000.0,
    }
    defaults.update(kwargs)
    return AgentInfo(**defaults)

  def test_save_and_load_info(self):
    """Round-trip save and load preserves all fields."""
    info = self._make_info(
      session_id="sess-1",
      finished_at=2000.0,
      error="oops",
    )
    self.store.save_info(info)
    loaded = self.store.load_info("ws/test")
    self.assertIsNotNone(loaded)
    self.assertEqual(loaded.agent_id, "ws/test")
    self.assertEqual(loaded.workspace, "ws")
    self.assertEqual(loaded.role, "test")
    self.assertEqual(loaded.cwd, "/tmp")
    self.assertEqual(loaded.model, "sonnet")
    self.assertEqual(loaded.state, AgentState.RUNNING)
    self.assertEqual(loaded.session_id, "sess-1")
    self.assertEqual(loaded.total_cost_usd, 0.05)
    self.assertEqual(loaded.num_turns, 3)
    self.assertEqual(loaded.started_at, 1000.0)
    self.assertEqual(loaded.finished_at, 2000.0)
    self.assertEqual(loaded.error, "oops")

  def test_load_missing(self):
    """Loading nonexistent agent returns None."""
    self.assertIsNone(
      self.store.load_info("no/such")
    )

  def test_load_corrupt_json(self):
    """Loading corrupt JSON returns None."""
    d = self.base_dir / "bad-agent"
    d.mkdir()
    (d / "info.json").write_text("{broken")
    self.assertIsNone(
      self.store.load_info("bad/agent")
    )

  def test_save_overwrites(self):
    """Saving again overwrites previous info."""
    info = self._make_info(num_turns=1)
    self.store.save_info(info)
    info.num_turns = 10
    info.state = AgentState.COMPLETED
    self.store.save_info(info)
    loaded = self.store.load_info("ws/test")
    self.assertEqual(loaded.num_turns, 10)
    self.assertEqual(loaded.state, AgentState.COMPLETED)

  def test_append_and_load_output(self):
    """Appended lines are loadable."""
    lines = [
      {"line_no": 0, "kind": "text",
       "content": "hi", "meta": {}},
      {"line_no": 1, "kind": "tool_use",
       "content": "Bash", "meta": {"input": {}}},
    ]
    self.store.append_output("ws/test", lines)
    loaded = self.store.load_output("ws/test")
    self.assertEqual(len(loaded), 2)
    self.assertEqual(loaded[0]["content"], "hi")
    self.assertEqual(loaded[1]["content"], "Bash")

  def test_append_incremental(self):
    """Multiple appends accumulate."""
    self.store.append_output("ws/test", [
      {"line_no": 0, "kind": "text",
       "content": "a", "meta": {}},
    ])
    self.store.append_output("ws/test", [
      {"line_no": 1, "kind": "text",
       "content": "b", "meta": {}},
    ])
    loaded = self.store.load_output("ws/test")
    self.assertEqual(len(loaded), 2)

  def test_load_output_from_line(self):
    """from_line filters out earlier lines."""
    lines = [
      {"line_no": 0, "kind": "text",
       "content": "a", "meta": {}},
      {"line_no": 1, "kind": "text",
       "content": "b", "meta": {}},
      {"line_no": 2, "kind": "text",
       "content": "c", "meta": {}},
    ]
    self.store.append_output("ws/test", lines)
    loaded = self.store.load_output("ws/test", from_line=1)
    self.assertEqual(len(loaded), 2)
    self.assertEqual(loaded[0]["content"], "b")

  def test_load_output_missing(self):
    """Loading output for nonexistent agent returns []."""
    self.assertEqual(
      self.store.load_output("no/such"), []
    )

  def test_list_agents_empty(self):
    """Empty store returns empty list."""
    self.assertEqual(self.store.list_agents(), [])

  def test_list_agents(self):
    """Lists all persisted agents sorted by started_at."""
    info1 = self._make_info(
      "a/first", started_at=100.0
    )
    info2 = self._make_info(
      "b/second", started_at=200.0
    )
    self.store.save_info(info1)
    self.store.save_info(info2)
    agents = self.store.list_agents()
    self.assertEqual(len(agents), 2)
    # Newest first.
    self.assertEqual(agents[0].agent_id, "b/second")
    self.assertEqual(agents[1].agent_id, "a/first")

  def test_list_agents_skips_corrupt(self):
    """Corrupt info files are skipped."""
    info = self._make_info()
    self.store.save_info(info)
    # Create corrupt entry.
    d = self.base_dir / "corrupt"
    d.mkdir()
    (d / "info.json").write_text("{bad")
    agents = self.store.list_agents()
    self.assertEqual(len(agents), 1)

  def test_directory_name_safe(self):
    """Agent with / in ID uses - in directory name."""
    info = self._make_info("my-ws/review")
    self.store.save_info(info)
    self.assertTrue(
      (self.base_dir / "my-ws-review").is_dir()
    )
    loaded = self.store.load_info("my-ws/review")
    self.assertEqual(
      loaded.agent_id, "my-ws/review"
    )


class TestAgentStoreOutputJSONL(unittest.TestCase):
  """Verify JSONL format on disk."""

  def setUp(self):
    self._tmpdir = tempfile.TemporaryDirectory()
    self.base_dir = Path(self._tmpdir.name)
    self.store = AgentStore(base_dir=self.base_dir)

  def tearDown(self):
    self._tmpdir.cleanup()

  def test_jsonl_format(self):
    """Each line in output.jsonl is valid JSON."""
    lines = [
      {"line_no": 0, "kind": "text",
       "content": "a", "meta": {}},
      {"line_no": 1, "kind": "text",
       "content": "b", "meta": {}},
    ]
    self.store.append_output("ws/x", lines)
    path = self.base_dir / "ws-x" / "output.jsonl"
    raw_lines = path.read_text().strip().split("\n")
    self.assertEqual(len(raw_lines), 2)
    for raw in raw_lines:
      parsed = json.loads(raw)
      self.assertIn("line_no", parsed)
      self.assertIn("kind", parsed)


if __name__ == "__main__":
  unittest.main()
