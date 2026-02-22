"""Tests for pipeline_watch marker scanning and stage triggering."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from bin.pipeline_watch import (
  MAX_EVENTS,
  _detect_stage_result,
  _retrigger_pr_stage,
  build_sync_prompt,
  build_trigger_prompt,
  load_events,
  log_events,
  scan_markers,
  scan_sync_markers,
  write_sync_markers,
)


class TestScanMarkers(unittest.TestCase):
  """Tests for scan_markers()."""

  def setUp(self):
    self.tmpdir = tempfile.mkdtemp()
    self.stages_dir = Path(self.tmpdir) / "stages"
    self.stages_dir.mkdir()
    self.patch = mock.patch(
      "bin.pipeline_watch.STAGES_DIR", self.stages_dir,
    )
    self.patch.start()

  def tearDown(self):
    self.patch.stop()
    shutil.rmtree(self.tmpdir)

  def test_empty_stages_dir(self):
    """Returns empty dict when no stages exist."""
    result = scan_markers()
    self.assertEqual(result, {})

  def test_no_markers(self):
    """Returns empty dict when stages exist but no markers."""
    repo = self.stages_dir / "ws" / "test" / "myrepo"
    repo.mkdir(parents=True)
    result = scan_markers()
    self.assertEqual(result, {})

  def test_single_marker(self):
    """Finds a single marker file."""
    repo = self.stages_dir / "ws" / "test" / "myrepo"
    repo.mkdir(parents=True)
    marker = repo / ".pipeline-push"
    marker.write_text(
      "2026-02-20T10:00:00+00:00 aaa bbb refs/heads/ws\n"
    )
    result = scan_markers()
    self.assertIn(("ws", "test"), result)
    self.assertEqual(len(result[("ws", "test")]), 1)
    repo_name, lines = result[("ws", "test")][0]
    self.assertEqual(repo_name, "myrepo")
    self.assertEqual(len(lines), 1)

  def test_multiple_repos(self):
    """Groups markers from multiple repos in same stage."""
    for name in ("repo-a", "repo-b"):
      repo = self.stages_dir / "ws" / "test" / name
      repo.mkdir(parents=True)
      (repo / ".pipeline-push").write_text(
        f"2026-02-20T10:00:00+00:00 aaa bbb refs/heads/ws\n"
      )
    result = scan_markers()
    self.assertEqual(len(result[("ws", "test")]), 2)

  def test_multiple_stages(self):
    """Finds markers across different stages."""
    for role in ("test", "review"):
      repo = self.stages_dir / "ws" / role / "myrepo"
      repo.mkdir(parents=True)
      (repo / ".pipeline-push").write_text(
        "2026-02-20T10:00:00+00:00 aaa bbb refs/heads/ws\n"
      )
    result = scan_markers()
    self.assertIn(("ws", "test"), result)
    self.assertIn(("ws", "review"), result)

  def test_empty_marker_skipped(self):
    """Empty marker files are ignored."""
    repo = self.stages_dir / "ws" / "test" / "myrepo"
    repo.mkdir(parents=True)
    (repo / ".pipeline-push").write_text("")
    result = scan_markers()
    self.assertEqual(result, {})

  def test_nonexistent_stages_dir(self):
    """Returns empty dict when STAGES_DIR doesn't exist."""
    shutil.rmtree(self.stages_dir)
    result = scan_markers()
    self.assertEqual(result, {})

  def test_multiline_marker(self):
    """Multiple push lines are preserved."""
    repo = self.stages_dir / "ws" / "test" / "myrepo"
    repo.mkdir(parents=True)
    (repo / ".pipeline-push").write_text(
      "2026-02-20T10:00:00+00:00 aaa bbb refs/heads/ws\n"
      "2026-02-20T10:05:00+00:00 bbb ccc refs/heads/ws\n"
    )
    result = scan_markers()
    _, lines = result[("ws", "test")][0]
    self.assertEqual(len(lines), 2)


