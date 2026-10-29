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

  def test_missing_path_reports_its_future_filesystem(self):
    """A path that does not exist yet still reports.

    This deliberately replaces an earlier contract of
    "missing path -> None". None is not a safe answer here:
    bin/takt_worker.py skips the --disk-floor-mb check when
    free space is unknown, so a worker whose work dir had
    not been created yet would accept work on a full disk
    and fail partway through a build rather than declining.
    The free space of a path that does not exist is the free
    space of the filesystem that would hold it, which is
    what df reports too.
    """
    mb = worker.free_disk_mb("/nonexistent/path/xyz")
    assert mb is not None and mb > 0


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


class TestCredentialResolution:
  """Where a worker finds its scheduler and token."""

  def _parse(self, argv):
    import argparse
    import importlib.util
    spec = importlib.util.spec_from_file_location(
      "takt_worker_mod",
      PROJECT_DIR / "bin" / "takt_worker.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    parser = argparse.ArgumentParser()
    mod.add_worker_args(parser)
    return parser.parse_args(argv), mod

  def test_reads_the_env_file(self, monkeypatch, tmp_path):
    """A worker needs no token in its shell."""
    env = tmp_path / "env"
    env.write_text(
      "TAKT_API_URL=http://sched:7433\n"
      "TAKT_API_TOKEN=s3cret\n"
      "TAKT_WORKER_ID=lap-01\n"
    )
    monkeypatch.setenv("TAKT_ENV_FILE", str(env))
    args, _ = self._parse([])
    assert args.scheduler == "http://sched:7433"
    assert args.token == "s3cret"
    assert args.worker_id == "lap-01"

  def test_real_env_wins(self, monkeypatch, tmp_path):
    """The environment overrides the file.

    That is how a single machine runs a worker against a
    different scheduler than its local service.
    """
    env = tmp_path / "env"
    env.write_text("TAKT_API_URL=http://file:7433\n")
    monkeypatch.setenv("TAKT_ENV_FILE", str(env))
    monkeypatch.setenv("TAKT_API_URL", "http://real:7433")
    args, _ = self._parse([])
    assert args.scheduler == "http://real:7433"

  def test_flag_wins_over_both(self, monkeypatch, tmp_path):
    env = tmp_path / "env"
    env.write_text("TAKT_API_URL=http://file:7433\n")
    monkeypatch.setenv("TAKT_ENV_FILE", str(env))
    monkeypatch.setenv("TAKT_API_URL", "http://real:7433")
    args, _ = self._parse(
      ["--scheduler", "http://flag:7433"]
    )
    assert args.scheduler == "http://flag:7433"

  def test_never_defaults_to_loopback(
    self, monkeypatch, tmp_path
  ):
    """An unconfigured worker must fail, not dial itself.

    api_base_url() falls back to 127.0.0.1 for client
    tools, which is right for them and wrong here: a worker
    that silently registers with a scheduler on its own
    machine looks like it is working while taking no work
    from the real queue. run_worker() rejects an empty
    --scheduler, so the default has to stay empty.
    """
    monkeypatch.setenv(
      "TAKT_ENV_FILE", str(tmp_path / "absent")
    )
    args, mod = self._parse([])
    assert args.scheduler is None

    class Boom(Exception):
      pass

    def fail(message):
      raise Boom(message)
    parser = type("P", (), {"error": staticmethod(fail)})()
    with pytest.raises(Boom, match="--scheduler"):
      mod.run_worker(args, parser)


class TestPushSeededMirrors:
  """A worker stocked by `takt provision`.

  Such a worker holds no git credentials: the scheduler
  pushed the repos to it. Its mirrors therefore have no
  reachable upstream, and that must not be mistaken for a
  broken worker.
  """

  @pytest.fixture
  def seeded(self, tmp_path):
    """A bare mirror with a commit and no usable origin.

    Built the way provisioning builds one: git init --bare
    on the far side, then a push into it. So there is no
    origin remote at all.
    """
    src = tmp_path / "src"
    src.mkdir()
    g = ["git", "-C", str(src)]
    subprocess.run(
      ["git", "init", "-q", "-b", "main", str(src)],
      check=True, timeout=60,
    )
    for key, val in (("user.email", "t@t"),
                     ("user.name", "t")):
      subprocess.run(
        g + ["config", key, val], check=True, timeout=60
      )
    (src / "f.txt").write_text("seeded\n")
    subprocess.run(g + ["add", "-A"], check=True, timeout=60)
    subprocess.run(
      g + ["commit", "-q", "-m", "c0", "--no-gpg-sign"],
      check=True, timeout=60,
    )
    sha = subprocess.run(
      g + ["rev-parse", "HEAD"], capture_output=True,
      text=True, timeout=60,
    ).stdout.strip()
    mirrors = tmp_path / "mirrors"
    mirrors.mkdir()
    mirror = mirrors / "r.git"
    subprocess.run(
      ["git", "init", "-q", "--bare", str(mirror)],
      check=True, timeout=60,
    )
    subprocess.run(
      g + ["push", "-q", "--mirror", str(mirror)],
      check=True, timeout=60,
    )
    return mirrors, mirror, sha

  # An upstream that cannot be reached. A path that does not
  # exist, rather than an unroutable address: git fails
  # identically but instantly, where a TCP connect to a
  # blackholed address costs a timeout per call and made
  # this class take over two minutes.
  UNREACHABLE = "/nonexistent/takt-test-upstream.git"

  def test_failed_refresh_is_not_fatal(self, seeded):
    """An existing mirror survives an unreachable upstream.

    Raising here would refuse work the worker can do: it
    already holds the commits.
    """
    mirrors, mirror, _ = seeded
    got = worker.ensure_mirror("r", mirrors, self.UNREACHABLE)
    assert got == mirror

  def test_initial_clone_still_raises(self, tmp_path):
    """With no mirror there is nothing to fall back on."""
    with pytest.raises(worker.WorkerError):
      worker.ensure_mirror(
        "r", tmp_path / "mirrors", self.UNREACHABLE
      )

  def test_materialises_without_any_upstream(self, seeded):
    """The whole point: a worktree with no credentials."""
    mirrors, _, sha = seeded
    work = {
      "run_id": 7,
      "workspace": "ws",
      "repos": ["r"],
      "refs": {"r:ws": sha},
    }
    trees = worker.materialise(
      work, mirrors, seeded[0].parent / "runs",
      lambda repo: self.UNREACHABLE,
    )
    head = subprocess.run(
      ["git", "-C", str(trees["r"]), "rev-parse", "HEAD"],
      capture_output=True, text=True, timeout=60,
    ).stdout.strip()
    assert head == sha
    assert (trees["r"] / "f.txt").read_text() == "seeded\n"

  def test_missing_commit_blames_the_commit(self, seeded):
    """The error must name the real problem.

    The tolerated fetch failure must not become the
    message. On a push-seeded worker every missing commit
    would otherwise report "origin does not appear to be a
    git repository", pointing the reader at remotes when
    the actual answer is that the mirror needs another
    push.
    """
    mirrors, _, _ = seeded
    missing = "deadbeef" * 5
    work = {
      "run_id": 8,
      "workspace": "ws",
      "repos": ["r"],
      "refs": {"r:ws": missing},
    }
    with pytest.raises(worker.WorkerError) as excinfo:
      worker.materialise(
        work, mirrors, mirrors.parent / "runs",
        lambda repo: self.UNREACHABLE,
      )
    message = str(excinfo.value)
    assert missing in message
    assert "not reachable" in message
    assert "takt provision" in message, (
      "the message should say how to fix it"
    )


class TestCrossPlatformProbes:
  """Machine facts, on a machine that is not this one.

  The Windows workstation reported ram_mb=null and
  free_disk_mb=null. Both are figures the scheduler uses to
  decide what to send a worker, and a null is not neutral:
  the --disk-floor-mb guard is skipped when free space is
  unknown, so the worker accepts work on a full disk and
  fails partway through a build instead of declining.
  """

  def test_free_disk_walks_up_to_an_existing_parent(
    self, tmp_path
  ):
    """A work dir that does not exist yet still reports."""
    missing = tmp_path / "not" / "created" / "yet"
    assert not missing.exists()
    got = worker.free_disk_mb(missing)
    assert got is not None and got > 0

  def test_free_disk_matches_an_existing_path(
    self, tmp_path
  ):
    direct = worker.free_disk_mb(tmp_path)
    nested = worker.free_disk_mb(tmp_path / "a" / "b")
    assert direct is not None
    # Same filesystem, so within rounding of each other.
    assert abs(direct - nested) < 50

  def test_ram_is_reported_on_this_platform(self):
    """Whatever the OS, the number must come back."""
    assert worker._ram_mb() is not None

  def test_ram_falls_back_to_windows_api(
    self, monkeypatch
  ):
    """When sysconf is absent, the Win32 path is used.

    Simulated, so this is meaningful on Linux: os.sysconf
    does not exist on Windows at all.
    """
    monkeypatch.delattr(worker.os, "sysconf", raising=False)
    monkeypatch.setattr(
      worker.platform, "system", lambda: "Windows"
    )
    monkeypatch.setattr(
      worker, "_ram_mb_windows", lambda: 16384
    )
    assert worker._ram_mb() == 16384

  def test_no_windows_fallback_on_linux(self, monkeypatch):
    """A Linux box with no sysconf must not call Win32."""
    monkeypatch.delattr(worker.os, "sysconf", raising=False)
    monkeypatch.setattr(
      worker.platform, "system", lambda: "Linux"
    )
    called = []
    monkeypatch.setattr(
      worker, "_ram_mb_windows",
      lambda: called.append(1) or 1,
    )
    assert worker._ram_mb() is None
    assert not called
