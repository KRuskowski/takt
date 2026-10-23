"""Tests for the worker daemon's logic.

The full loop is exercised in test_worker_e2e.py against a
real scheduler. These cover the decisions a worker makes on
its own: what it will accept, where it fetches from, and
that it cleans up after itself.
"""

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib import worker


class TestCapabilityGating:
  """What a worker will take on."""

  def test_build_only_worker_refuses_agent_steps(self):
    """A worker without Claude will not take an agent run.

    It must refuse at the gate rather than lease, fail and
    hand back, which two such workers can do to each other
    forever.
    """
    work = {"steps": [
      {"name": "test", "step_type": "agent", "seq": 0},
    ]}
    ok, reason = worker.can_run(
      work, {"agent_steps": False}
    )
    assert not ok
    assert "agent step" in reason

  def test_worker_with_claude_accepts_agent_steps(self):
    """An equipped worker takes the same run."""
    work = {"steps": [
      {"name": "test", "step_type": "agent", "seq": 0},
    ]}
    ok, _ = worker.can_run(work, {"agent_steps": True})
    assert ok

  def test_script_only_run_is_accepted_by_any_worker(self):
    """Build and test work needs no Claude."""
    work = {"steps": [
      {"name": "check_build", "step_type": "script",
       "seq": 0},
    ]}
    ok, _ = worker.can_run(work, {"agent_steps": False})
    assert ok

  def test_unknown_step_type_is_refused(self):
    """An unrecognised step type is not guessed at."""
    work = {"steps": [
      {"name": "weird", "step_type": "quantum", "seq": 0},
    ]}
    ok, reason = worker.can_run(work, {"agent_steps": True})
    assert not ok
    assert "unsupported step types" in reason

  def test_empty_run_is_accepted(self):
    """A run with no steps is not an error."""
    ok, _ = worker.can_run(
      {"steps": []}, {"agent_steps": False}
    )
    assert ok


class TestSchedulerOnlySteps:
  """Delivery stays on the scheduler."""

  def test_push_and_pr_are_scheduler_only(self):
    """A worker must never push or open PRs.

    The scheduler holds the credentials and is the single
    gate on what reaches GitHub.
    """
    assert "push_to_github" in worker.SCHEDULER_ONLY_STEPS
    assert "create_pr" in worker.SCHEDULER_ONLY_STEPS

  def test_build_checks_are_not_scheduler_only(self):
    """CPU-bound work is exactly what should move."""
    for name in (
      "check_build", "check_tests", "merge_upstream"
    ):
      assert name not in worker.SCHEDULER_ONLY_STEPS


class TestRefResolution:
  """Picking the commit for a repo."""

  def test_prefers_the_workspace_branch(self):
    """The workspace name is the branch name."""
    refs = {
      "repo-a:main": "aaa",
      "repo-a:feature-x": "bbb",
    }
    assert worker._ref_for(
      refs, "repo-a", "feature-x"
    ) == "bbb"

  def test_falls_back_to_any_branch_for_the_repo(self):
    """A reference repo on another branch still resolves."""
    refs = {"repo-b:main": "ccc"}
    assert worker._ref_for(
      refs, "repo-b", "feature-x"
    ) == "ccc"

  def test_unknown_repo_is_none(self):
    """A repo with no ref yields None, not a guess."""
    assert worker._ref_for({}, "repo-z", "ws") is None

  def test_repo_name_containing_colon_is_not_confused(self):
    """Splitting on the first colon only."""
    refs = {"org:repo:main": "ddd"}
    assert worker._ref_for(
      refs, "org", "main"
    ) == "ddd"


class TestDiskReporting:
  """Free space, which gates taking work."""

  def test_reports_a_number(self, tmp_path):
    """Free space is reported in MB."""
    mb = worker.free_disk_mb(tmp_path)
    assert isinstance(mb, int)
    assert mb > 0

  def test_missing_path_is_none(self):
    """An unreadable path yields None, not a crash."""
    assert worker.free_disk_mb(
      "/nonexistent/path/xyz"
    ) is None


class TestCapabilityDetection:
  """What the worker reports about itself."""

  def test_reports_cores_and_os(self, tmp_path):
    """The registration payload is populated."""
    caps = worker.detect_capabilities(
      tmp_path, claude_available=False
    )
    assert caps["cores"]
    assert caps["os"]
    assert caps["free_disk_mb"] > 0
    assert caps["capabilities"]["agent_steps"] is False
    assert "cmake" in caps["capabilities"]["toolchains"]

  def test_claude_presence_sets_agent_steps(
    self, tmp_path
  ):
    """Having Claude Code enables agent steps."""
    caps = worker.detect_capabilities(
      tmp_path, claude_available=True
    )
    assert caps["capabilities"]["agent_steps"] is True