class TestBuildTriggerPrompt(unittest.TestCase):
  """Tests for build_trigger_prompt()."""

  def setUp(self):
    self.tmpdir = tempfile.mkdtemp()
    self.stages_dir = Path(self.tmpdir) / "stages"
    self.stages_dir.mkdir()
    self.patch = mock.patch(
      "bin.pipeline_watch.STAGES_DIR", self.stages_dir,
    )
    self.patch.start()

  def tearDown(self):
    self.patch.stop()
    shutil.rmtree(self.tmpdir)

  def _make_git_repo(self, ws, role, repo):
    """Create a minimal git repo in the stage directory."""
    repo_dir = self.stages_dir / ws / role / repo
    repo_dir.mkdir(parents=True)
    subprocess.run(
      ["git", "init"], cwd=repo_dir,
      capture_output=True, check=True,
    )
    subprocess.run(
      ["git", "commit", "--allow-empty", "-m", "init"],
      cwd=repo_dir, capture_output=True, check=True,
      env={
        **os.environ,
        "GIT_COMMITTER_NAME": "test",
        "GIT_AUTHOR_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_AUTHOR_EMAIL": "t@t",
      },
    )
    return repo_dir

  def test_new_branch(self):
    """Prompt mentions new branch when old ref is all zeros."""
    null_ref = "0" * 40
    new_ref = "abc1234567890" + "0" * 27
    lines = [f"2026-02-20T10:00:00+00:00 {null_ref} {new_ref}"
             f" refs/heads/ws"]
    prompt = build_trigger_prompt(
      "ws", "test", [("myrepo", lines)],
    )
    self.assertIn("New branch created", prompt)
    self.assertIn("abc12345", prompt)

  @mock.patch("bin.pipeline_watch.get_log", return_value="abc fix")
  def test_prompt_structure(self, mock_log):
    """Prompt has expected header and footer."""
    lines = [
      "2026-02-20T10:00:00+00:00 aaa bbb refs/heads/ws",
    ]
    prompt = build_trigger_prompt(
      "ws", "test", [("myrepo", lines)],
    )
    self.assertIn("Incoming changes on branch `ws`", prompt)
    self.assertIn("## myrepo", prompt)
    self.assertIn("Process these changes", prompt)
    self.assertIn("push to origin", prompt)

  def test_with_real_commits(self):
    """Prompt includes commit log from real git repo."""
    repo_dir = self._make_git_repo("ws", "test", "myrepo")
    # Get the initial commit hash.
    old_ref = subprocess.run(
      ["git", "rev-parse", "HEAD"],
      cwd=repo_dir, capture_output=True, text=True,
    ).stdout.strip()
    # Add another commit.
    subprocess.run(
      ["git", "commit", "--allow-empty", "-m", "second"],
      cwd=repo_dir, capture_output=True, check=True,
      env={
        **os.environ,
        "GIT_COMMITTER_NAME": "test",
        "GIT_AUTHOR_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_AUTHOR_EMAIL": "t@t",
      },
    )
    new_ref = subprocess.run(
      ["git", "rev-parse", "HEAD"],
      cwd=repo_dir, capture_output=True, text=True,
    ).stdout.strip()
    lines = [
      f"2026-02-20T10:00:00+00:00 {old_ref} {new_ref}"
      f" refs/heads/ws",
    ]
    prompt = build_trigger_prompt(
      "ws", "test", [("myrepo", lines)],
    )
    self.assertIn("second", prompt)

  def test_malformed_line_skipped(self):
    """Lines with too few tokens are skipped."""
    lines = ["bad line"]
    prompt = build_trigger_prompt(
      "ws", "test", [("myrepo", lines)],
    )
    self.assertIn("## myrepo", prompt)
    self.assertIn("Process these changes", prompt)

  def test_multiple_push_lines_coalesced(self):
    """Multiple pushes use first old and last new ref."""
    repo_dir = self._make_git_repo("ws", "test", "myrepo")
    # Create 3 commits: init -> second -> third.
    refs = []
    refs.append(subprocess.run(
      ["git", "rev-parse", "HEAD"],
      cwd=repo_dir, capture_output=True, text=True,
    ).stdout.strip())
    for msg in ("second", "third"):
      subprocess.run(
        ["git", "commit", "--allow-empty", "-m", msg],
        cwd=repo_dir, capture_output=True, check=True,
        env={
          **os.environ,
          "GIT_COMMITTER_NAME": "test",
          "GIT_AUTHOR_NAME": "test",
          "GIT_COMMITTER_EMAIL": "t@t",
          "GIT_AUTHOR_EMAIL": "t@t",
        },
      )
      refs.append(subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_dir, capture_output=True, text=True,
      ).stdout.strip())
    lines = [
      f"2026-02-20T10:00:00+00:00 {refs[0]} {refs[1]}"
      f" refs/heads/ws",
      f"2026-02-20T10:05:00+00:00 {refs[1]} {refs[2]}"
      f" refs/heads/ws",
    ]
    prompt = build_trigger_prompt(
      "ws", "test", [("myrepo", lines)],
    )
    # Should include both commits.
    self.assertIn("second", prompt)
    self.assertIn("third", prompt)


