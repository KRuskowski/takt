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
  _scan_and_trigger,
  build_trigger_prompt,
  launch_in_kitty,
  scan_markers,
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

  @mock.patch("bin.pipeline_watch._find_kitty_socket",
              return_value=FAKE_SOCKET)
  @mock.patch("bin.pipeline_watch.subprocess.run")
  def test_launches_tab(self, mock_run, _mock_sock):
    """Launches claude in a new kitty tab."""
    # _kitty_tab_exists (no match), launch.
    ls_data = [{"tabs": [{"title": "shell"}]}]
    mock_run.side_effect = [
      mock.Mock(returncode=0, stdout=json.dumps(ls_data)),
      mock.Mock(returncode=0, stdout="", stderr=""),
    ]
    launch_in_kitty(
      "ws", "test", Path("/tmp/stage"), "do stuff",
    )
    launch_call = mock_run.call_args_list[1][0][0]
    self.assertEqual(launch_call[:4], [
      "kitten", "@", "--to", FAKE_SOCKET,
    ])
    self.assertIn("--tab-title", launch_call)
    idx = launch_call.index("--tab-title")
    self.assertEqual(launch_call[idx + 1], "ws/test")
    # Verify bash -ic with unset + claude.
    self.assertEqual(launch_call[-3], "zsh")
    self.assertEqual(launch_call[-2], "-ic")
    shell_cmd = launch_call[-1]
    self.assertIn("unset CLAUDECODE", shell_cmd)
    self.assertIn("claude", shell_cmd)
    self.assertIn("do stuff", shell_cmd)

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


if __name__ == "__main__":
  unittest.main()
