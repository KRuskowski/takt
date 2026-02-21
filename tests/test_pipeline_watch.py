"""Tests for pipeline_watch marker scanning and stage triggering."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from bin.pipeline_watch import (
  _kitty_tab_exists,
  _prune_finished_tabs,
  _scan_and_sync,
  _scan_and_trigger,
  build_sync_prompt,
  build_trigger_prompt,
  launch_in_kitty,
  scan_markers,
  scan_sync_markers,
  write_sync_markers,
)

FAKE_SOCKET = "unix:/tmp/kitty-pipeline-99999"


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


class TestKittyHelpers(unittest.TestCase):
  """Tests for kitty tab detection."""

  @mock.patch("bin.pipeline_watch._find_kitty_socket",
              return_value=FAKE_SOCKET)
  @mock.patch("bin.pipeline_watch.subprocess.run")
  def test_tab_exists(self, mock_run, _mock_sock):
    """Detects existing kitty tab by title."""
    ls_data = [{"tabs": [{"title": "ws/test"}]}]
    mock_run.return_value = mock.Mock(
      returncode=0, stdout=json.dumps(ls_data),
    )
    self.assertTrue(_kitty_tab_exists("ws/test"))
    mock_run.assert_called_once_with(
      ["kitten", "@", "--to", FAKE_SOCKET, "ls"],
      capture_output=True, text=True,
    )

  @mock.patch("bin.pipeline_watch._find_kitty_socket",
              return_value=FAKE_SOCKET)
  @mock.patch("bin.pipeline_watch.subprocess.run")
  def test_tab_not_exists(self, mock_run, _mock_sock):
    """Returns False when tab title is absent."""
    ls_data = [{"tabs": [{"title": "ws/review"}]}]
    mock_run.return_value = mock.Mock(
      returncode=0, stdout=json.dumps(ls_data),
    )
    self.assertFalse(_kitty_tab_exists("ws/test"))

  @mock.patch("bin.pipeline_watch._find_kitty_socket",
              return_value=FAKE_SOCKET)
  @mock.patch("bin.pipeline_watch.subprocess.run")
  def test_ls_fails(self, mock_run, _mock_sock):
    """Returns False when kitten ls fails."""
    mock_run.return_value = mock.Mock(
      returncode=1, stdout="", stderr="no socket",
    )
    self.assertFalse(_kitty_tab_exists("ws/test"))

  @mock.patch("bin.pipeline_watch._find_kitty_socket",
              return_value=FAKE_SOCKET)
  @mock.patch("bin.pipeline_watch.subprocess.run")
  def test_invalid_json(self, mock_run, _mock_sock):
    """Returns False when ls output is not valid JSON."""
    mock_run.return_value = mock.Mock(
      returncode=0, stdout="not json",
    )
    self.assertFalse(_kitty_tab_exists("ws/test"))

  @mock.patch("bin.pipeline_watch._find_kitty_socket",
              return_value=FAKE_SOCKET)
  @mock.patch("bin.pipeline_watch.subprocess.run")
  def test_multiple_os_windows(self, mock_run, _mock_sock):
    """Finds tab across multiple OS windows."""
    ls_data = [
      {"tabs": [{"title": "shell"}]},
      {"tabs": [{"title": "ws/test"}]},
    ]
    mock_run.return_value = mock.Mock(
      returncode=0, stdout=json.dumps(ls_data),
    )
    self.assertTrue(_kitty_tab_exists("ws/test"))

  @mock.patch("bin.pipeline_watch._find_kitty_socket",
              return_value=None)
  def test_no_socket(self, _mock_sock):
    """Returns False when no kitty socket is found."""
    self.assertFalse(_kitty_tab_exists("ws/test"))


class TestLaunchInKitty(unittest.TestCase):
  """Tests for launch_in_kitty()."""

  def setUp(self):
    self.tmpdir = tempfile.mkdtemp()

  def tearDown(self):
    shutil.rmtree(self.tmpdir)

  @mock.patch("bin.pipeline_watch._find_kitty_socket",
              return_value=FAKE_SOCKET)
  @mock.patch("bin.pipeline_watch.subprocess.run")
  def test_launches_tab(self, mock_run, _mock_sock):
    """Launches claude in a new kitty tab."""
    # _kitty_tab_exists (no match), launch, set-tab-color,
    # focus-tab.
    ls_data = [{"tabs": [{"title": "shell"}]}]
    ok = mock.Mock(returncode=0, stdout="", stderr="")
    mock_run.side_effect = [
      mock.Mock(returncode=0, stdout=json.dumps(ls_data)),
      ok, ok, ok,
    ]
    stage = Path(self.tmpdir) / "stage"
    stage.mkdir()
    # Place a stale done marker to verify it gets cleaned.
    stale = stage / ".agent-done"
    stale.touch()
    launch_in_kitty("ws", "test", stage, "do stuff")
    # Stale marker should be removed before launch.
    self.assertFalse(stale.exists())
    launch_call = mock_run.call_args_list[1][0][0]
    self.assertEqual(launch_call[:4], [
      "kitten", "@", "--to", FAKE_SOCKET,
    ])
    self.assertIn("--tab-title", launch_call)
    idx = launch_call.index("--tab-title")
    self.assertEqual(launch_call[idx + 1], "ws/test")
    # Verify zsh -ic with unset + claude + touch.
    self.assertEqual(launch_call[-3], "zsh")
    self.assertEqual(launch_call[-2], "-ic")
    shell_cmd = launch_call[-1]
    self.assertIn("unset CLAUDECODE", shell_cmd)
    self.assertIn("claude", shell_cmd)
    self.assertIn("do stuff", shell_cmd)
    self.assertIn("touch", shell_cmd)
    self.assertIn(".agent-done", shell_cmd)
    # Verify set-tab-color call (role "test" has a color).
    color_call = mock_run.call_args_list[2][0][0]
    self.assertIn("set-tab-color", color_call)
    self.assertIn("active_bg=#5a4b27", color_call)
    # Verify focus-tab call.
    focus_call = mock_run.call_args_list[3][0][0]
    self.assertIn("focus-tab", focus_call)

  @mock.patch("bin.pipeline_watch._find_kitty_socket",
              return_value=FAKE_SOCKET)
  @mock.patch("bin.pipeline_watch.subprocess.run")
  def test_skips_duplicate_tab(self, mock_run, _mock_sock):
    """Skips launch when tab already exists."""
    ls_data = [{"tabs": [{"title": "ws/test"}]}]
    mock_run.return_value = mock.Mock(
      returncode=0, stdout=json.dumps(ls_data),
    )
    launch_in_kitty("ws", "test", Path("/tmp/stage"), "hi")
    # Only the ls call, no launch.
    self.assertEqual(mock_run.call_count, 1)

  @mock.patch("bin.pipeline_watch._find_kitty_socket",
              return_value=FAKE_SOCKET)
  @mock.patch("bin.pipeline_watch.subprocess.run")
  def test_raises_on_failure(self, mock_run, _mock_sock):
    """Raises RuntimeError when kitty launch fails."""
    ls_data = [{"tabs": []}]
    mock_run.side_effect = [
      mock.Mock(returncode=0, stdout=json.dumps(ls_data)),
      mock.Mock(returncode=1, stdout="", stderr="no socket"),
    ]
    with self.assertRaises(RuntimeError):
      launch_in_kitty(
        "ws", "test", Path("/tmp/stage"), "hi",
      )

  @mock.patch("bin.pipeline_watch._find_kitty_socket",
              return_value=None)
  def test_raises_no_socket(self, _mock_sock):
    """Raises RuntimeError when no kitty socket is found."""
    with self.assertRaises(RuntimeError):
      launch_in_kitty(
        "ws", "test", Path("/tmp/stage"), "hi",
      )


class TestScanAndTrigger(unittest.TestCase):
  """Tests for _scan_and_trigger()."""

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

  @mock.patch("bin.pipeline_watch.launch_in_kitty")
  def test_deletes_markers_after_trigger(self, mock_launch):
    """Marker files are deleted after triggering."""
    repo = self.stages_dir / "ws" / "test" / "myrepo"
    repo.mkdir(parents=True)
    marker = repo / ".pipeline-push"
    marker.write_text(
      "2026-02-20T10:00:00+00:00 aaa bbb refs/heads/ws\n"
    )
    _scan_and_trigger()
    self.assertFalse(marker.exists())
    mock_launch.assert_called_once()

  @mock.patch("bin.pipeline_watch.launch_in_kitty")
  def test_noop_without_markers(self, mock_launch):
    """Does nothing when no markers exist."""
    _scan_and_trigger()
    mock_launch.assert_not_called()

  @mock.patch("bin.pipeline_watch.launch_in_kitty")
  def test_passes_stage_dir(self, mock_launch):
    """Passes correct stage dir to launch_in_kitty."""
    repo = self.stages_dir / "ws" / "test" / "myrepo"
    repo.mkdir(parents=True)
    (repo / ".pipeline-push").write_text(
      "2026-02-20T10:00:00+00:00 aaa bbb refs/heads/ws\n"
    )
    _scan_and_trigger()
    call_args = mock_launch.call_args
    self.assertEqual(call_args[0][0], "ws")
    self.assertEqual(call_args[0][1], "test")
    self.assertEqual(
      call_args[0][2], self.stages_dir / "ws" / "test",
    )


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


class TestScanAndSync(unittest.TestCase):
  """Tests for _scan_and_sync()."""

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

  @mock.patch("bin.pipeline_watch.launch_in_kitty")
  def test_deletes_markers_and_launches(self, mock_launch):
    """Markers deleted and agent launched on trigger."""
    repo = self.ws_dir / "ws" / "myrepo"
    repo.mkdir(parents=True)
    marker = repo / ".upstream-sync"
    marker.write_text(
      "2026-02-20T10:00:00+00:00 aaa bbb"
      " refs/heads/master\n"
    )
    _scan_and_sync()
    self.assertFalse(marker.exists())
    mock_launch.assert_called_once()

  @mock.patch("bin.pipeline_watch.launch_in_kitty")
  def test_noop_without_markers(self, mock_launch):
    """Does nothing when no markers exist."""
    _scan_and_sync()
    mock_launch.assert_not_called()

  @mock.patch("bin.pipeline_watch.launch_in_kitty")
  def test_passes_workspace_dir_and_sync_role(
    self, mock_launch,
  ):
    """Passes workspace dir and role='sync'."""
    repo = self.ws_dir / "ws" / "myrepo"
    repo.mkdir(parents=True)
    (repo / ".upstream-sync").write_text(
      "2026-02-20T10:00:00+00:00 aaa bbb"
      " refs/heads/master\n"
    )
    _scan_and_sync()
    call_args = mock_launch.call_args
    self.assertEqual(call_args[0][0], "ws")
    self.assertEqual(call_args[0][1], "sync")
    self.assertEqual(call_args[0][2], self.ws_dir / "ws")

  @mock.patch("bin.pipeline_watch._kitty_tab_exists")
  @mock.patch("bin.pipeline_watch.launch_in_kitty")
  def test_markers_persist_when_tab_exists(
    self, mock_launch, mock_tab_exists,
  ):
    """Markers preserved when sync tab already running."""
    mock_tab_exists.return_value = True
    repo = self.ws_dir / "ws" / "myrepo"
    repo.mkdir(parents=True)
    marker = repo / ".upstream-sync"
    marker.write_text(
      "2026-02-20T10:00:00+00:00 aaa bbb"
      " refs/heads/master\n"
    )
    _scan_and_sync()
    self.assertTrue(marker.exists())
    mock_launch.assert_not_called()


class TestPruneFinishedTabs(unittest.TestCase):
  """Tests for _prune_finished_tabs()."""

  def setUp(self):
    self.tmpdir = tempfile.mkdtemp()
    self.stages_dir = Path(self.tmpdir) / "stages"
    self.stages_dir.mkdir()
    self.ws_dir = Path(self.tmpdir) / "workspaces"
    self.ws_dir.mkdir()
    self.patch_stages = mock.patch(
      "bin.pipeline_watch.STAGES_DIR", self.stages_dir,
    )
    self.patch_ws = mock.patch(
      "bin.pipeline_watch.WORKSPACES_DIR", self.ws_dir,
    )
    self.patch_stages.start()
    self.patch_ws.start()

  def tearDown(self):
    self.patch_stages.stop()
    self.patch_ws.stop()
    shutil.rmtree(self.tmpdir)

  @mock.patch("bin.pipeline_watch._find_kitty_socket",
              return_value=FAKE_SOCKET)
  @mock.patch("bin.pipeline_watch.subprocess.run")
  def test_prunes_tab_with_done_marker(
    self, mock_run, _mock_sock,
  ):
    """Closes tab and deletes marker when .agent-done exists."""
    stage = self.stages_dir / "ws" / "test"
    stage.mkdir(parents=True)
    (stage / ".agent-done").touch()
    ls_data = [{"tabs": [{"title": "ws/test"}]}]
    ok = mock.Mock(returncode=0, stdout="", stderr="")
    mock_run.side_effect = [
      mock.Mock(returncode=0, stdout=json.dumps(ls_data)),
      ok,
    ]
    pruned = _prune_finished_tabs()
    self.assertEqual(pruned, ["ws/test"])
    # close-tab should have been called.
    close_call = mock_run.call_args_list[1][0][0]
    self.assertIn("close-tab", close_call)
    self.assertIn("title:ws/test", close_call)
    # Marker should be deleted.
    self.assertFalse((stage / ".agent-done").exists())

  @mock.patch("bin.pipeline_watch._find_kitty_socket",
              return_value=FAKE_SOCKET)
  @mock.patch("bin.pipeline_watch.subprocess.run")
  def test_skips_tab_without_marker(
    self, mock_run, _mock_sock,
  ):
    """No close-tab when .agent-done is absent."""
    stage = self.stages_dir / "ws" / "test"
    stage.mkdir(parents=True)
    ls_data = [{"tabs": [{"title": "ws/test"}]}]
    mock_run.return_value = mock.Mock(
      returncode=0, stdout=json.dumps(ls_data),
    )
    pruned = _prune_finished_tabs()
    self.assertEqual(pruned, [])
    # Only the ls call, no close-tab.
    self.assertEqual(mock_run.call_count, 1)

  @mock.patch("bin.pipeline_watch._find_kitty_socket",
              return_value=FAKE_SOCKET)
  @mock.patch("bin.pipeline_watch.subprocess.run")
  def test_skips_non_pipeline_tabs(
    self, mock_run, _mock_sock,
  ):
    """Tabs without '/' in title are ignored."""
    ls_data = [{"tabs": [{"title": "shell"}]}]
    mock_run.return_value = mock.Mock(
      returncode=0, stdout=json.dumps(ls_data),
    )
    pruned = _prune_finished_tabs()
    self.assertEqual(pruned, [])
    # Only the ls call.
    self.assertEqual(mock_run.call_count, 1)

  @mock.patch("bin.pipeline_watch._find_kitty_socket",
              return_value=None)
  def test_noop_without_socket(self, _mock_sock):
    """Returns empty list when no socket found."""
    pruned = _prune_finished_tabs()
    self.assertEqual(pruned, [])

  @mock.patch("bin.pipeline_watch._find_kitty_socket",
              return_value=FAKE_SOCKET)
  @mock.patch("bin.pipeline_watch.subprocess.run")
  def test_sync_role_checks_workspace_dir(
    self, mock_run, _mock_sock,
  ):
    """Sync tab checks WORKSPACES_DIR for .agent-done."""
    ws = self.ws_dir / "ws"
    ws.mkdir(parents=True)
    (ws / ".agent-done").touch()
    ls_data = [{"tabs": [{"title": "ws/sync"}]}]
    ok = mock.Mock(returncode=0, stdout="", stderr="")
    mock_run.side_effect = [
      mock.Mock(returncode=0, stdout=json.dumps(ls_data)),
      ok,
    ]
    pruned = _prune_finished_tabs()
    self.assertEqual(pruned, ["ws/sync"])
    close_call = mock_run.call_args_list[1][0][0]
    self.assertIn("close-tab", close_call)
    self.assertIn("title:ws/sync", close_call)
    self.assertFalse((ws / ".agent-done").exists())


if __name__ == "__main__":
  unittest.main()