class TestScanSyncMarkers(unittest.TestCase):
  """Tests for scan_sync_markers()."""

  def setUp(self):
    self.tmpdir = tempfile.mkdtemp()
    self.ws_dir = Path(self.tmpdir) / "workspaces"
    self.ws_dir.mkdir()
    self.patch = mock.patch(
      "bin.pipeline_watch.WORKSPACES_DIR", self.ws_dir,
    )
    self.patch.start()

  def tearDown(self):
    self.patch.stop()
    shutil.rmtree(self.tmpdir)

  def test_empty_dir(self):
    """Returns empty dict when no workspaces exist."""
    result = scan_sync_markers()
    self.assertEqual(result, {})

  def test_no_markers(self):
    """Returns empty dict when repos exist but no markers."""
    repo = self.ws_dir / "ws" / "myrepo"
    repo.mkdir(parents=True)
    result = scan_sync_markers()
    self.assertEqual(result, {})

  def test_single_marker(self):
    """Finds a single upstream sync marker."""
    repo = self.ws_dir / "ws" / "myrepo"
    repo.mkdir(parents=True)
    (repo / ".upstream-sync").write_text(
      "2026-02-20T10:00:00+00:00 aaa bbb"
      " refs/heads/master\n"
    )
    result = scan_sync_markers()
    self.assertIn("ws", result)
    self.assertEqual(len(result["ws"]), 1)
    repo_name, lines = result["ws"][0]
    self.assertEqual(repo_name, "myrepo")
    self.assertEqual(len(lines), 1)

  def test_multiple_repos(self):
    """Groups markers from multiple repos in same workspace."""
    for name in ("repo-a", "repo-b"):
      repo = self.ws_dir / "ws" / name
      repo.mkdir(parents=True)
      (repo / ".upstream-sync").write_text(
        "2026-02-20T10:00:00+00:00 aaa bbb"
        " refs/heads/master\n"
      )
    result = scan_sync_markers()
    self.assertEqual(len(result["ws"]), 2)

  def test_multiple_workspaces(self):
    """Finds markers across different workspaces."""
    for ws in ("ws-a", "ws-b"):
      repo = self.ws_dir / ws / "myrepo"
      repo.mkdir(parents=True)
      (repo / ".upstream-sync").write_text(
        "2026-02-20T10:00:00+00:00 aaa bbb"
        " refs/heads/master\n"
      )
    result = scan_sync_markers()
    self.assertIn("ws-a", result)
    self.assertIn("ws-b", result)

  def test_empty_marker_skipped(self):
    """Empty marker files are ignored."""
    repo = self.ws_dir / "ws" / "myrepo"
    repo.mkdir(parents=True)
    (repo / ".upstream-sync").write_text("")
    result = scan_sync_markers()
    self.assertEqual(result, {})

  def test_nonexistent_dir(self):
    """Returns empty dict when WORKSPACES_DIR doesn't exist."""
    shutil.rmtree(self.ws_dir)
    result = scan_sync_markers()
    self.assertEqual(result, {})


