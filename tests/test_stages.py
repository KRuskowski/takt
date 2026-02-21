"""Tests for the generic stage system."""

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


class TestParsePipelineRoles(unittest.TestCase):
  """Tests for parse_pipeline_roles()."""

  def test_parses_all_roles(self):
    """All 7 roles are extracted from pipeline_roles.md."""
    from lib.config import parse_pipeline_roles
    roles = parse_pipeline_roles()
    expected = {
      "feature", "test", "review", "docs",
      "refactor", "pr", "deploy_qa",
    }
    self.assertEqual(set(roles.keys()), expected)

  def test_role_snippets_not_empty(self):
    """Each role has a non-empty snippet."""
    from lib.config import parse_pipeline_roles
    roles = parse_pipeline_roles()
    for name, snippet in roles.items():
      self.assertTrue(
        len(snippet) > 10,
        f"Role '{name}' has too-short snippet",
      )

  def test_snippets_exclude_separators(self):
    """Snippets don't contain --- separators."""
    from lib.config import parse_pipeline_roles
    roles = parse_pipeline_roles()
    for name, snippet in roles.items():
      for line in snippet.splitlines():
        self.assertNotEqual(
          line.strip(), "---",
          f"Role '{name}' contains separator",
        )


class TestSlugifyRole(unittest.TestCase):
  """Tests for _slugify_role()."""

  def test_simple_name(self):
    from lib.config import _slugify_role
    self.assertEqual(_slugify_role("Feature Agent"), "feature")

  def test_slash_name(self):
    from lib.config import _slugify_role
    self.assertEqual(
      _slugify_role("Deploy/QA Agent"), "deploy_qa",
    )

  def test_no_agent_suffix(self):
    from lib.config import _slugify_role
    self.assertEqual(_slugify_role("Test"), "test")

  def test_multi_word(self):
    from lib.config import _slugify_role
    self.assertEqual(
      _slugify_role("Code Review Agent"), "code_review",
    )