class TestMirrorsAndWorktrees:
  """Making the right source present locally."""

  @pytest.fixture
  def upstream(self, tmp_path):
    """An upstream repo with two commits on a branch."""
    up = tmp_path / "upstream"
    up.mkdir()
    g = ["git", "-C", str(up)]
    subprocess.run(
      ["git", "init", "-q", "-b", "main", str(up)],
      check=True, timeout=60,
    )
    subprocess.run(
      g + ["config", "user.email", "t@t"], check=True,
      timeout=60,
    )
    subprocess.run(
      g + ["config", "user.name", "t"], check=True,
      timeout=60,
    )
    shas = []
    for i, text in enumerate(("one", "two")):
      (up / "f.txt").write_text(text + "\n")
      subprocess.run(
        g + ["add", "-A"], check=True, timeout=60
      )
      subprocess.run(
        g + ["commit", "-q", "-m", f"c{i}",
             "--no-gpg-sign"],
        check=True, timeout=60,
      )
      shas.append(subprocess.run(
        g + ["rev-parse", "HEAD"], capture_output=True,
        text=True, timeout=60,
      ).stdout.strip())
    return up, shas

  def test_clones_then_reuses_a_mirror(
    self, tmp_path, upstream
  ):
    """A mirror is cloned once and fetched thereafter.

    Mirrors are cheap to keep and expensive to refetch, so
    a second run must not re-clone a multi-gigabyte repo.
    """
    up, _ = upstream
    mirrors = tmp_path / "mirrors"
    first = worker.ensure_mirror("r", mirrors, str(up))
    assert first.exists()
    marker = first / "takt-was-here"
    marker.write_text("x")
    second = worker.ensure_mirror("r", mirrors, str(up))
    assert second == first
    assert marker.exists(), "mirror was re-cloned"

  def test_materialises_the_exact_commit(
    self, tmp_path, upstream
  ):
    """The worktree is at the SHA the run recorded.

    Not at the branch tip: a requeued run must build the
    same source as the original attempt, even if the branch
    moved in between.
    """
    up, shas = upstream
    work = {
      "run_id": 7, "workspace": "main",
      "repos": ["r"],
      "refs": {"r:main": shas[0]},
    }
    trees = worker.materialise(
      work, tmp_path / "mirrors", tmp_path / "runs",
      lambda repo: str(up),
    )
    head = subprocess.run(
      ["git", "-C", str(trees["r"]), "rev-parse", "HEAD"],
      capture_output=True, text=True, timeout=60,
    ).stdout.strip()
    assert head == shas[0]
    assert (trees["r"] / "f.txt").read_text() == "one\n"

  def test_missing_commit_is_an_error(
    self, tmp_path, upstream
  ):
    """An unreachable commit fails loudly.

    Building the wrong commit silently would be worse than
    failing the run.
    """
    up, _ = upstream
    work = {
      "run_id": 8, "workspace": "main",
      "repos": ["r"],
      "refs": {"r:main": "0" * 40},
    }
    with pytest.raises(
      worker.WorkerError, match="not reachable"
    ):
      worker.materialise(
        work, tmp_path / "mirrors", tmp_path / "runs",
        lambda repo: str(up),
      )

  def test_cleanup_removes_the_worktree(
    self, tmp_path, upstream
  ):
    """Build output does not survive the run.

    An unattended worker has nobody to notice it filling
    up, and build trees are the dominant disk cost.
    """
    up, shas = upstream
    mirrors = tmp_path / "mirrors"
    work = {
      "run_id": 9, "workspace": "main", "repos": ["r"],
      "refs": {"r:main": shas[1]},
    }
    trees = worker.materialise(
      work, mirrors, tmp_path / "runs", lambda r: str(up)
    )
    # Simulate build output inside the tree.
    (trees["r"] / "build").mkdir()
    (trees["r"] / "build" / "big.o").write_bytes(b"\0" * 1024)
    worker.cleanup(trees, mirrors)
    assert not Path(trees["r"]).exists()
    # The mirror survives, since refetching is expensive.
    assert (mirrors / "r.git").exists()


class TestBundleCreation:
  """Sending commits back."""

  @pytest.fixture
  def tree(self, tmp_path):
    """A repo with one commit, acting as a worktree."""
    d = tmp_path / "tree"
    d.mkdir()
    g = ["git", "-C", str(d)]
    subprocess.run(
      ["git", "init", "-q", "-b", "wsbranch", str(d)],
      check=True, timeout=60,
    )
    for k, v in (
      ("user.email", "t@t"), ("user.name", "t")
    ):
      subprocess.run(
        g + ["config", k, v], check=True, timeout=60
      )
    (d / "a.txt").write_text("base\n")
    subprocess.run(
      g + ["add", "-A"], check=True, timeout=60
    )
    subprocess.run(
      g + ["commit", "-q", "-m", "base", "--no-gpg-sign"],
      check=True, timeout=60,
    )
    base = subprocess.run(
      g + ["rev-parse", "HEAD"], capture_output=True,
      text=True, timeout=60,
    ).stdout.strip()
    return d, base

  def test_no_new_commits_means_no_bundle(self, tree):
    """Nothing to deliver returns None.

    Most runs only test and review, committing nothing.
    They must not send an empty delivery.
    """
    d, base = tree
    assert worker.bundle_for(d, "wsbranch", base) is None

  def test_bundles_only_the_new_range(self, tree):
    """A delivery carries the new commits, not the repo."""
    d, base = tree
    g = ["git", "-C", str(d)]
    (d / "b.txt").write_text("new\n")
    subprocess.run(
      g + ["add", "-A"], check=True, timeout=60
    )
    subprocess.run(
      g + ["commit", "-q", "-m", "added",
           "--no-gpg-sign"],
      check=True, timeout=60,
    )
    data = worker.bundle_for(d, "wsbranch", base)
    assert data
    assert data.startswith(b"# v2 git bundle")
    # The bundle names base as a prerequisite rather than
    # shipping it, which is what keeps a delivery small.
    assert base.encode() in data

  def test_bundle_file_is_not_left_behind(self, tree):
    """The temporary bundle is cleaned up."""
    d, base = tree
    g = ["git", "-C", str(d)]
    (d / "c.txt").write_text("x\n")
    subprocess.run(
      g + ["add", "-A"], check=True, timeout=60
    )
    subprocess.run(
      g + ["commit", "-q", "-m", "c", "--no-gpg-sign"],
      check=True, timeout=60,
    )
    worker.bundle_for(d, "wsbranch", base)
    assert not (d / ".takt-delivery.bundle").exists()