class TestBuildSyncPrompt(unittest.TestCase):
  """Tests for build_sync_prompt()."""

  def setUp(self):
    self.tmpdir = tempfile.mkdtemp()
    self.ws_dir = Path(self.tmpdir) / "workspaces"
    self.ws_dir.mkdir()
    self.patch = mock.patch(
      "bin.pipeline_watch.WORKSPACES_DIR", self.ws_dir,
    )
    self.patch.start()

  def tearDown(self):
    self.patch.stop()
    shutil.rmtree(self.tmpdir)

  @mock.patch(
    "bin.pipeline_watch.get_log", return_value="log",
  )
  def test_contains_workspace_name(self, _mock_log):
    """Prompt mentions the workspace name."""
    lines = [
      "2026-02-20T10:00:00+00:00 aaa bbb"
      " refs/heads/master",
    ]
    prompt = build_sync_prompt("my-ws", [("myrepo", lines)])
    self.assertIn("my-ws", prompt)

  @mock.patch(
    "bin.pipeline_watch.get_log", return_value="log",
  )
  def test_contains_repo_name(self, _mock_log):
    """Prompt mentions the repo name."""
    lines = [
      "2026-02-20T10:00:00+00:00 aaa bbb"
      " refs/heads/master",
    ]
    prompt = build_sync_prompt("ws", [("myrepo", lines)])
    self.assertIn("## myrepo", prompt)

  @mock.patch(
    "bin.pipeline_watch.get_log", return_value="log",
  )
  def test_contains_merge_and_push_instructions(
    self, _mock_log,
  ):
    """Prompt includes merge and push steps."""
    lines = [
      "2026-02-20T10:00:00+00:00 aaa bbb"
      " refs/heads/master",
    ]
    prompt = build_sync_prompt("ws", [("myrepo", lines)])
    self.assertIn("fetch ~/dev/root/myrepo master", prompt)
    self.assertIn("merge FETCH_HEAD", prompt)
    self.assertIn("git push origin ws", prompt)

  @mock.patch(
    "bin.pipeline_watch.get_log", return_value="log",
  )
  def test_parses_default_branch_from_ref(self, _mock_log):
    """Extracts branch name from refs/heads/<branch>."""
    lines = [
      "2026-02-20T10:00:00+00:00 aaa bbb"
      " refs/heads/develop",
    ]
    prompt = build_sync_prompt("ws", [("myrepo", lines)])
    self.assertIn("fetch ~/dev/root/myrepo develop", prompt)
    self.assertIn("merge FETCH_HEAD", prompt)

  @mock.patch(
    "bin.pipeline_watch.get_log",
    return_value="abc123 fix thing",
  )
  def test_includes_commit_log(self, _mock_log):
    """Prompt includes commit log for updated refs."""
    lines = [
      "2026-02-20T10:00:00+00:00 aaa bbb"
      " refs/heads/master",
    ]
    prompt = build_sync_prompt("ws", [("myrepo", lines)])
    self.assertIn("abc123 fix thing", prompt)

  @mock.patch(
    "bin.pipeline_watch.get_log", return_value="log",
  )
  def test_handles_multiple_repos(self, _mock_log):
    """Prompt covers all repos."""
    lines_a = [
      "2026-02-20T10:00:00+00:00 aaa bbb"
      " refs/heads/master",
    ]
    lines_b = [
      "2026-02-20T10:00:00+00:00 ccc ddd"
      " refs/heads/master",
    ]
    prompt = build_sync_prompt(
      "ws",
      [("repo-a", lines_a), ("repo-b", lines_b)],
    )
    self.assertIn("## repo-a", prompt)
    self.assertIn("## repo-b", prompt)