class TestStageOperations(unittest.TestCase):
  """Integration tests for stage create/delete/list/pipeline.

  Uses a temporary directory as ORCH_BASE_DIR with real git
  repos.
  """

  def setUp(self):
    self.tmpdir = tempfile.mkdtemp()
    self.base_dir = Path(self.tmpdir)

    # Patch BASE_DIR and derived dirs.
    self.patches = []
    for attr, subdir in [
      ("BASE_DIR", ""),
      ("ROOT_DIR", "root"),
      ("STAGES_DIR", "stages"),
      ("WORKSPACES_DIR", "workspaces"),
    ]:
      p = mock.patch(
        f"lib.config.{attr}",
        self.base_dir / subdir if subdir else self.base_dir,
      )
      p.start()
      self.patches.append(p)

    # Also patch in workspace_ops which imports at top level.
    for attr, subdir in [
      ("STAGES_DIR", "stages"),
      ("WORKSPACES_DIR", "workspaces"),
    ]:
      p = mock.patch(
        f"lib.workspace_ops.{attr}",
        self.base_dir / subdir if subdir else self.base_dir,
      )
      p.start()
      self.patches.append(p)

    # Create dirs.
    (self.base_dir / "root").mkdir()
    (self.base_dir / "stages").mkdir()
    (self.base_dir / "workspaces").mkdir()

    # Create a bare-ish root repo.
    self.repo_name = "test-repo"
    repo_path = self.base_dir / "root" / self.repo_name
    repo_path.mkdir(parents=True)
    subprocess.run(
      ["git", "init"], cwd=repo_path,
      capture_output=True, check=True,
    )
    subprocess.run(
      ["git", "commit", "--allow-empty", "-m", "init"],
      cwd=repo_path, capture_output=True, check=True,
      env={**os.environ, "GIT_COMMITTER_NAME": "test",
           "GIT_AUTHOR_NAME": "test",
           "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_AUTHOR_EMAIL": "t@t"},
    )

    # Mock repos config.
    self.repos_config_patch = mock.patch(
      "lib.workspace_ops.load_repos_config",
      return_value={
        "repos": {
          self.repo_name: {
            "path": self.repo_name,
            "default_branch": "main",
            "push_order": 1,
          },
        },
      },
    )
    self.repos_config_patch.start()
    self.patches.append(self.repos_config_patch)

    # Mock validate_repo to always return True.
    self.validate_patch = mock.patch(
      "lib.workspace_ops.validate_repo",
      return_value=True,
    )
    self.validate_patch.start()
    self.patches.append(self.validate_patch)

    # Mock CONTEXT_DIR to a non-existent path (skip copies).
    ctx_patch = mock.patch(
      "lib.workspace_ops.CONTEXT_DIR",
      self.base_dir / "nonexistent_context",
    )
    ctx_patch.start()
    self.patches.append(ctx_patch)

  def tearDown(self):
    for p in self.patches:
      p.stop()
    shutil.rmtree(self.tmpdir)

  def _create_workspace(self, name="test-ws"):
    """Helper to create a workspace."""
    from lib.workspace_ops import create_workspace
    return create_workspace(name, [self.repo_name])

  def test_create_stage(self):
    """Creating a stage produces the expected directory."""
    from lib.workspace_ops import create_stage
    self._create_workspace()
    stage_dir = create_stage("test-ws", "test")
    self.assertTrue(stage_dir.exists())
    self.assertTrue(
      (stage_dir / self.repo_name / ".git").exists(),
    )
    self.assertTrue((stage_dir / "CLAUDE.md").exists())

  def test_create_stage_unknown_role(self):
    """Creating a stage with unknown role raises ValueError."""
    from lib.workspace_ops import create_stage
    self._create_workspace()
    with self.assertRaises(ValueError):
      create_stage("test-ws", "nonexistent_role")

  def test_create_stage_no_workspace(self):
    """Creating a stage for missing workspace raises error."""
    from lib.workspace_ops import create_stage
    with self.assertRaises(FileNotFoundError):
      create_stage("no-such-ws", "test")

  def test_create_duplicate_stage(self):
    """Creating same stage twice raises FileExistsError."""
    from lib.workspace_ops import create_stage
    self._create_workspace()
    create_stage("test-ws", "test")
    with self.assertRaises(FileExistsError):
      create_stage("test-ws", "test")

  def test_delete_stage(self):
    """Deleting a stage removes the directory."""
    from lib.workspace_ops import create_stage, delete_stage
    self._create_workspace()
    stage_dir = create_stage("test-ws", "test")
    self.assertTrue(stage_dir.exists())
    delete_stage("test-ws", "test")
    self.assertFalse(stage_dir.exists())

  def test_delete_nonexistent_stage(self):
    """Deleting missing stage raises FileNotFoundError."""
    from lib.workspace_ops import delete_stage
    self._create_workspace()
    with self.assertRaises(FileNotFoundError):
      delete_stage("test-ws", "test")

  def test_list_stages_empty(self):
    """list_stages returns empty when no stages exist."""
    from lib.workspace_ops import list_stages
    self.assertEqual(list_stages(), [])

  def test_list_stages(self):
    """list_stages returns created stages."""
    from lib.workspace_ops import create_stage, list_stages
    self._create_workspace()
    create_stage("test-ws", "test")
    create_stage("test-ws", "review")
    stages = list_stages()
    self.assertEqual(len(stages), 2)
    roles = {s["role"] for s in stages}
    self.assertEqual(roles, {"test", "review"})

  def test_list_stages_filtered(self):
    """list_stages with workspace filter works."""
    from lib.workspace_ops import create_stage, list_stages
    self._create_workspace("ws-a")
    self._create_workspace("ws-b")
    create_stage("ws-a", "test")
    create_stage("ws-b", "review")
    a_stages = list_stages("ws-a")
    self.assertEqual(len(a_stages), 1)
    self.assertEqual(a_stages[0]["role"], "test")

  def test_pipeline_ordering(self):
    """Pipeline tracks stage order."""
    from lib.workspace_ops import (
      create_stage,
      get_pipeline,
    )
    self._create_workspace()
    create_stage("test-ws", "test")
    create_stage("test-ws", "review")
    pipeline = get_pipeline("test-ws")
    self.assertEqual(
      pipeline["stages"], ["test", "review"],
    )
    self.assertEqual(
      pipeline["chain"],
      "workspace -> test -> review -> root",
    )

  def test_pipeline_after_delete(self):
    """Pipeline updates after stage removal."""
    from lib.workspace_ops import (
      create_stage,
      delete_stage,
      get_pipeline,
    )
    self._create_workspace()
    create_stage("test-ws", "test")
    create_stage("test-ws", "review")
    delete_stage("test-ws", "test")
    pipeline = get_pipeline("test-ws")
    self.assertEqual(pipeline["stages"], ["review"])

  def test_remote_chain(self):
    """Remote chain is correct after creating stages."""
    from lib.workspace_ops import create_stage
    from lib.git_utils import run_git
    self._create_workspace()
    create_stage("test-ws", "test")
    create_stage("test-ws", "review")

    ws_dir = self.base_dir / "workspaces" / "test-ws"
    test_dir = self.base_dir / "stages" / "test-ws" / "test"
    review_dir = (
      self.base_dir / "stages" / "test-ws" / "review"
    )

    # Workspace origin -> test stage.
    ws_origin = run_git(
      ["remote", "get-url", "origin"],
      cwd=ws_dir / self.repo_name,
    )
    self.assertEqual(
      ws_origin, str(test_dir / self.repo_name),
    )

    # Test stage origin -> review stage.
    test_origin = run_git(
      ["remote", "get-url", "origin"],
      cwd=test_dir / self.repo_name,
    )
    self.assertEqual(
      test_origin, str(review_dir / self.repo_name),
    )

    # Review stage origin -> root.
    review_origin = run_git(
      ["remote", "get-url", "origin"],
      cwd=review_dir / self.repo_name,
    )
    root_path = self.base_dir / "root" / self.repo_name
    self.assertEqual(review_origin, str(root_path))

  def test_remote_chain_after_delete(self):
    """Remote chain updates after removing a middle stage."""
    from lib.workspace_ops import create_stage, delete_stage
    from lib.git_utils import run_git
    self._create_workspace()
    create_stage("test-ws", "test")
    create_stage("test-ws", "review")
    delete_stage("test-ws", "test")

    ws_dir = self.base_dir / "workspaces" / "test-ws"
    review_dir = (
      self.base_dir / "stages" / "test-ws" / "review"
    )

    # Workspace origin -> review stage (test removed).
    ws_origin = run_git(
      ["remote", "get-url", "origin"],
      cwd=ws_dir / self.repo_name,
    )
    self.assertEqual(
      ws_origin, str(review_dir / self.repo_name),
    )

  def test_claude_md_has_role_snippet(self):
    """Stage CLAUDE.md contains the role snippet."""
    from lib.workspace_ops import create_stage
    self._create_workspace()
    stage_dir = create_stage("test-ws", "test")
    content = (stage_dir / "CLAUDE.md").read_text()
    self.assertIn("test agent", content.lower())

  def test_stage_claude_md_has_stage_git_rules(self):
    """Stage CLAUDE.md has stage-aware git rules."""
    from lib.workspace_ops import create_stage, refresh_stage
    self._create_workspace()
    create_stage("test-ws", "test")
    create_stage("test-ws", "review")
    # Refresh test stage so its CLAUDE.md reflects the
    # full pipeline (review was added after test).
    refresh_stage("test-ws", "test")
    test_dir = (
      self.base_dir / "stages" / "test-ws" / "test"
    )
    content = (test_dir / "CLAUDE.md").read_text()
    # Should mention it's the test stage.
    self.assertIn("**test** stage", content)
    # Should show pipeline chain with test highlighted.
    self.assertIn("[test]", content)
    # Should mention origin points to review.
    self.assertIn("review", content)

  def test_stage_claude_md_last_stage_points_to_root(self):
    """Last stage's origin description mentions root repo."""
    from lib.workspace_ops import create_stage
    self._create_workspace()
    stage_dir = create_stage("test-ws", "review")
    content = (stage_dir / "CLAUDE.md").read_text()
    self.assertIn("root repo", content)

  def test_stage_claude_md_has_pipeline_section(self):
    """Stage CLAUDE.md includes incoming changes section."""
    from lib.workspace_ops import create_stage
    self._create_workspace()
    stage_dir = create_stage("test-ws", "test")
    content = (stage_dir / "CLAUDE.md").read_text()
    self.assertIn("Incoming Changes", content)
    self.assertIn(".pipeline-push", content)

  def test_workspace_claude_md_no_pipeline_section(self):
    """Workspace CLAUDE.md does NOT have pipeline section."""
    self._create_workspace()
    ws_dir = self.base_dir / "workspaces" / "test-ws"
    content = (ws_dir / "CLAUDE.md").read_text()
    self.assertNotIn("Incoming Changes", content)

  def test_push_hook_installed(self):
    """post-receive hook is installed in stage repos."""
    from lib.workspace_ops import create_stage
    self._create_workspace()
    stage_dir = create_stage("test-ws", "test")
    hook = (
      stage_dir / self.repo_name / ".git"
      / "hooks" / "post-receive"
    )
    self.assertTrue(hook.exists())
    self.assertTrue(os.access(hook, os.X_OK))
    content = hook.read_text()
    self.assertIn(".pipeline-push", content)

  def test_refresh_stage(self):
    """refresh_stage re-generates CLAUDE.md and installs hooks."""
    from lib.workspace_ops import create_stage, refresh_stage
    self._create_workspace()
    stage_dir = create_stage("test-ws", "test")

    # Delete CLAUDE.md and hook, then refresh.
    (stage_dir / "CLAUDE.md").unlink()
    hook = (
      stage_dir / self.repo_name / ".git"
      / "hooks" / "post-receive"
    )
    hook.unlink()

    refresh_stage("test-ws", "test")
    self.assertTrue((stage_dir / "CLAUDE.md").exists())
    self.assertTrue(hook.exists())
    content = (stage_dir / "CLAUDE.md").read_text()
    self.assertIn("**test** stage", content)

  def test_refresh_nonexistent_stage(self):
    """refresh_stage raises FileNotFoundError for missing stage."""
    from lib.workspace_ops import refresh_stage
    self._create_workspace()
    with self.assertRaises(FileNotFoundError):
      refresh_stage("test-ws", "test")


if __name__ == "__main__":
  unittest.main()