class TestWriteSyncMarkers(unittest.TestCase):
  """Tests for write_sync_markers()."""

  def setUp(self):
    self.tmpdir = tempfile.mkdtemp()
    self.ws_dir = Path(self.tmpdir) / "workspaces"
    self.ws_dir.mkdir()
    self.patch_ws = mock.patch(
      "bin.pipeline_watch.WORKSPACES_DIR", self.ws_dir,
    )
    self.patch_ws.start()
    # Pre-create a workspace dir with a repo dir.
    self.repo_dir = self.ws_dir / "feat" / "myrepo"
    self.repo_dir.mkdir(parents=True)

  def tearDown(self):
    self.patch_ws.stop()
    shutil.rmtree(self.tmpdir)

  def _mock_workspaces(self, workspaces):
    """Return a patch for list_workspaces."""
    return mock.patch(
      "bin.pipeline_watch.list_workspaces",
      return_value=workspaces,
    )

  def test_writes_marker_for_matching_workspace(self):
    """Writes marker when workspace contains the repo."""
    ws_list = [{
      "name": "feat", "repos": ["myrepo"],
      "branch": "feat", "path": str(self.ws_dir / "feat"),
      "last_active": 0.0,
    }]
    changes = [{
      "repo": "myrepo", "branch": "master",
      "old_ref": "aaa", "new_ref": "bbb",
      "type": "updated",
    }]
    with self._mock_workspaces(ws_list):
      write_sync_markers(changes, {"repos": {}})
    marker = self.repo_dir / ".upstream-sync"
    self.assertTrue(marker.exists())
    content = marker.read_text()
    self.assertIn("aaa", content)
    self.assertIn("bbb", content)
    self.assertIn("refs/heads/master", content)

  def test_skips_workspace_without_repo(self):
    """No marker if workspace doesn't contain the repo."""
    ws_list = [{
      "name": "feat", "repos": ["other-repo"],
      "branch": "feat", "path": str(self.ws_dir / "feat"),
      "last_active": 0.0,
    }]
    changes = [{
      "repo": "myrepo", "branch": "master",
      "old_ref": "aaa", "new_ref": "bbb",
      "type": "updated",
    }]
    with self._mock_workspaces(ws_list):
      write_sync_markers(changes, {"repos": {}})
    marker = self.repo_dir / ".upstream-sync"
    self.assertFalse(marker.exists())

  def test_skips_workspace_on_default_branch(self):
    """No marker if workspace branch matches the change."""
    ws_list = [{
      "name": "master", "repos": ["myrepo"],
      "branch": "master",
      "path": str(self.ws_dir / "master"),
      "last_active": 0.0,
    }]
    (self.ws_dir / "master" / "myrepo").mkdir(parents=True)
    changes = [{
      "repo": "myrepo", "branch": "master",
      "old_ref": "aaa", "new_ref": "bbb",
      "type": "updated",
    }]
    with self._mock_workspaces(ws_list):
      write_sync_markers(changes, {"repos": {}})
    marker = (
      self.ws_dir / "master" / "myrepo" / ".upstream-sync"
    )
    self.assertFalse(marker.exists())

  def test_skips_deleted_changes(self):
    """No marker for deleted branch changes."""
    ws_list = [{
      "name": "feat", "repos": ["myrepo"],
      "branch": "feat", "path": str(self.ws_dir / "feat"),
      "last_active": 0.0,
    }]
    changes = [{
      "repo": "myrepo", "branch": "master",
      "old_ref": "aaa", "new_ref": None,
      "type": "deleted",
    }]
    with self._mock_workspaces(ws_list):
      write_sync_markers(changes, {"repos": {}})
    marker = self.repo_dir / ".upstream-sync"
    self.assertFalse(marker.exists())

  def test_appends_to_existing_marker(self):
    """Appends new line to existing marker file."""
    ws_list = [{
      "name": "feat", "repos": ["myrepo"],
      "branch": "feat", "path": str(self.ws_dir / "feat"),
      "last_active": 0.0,
    }]
    marker = self.repo_dir / ".upstream-sync"
    marker.write_text(
      "2026-02-20T09:00:00+00:00 xxx yyy"
      " refs/heads/master\n"
    )
    changes = [{
      "repo": "myrepo", "branch": "master",
      "old_ref": "aaa", "new_ref": "bbb",
      "type": "updated",
    }]
    with self._mock_workspaces(ws_list):
      write_sync_markers(changes, {"repos": {}})
    lines = marker.read_text().strip().splitlines()
    self.assertEqual(len(lines), 2)

  def test_multiple_workspaces_same_repo(self):
    """Both workspaces get markers for the same repo."""
    (self.ws_dir / "feat-b" / "myrepo").mkdir(parents=True)
    ws_list = [
      {
        "name": "feat", "repos": ["myrepo"],
        "branch": "feat",
        "path": str(self.ws_dir / "feat"),
        "last_active": 0.0,
      },
      {
        "name": "feat-b", "repos": ["myrepo"],
        "branch": "feat-b",
        "path": str(self.ws_dir / "feat-b"),
        "last_active": 0.0,
      },
    ]
    changes = [{
      "repo": "myrepo", "branch": "master",
      "old_ref": "aaa", "new_ref": "bbb",
      "type": "updated",
    }]
    with self._mock_workspaces(ws_list):
      write_sync_markers(changes, {"repos": {}})
    m1 = self.repo_dir / ".upstream-sync"
    m2 = (
      self.ws_dir / "feat-b" / "myrepo" / ".upstream-sync"
    )
    self.assertTrue(m1.exists())
    self.assertTrue(m2.exists())


class TestEventLog(unittest.TestCase):
  """Tests for log_events() and load_events()."""

  def setUp(self):
    self.tmpdir = tempfile.mkdtemp()
    self.state_dir = Path(self.tmpdir) / "state"
    self.state_dir.mkdir()
    self.events_file = self.state_dir / "events.json"
    self.patch_state = mock.patch(
      "bin.pipeline_watch.STATE_DIR", self.state_dir,
    )
    self.patch_events = mock.patch(
      "bin.pipeline_watch.EVENTS_FILE", self.events_file,
    )
    self.patch_state.start()
    self.patch_events.start()

  def tearDown(self):
    self.patch_state.stop()
    self.patch_events.stop()
    shutil.rmtree(self.tmpdir)

  def test_log_and_load(self):
    """Events round-trip through log/load."""
    events = [
      {"stage": "ws/test", "repos": "r",
       "event": "triggered"},
    ]
    log_events(events)
    loaded = load_events()
    self.assertEqual(len(loaded), 1)
    self.assertEqual(loaded[0]["stage"], "ws/test")

  def test_cap_at_max(self):
    """Event log is capped at MAX_EVENTS."""
    events = [
      {"stage": f"ws/s{i}", "repos": "r",
       "event": "triggered", "time": "00:00:00"}
      for i in range(MAX_EVENTS + 50)
    ]
    log_events(events)
    loaded = load_events()
    self.assertEqual(len(loaded), MAX_EVENTS)

  def test_load_empty_file(self):
    """Returns empty list when file doesn't exist."""
    loaded = load_events()
    self.assertEqual(loaded, [])

  def test_corrupt_json(self):
    """Returns empty list on corrupt JSON."""
    self.events_file.write_text("not valid json{{{")
    loaded = load_events()
    self.assertEqual(loaded, [])

  def test_non_list_json(self):
    """Returns empty list when JSON is not a list."""
    self.events_file.write_text('{"key": "value"}')
    loaded = load_events()
    self.assertEqual(loaded, [])

  def test_appends_to_existing(self):
    """New events are prepended to existing log."""
    log_events([
      {"stage": "ws/a", "repos": "r",
       "event": "triggered"},
    ])
    log_events([
      {"stage": "ws/b", "repos": "r",
       "event": "triggered"},
    ])
    loaded = load_events()
    self.assertEqual(len(loaded), 2)
    # Newest first.
    self.assertEqual(loaded[0]["stage"], "ws/b")
    self.assertEqual(loaded[1]["stage"], "ws/a")

  def test_noop_empty_events(self):
    """No file written when events list is empty."""
    log_events([])
    self.assertFalse(self.events_file.exists())

  def test_adds_timestamp(self):
    """Events without time get a timestamp."""
    log_events([
      {"stage": "ws/a", "repos": "r",
       "event": "triggered"},
    ])
    loaded = load_events()
    self.assertIn("time", loaded[0])


class TestRetriggerPrStage(unittest.TestCase):
  """Tests for _retrigger_pr_stage()."""

  def setUp(self):
    self.tmpdir = tempfile.mkdtemp()
    self.stages_dir = Path(self.tmpdir) / "stages"
    self.stages_dir.mkdir()
    self.patch = mock.patch(
      "bin.pipeline_watch.STAGES_DIR", self.stages_dir,
    )
    self.patch.start()

  def tearDown(self):
    self.patch.stop()
    shutil.rmtree(self.tmpdir)

  def test_writes_markers_when_pr_stage_exists(self):
    """Writes .pipeline-push in each PR stage repo."""
    pr_repo = self.stages_dir / "ws" / "pr" / "myrepo"
    pr_repo.mkdir(parents=True)
    events = _retrigger_pr_stage("ws")
    self.assertEqual(len(events), 1)
    self.assertEqual(events[0]["stage"], "ws/pr")
    self.assertEqual(events[0]["event"], "retrigger")
    marker = pr_repo / ".pipeline-push"
    self.assertTrue(marker.exists())
    content = marker.read_text()
    self.assertIn("refs/heads/ws", content)

  def test_noop_when_no_pr_stage(self):
    """Returns empty when PR stage doesn't exist."""
    # Only a test stage exists.
    (self.stages_dir / "ws" / "test" / "myrepo").mkdir(
      parents=True,
    )
    events = _retrigger_pr_stage("ws")
    self.assertEqual(events, [])

  def test_multiple_repos(self):
    """Writes markers for all repos in PR stage."""
    for name in ("repo-a", "repo-b"):
      (self.stages_dir / "ws" / "pr" / name).mkdir(
        parents=True,
      )
    events = _retrigger_pr_stage("ws")
    self.assertEqual(len(events), 1)
    self.assertIn("repo-a", events[0]["repos"])
    self.assertIn("repo-b", events[0]["repos"])
    for name in ("repo-a", "repo-b"):
      marker = (
        self.stages_dir / "ws" / "pr" / name
        / ".pipeline-push"
      )
      self.assertTrue(marker.exists())


class TestDetectStageResult(unittest.TestCase):
  """Tests for _detect_stage_result()."""

  def setUp(self):
    self.tmpdir = tempfile.mkdtemp()
    self.stages_dir = Path(self.tmpdir) / "stages"
    self.stages_dir.mkdir()
    self.patch = mock.patch(
      "bin.pipeline_watch.STAGES_DIR", self.stages_dir,
    )
    self.patch.start()

  def tearDown(self):
    self.patch.stop()
    shutil.rmtree(self.tmpdir)

  @mock.patch(
    "bin.pipeline_watch.get_pipeline_stages",
    return_value=["test", "review"],
  )
  def test_non_terminal_passed(self, _mock_pipeline):
    """Non-terminal stage with markers in next = passed."""
    # Create next stage with a pipeline-push marker.
    next_repo = (
      self.stages_dir / "ws" / "review" / "myrepo"
    )
    next_repo.mkdir(parents=True)
    (next_repo / ".pipeline-push").write_text("data")
    result = _detect_stage_result("ws", "test")
    self.assertEqual(result, "passed")

  @mock.patch(
    "bin.pipeline_watch.get_pipeline_stages",
    return_value=["test", "review"],
  )
  def test_non_terminal_failed(self, _mock_pipeline):
    """Non-terminal stage without markers in next = failed."""
    # Create next stage but no marker.
    next_repo = (
      self.stages_dir / "ws" / "review" / "myrepo"
    )
    next_repo.mkdir(parents=True)
    result = _detect_stage_result("ws", "test")
    self.assertEqual(result, "failed")

  @mock.patch(
    "bin.pipeline_watch.get_pipeline_stages",
    return_value=["test", "review"],
  )
  def test_terminal_stage_passed(self, _mock_pipeline):
    """Terminal stage (last in chain) = passed."""
    result = _detect_stage_result("ws", "review")
    self.assertEqual(result, "passed")

  @mock.patch(
    "bin.pipeline_watch.get_pipeline_stages",
    return_value=[],
  )
  def test_unknown_role_returns_none(
    self, _mock_pipeline,
  ):
    """Role not in pipeline returns None."""
    result = _detect_stage_result("ws", "test")
    self.assertIsNone(result)

  @mock.patch(
    "bin.pipeline_watch.get_pipeline_stages",
    return_value=["test", "review"],
  )
  def test_next_stage_dir_missing_is_failed(
    self, _mock_pipeline,
  ):
    """Non-terminal with missing next stage dir = failed."""
    result = _detect_stage_result("ws", "test")
    self.assertEqual(result, "failed")


if __name__ == "__main__":
  unittest.main()
