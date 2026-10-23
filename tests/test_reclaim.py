"""Tests for bin/reclaim.py — build output reclamation.

The destructive paths get the most attention here: what
counts as regenerable, what is refused, and that a stale
survey cannot be used to delete something that has since
stopped being a build directory.
"""

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from bin import reclaim


def make_build_dir(parent, name, marker="CMakeCache.txt",
                   payload_mb=0):
  """Create a directory that looks like a build tree."""
  d = parent / name
  d.mkdir(parents=True)
  (d / marker).write_text("# generated\n")
  (d / "sub").mkdir()
  (d / "sub" / "obj.o").write_bytes(
    b"\0" * (payload_mb * 1024 * 1024 or 64)
  )
  return d


def make_source_dir(parent, name):
  """Create a directory that is plainly source."""
  d = parent / name
  d.mkdir(parents=True)
  (d / "main.cc").write_text("int main() {}\n")
  return d


class TestClassify:
  """What counts as regenerable."""

  def test_cmake_build_dir(self, tmp_path):
    """CMakeCache.txt marks a build dir."""
    d = make_build_dir(tmp_path, "build")
    assert reclaim.classify(str(d)) == "cmake build dir"

  def test_cachedir_tag(self, tmp_path):
    """CACHEDIR.TAG marks a cache dir."""
    d = make_build_dir(
      tmp_path, "target", marker="CACHEDIR.TAG"
    )
    assert "cache dir" in reclaim.classify(str(d))

  def test_node_modules_by_name(self, tmp_path):
    """node_modules needs no marker file."""
    d = tmp_path / "node_modules"
    d.mkdir()
    assert reclaim.classify(str(d)) == "node packages"

  def test_source_is_not_reclaimable(self, tmp_path):
    """A plain source directory is never a candidate."""
    d = make_source_dir(tmp_path, "src")
    assert reclaim.classify(str(d)) is None

  def test_build_named_dir_without_marker(self, tmp_path):
    """A directory called 'build' is not enough.

    Name alone would be a guess. Only the marker proves the
    content is regenerable.
    """
    d = make_source_dir(tmp_path, "build")
    assert reclaim.classify(str(d)) is None


class TestFindReclaimable:
  """Walking a workspace."""

  def test_finds_build_dirs(self, tmp_path):
    """Build dirs at any depth are found."""
    make_build_dir(tmp_path, "b-full")
    make_build_dir(tmp_path / "repo", "build")
    make_source_dir(tmp_path, "src")
    found = reclaim.find_reclaimable(tmp_path)
    names = sorted(Path(f["path"]).name for f in found)
    assert names == ["b-full", "build"]

  def test_does_not_descend_into_a_build_dir(
    self, tmp_path
  ):
    """A build dir inside a build dir is not double counted.

    CMake builds nest; counting an inner one separately
    would inflate the total and delete twice.
    """
    outer = make_build_dir(tmp_path, "build")
    make_build_dir(outer, "_deps")
    found = reclaim.find_reclaimable(tmp_path)
    assert len(found) == 1
    assert Path(found[0]["path"]).name == "build"

  def test_skips_git_directory(self, tmp_path):
    """Nothing inside .git is ever a candidate."""
    git = tmp_path / ".git"
    git.mkdir()
    (git / "CMakeCache.txt").write_text("x")
    make_build_dir(git, "modules")
    assert reclaim.find_reclaimable(tmp_path) == []

  def test_reports_size(self, tmp_path):
    """Each find carries its disk usage."""
    make_build_dir(tmp_path, "build", payload_mb=1)
    found = reclaim.find_reclaimable(tmp_path)
    assert found[0]["bytes"] >= 1024 * 1024

  def test_empty_workspace(self, tmp_path):
    """A workspace with no build output yields nothing."""
    make_source_dir(tmp_path, "src")
    assert reclaim.find_reclaimable(tmp_path) == []


class TestIsWithin:
  """Containment checks guarding deletion."""

  def test_child_is_within(self, tmp_path):
    """A real child passes."""
    child = tmp_path / "a" / "b"
    child.mkdir(parents=True)
    assert reclaim.is_within(str(child), str(tmp_path))

  def test_outside_is_rejected(self, tmp_path):
    """A sibling directory fails."""
    other = tmp_path.parent / "elsewhere"
    assert not reclaim.is_within(
      str(other), str(tmp_path / "ws")
    )

  def test_symlink_cannot_escape(self, tmp_path):
    """A symlink pointing out of the tree is rejected.

    Both sides are resolved, so a link inside the
    workspaces tree cannot redirect deletion elsewhere.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    inside = tmp_path / "ws"
    inside.mkdir()
    link = inside / "link"
    link.symlink_to(outside, target_is_directory=True)
    assert not reclaim.is_within(str(link), str(inside))


class TestDelete:
  """The destructive path."""

  @pytest.fixture
  def ws_root(self, tmp_path, monkeypatch):
    """A workspaces tree that delete() will accept."""
    root = tmp_path / "workspaces"
    root.mkdir()
    monkeypatch.setattr(
      reclaim, "WORKSPACES_DIR", root
    )
    return root

  def test_removes_a_build_dir(self, ws_root):
    """A genuine build dir is removed."""
    d = make_build_dir(ws_root / "ws1", "build")
    ok, msg = reclaim.delete(
      {"path": str(d), "kind": "cmake build dir"}
    )
    assert ok, msg
    assert not d.exists()

  def test_refuses_outside_the_tree(self, ws_root, tmp_path):
    """A path outside the workspaces tree is refused."""
    outside = make_build_dir(tmp_path, "rogue")
    ok, msg = reclaim.delete(
      {"path": str(outside), "kind": "cmake build dir"}
    )
    assert not ok
    assert "outside" in msg
    assert outside.exists()

  def test_rechecks_marker_before_deleting(self, ws_root):
    """A stale survey cannot delete a non-build dir.

    Regression guard: the survey may be minutes old. If the
    marker is gone the directory is no longer provably
    regenerable and must be left alone.
    """
    d = make_build_dir(ws_root / "ws1", "build")
    (d / "CMakeCache.txt").unlink()
    ok, msg = reclaim.delete(
      {"path": str(d), "kind": "cmake build dir"}
    )
    assert not ok
    assert "no longer" in msg
    assert d.exists()

  def test_already_gone_is_not_an_error(self, ws_root):
    """A directory removed since the survey is fine."""
    ok, msg = reclaim.delete({
      "path": str(ws_root / "ws1" / "build"),
      "kind": "cmake build dir",
    })
    assert not ok
    assert "gone" in msg


class TestSurvey:
  """Aggregation across workspaces."""

  def test_sorts_largest_first(self, tmp_path):
    """Biggest reclaim first, so attention goes there."""
    small = tmp_path / "small"
    big = tmp_path / "big"
    make_build_dir(small, "build", payload_mb=1)
    make_build_dir(big, "build", payload_mb=4)
    rows = reclaim.survey([
      {"name": "small", "path": str(small),
       "last_active": 0},
      {"name": "big", "path": str(big), "last_active": 0},
    ], now=1_000_000)
    assert [r["workspace"] for r in rows] == ["big", "small"]

  def test_omits_workspaces_with_nothing(self, tmp_path):
    """A clean workspace does not appear in the report."""
    clean = tmp_path / "clean"
    make_source_dir(clean, "src")
    rows = reclaim.survey([
      {"name": "clean", "path": str(clean),
       "last_active": 0},
    ], now=1_000_000)
    assert rows == []

  def test_computes_idle_days(self, tmp_path):
    """Idle time comes through for the report."""
    ws = tmp_path / "ws"
    make_build_dir(ws, "build")
    now = 100 * reclaim.SECONDS_PER_DAY
    rows = reclaim.survey([
      {"name": "ws", "path": str(ws),
       "last_active": 70 * reclaim.SECONDS_PER_DAY},
    ], now=now)
    assert rows[0]["idle_days"] == 30


class TestIdleDays:
  """Idle computation."""

  def test_unknown_last_active(self):
    """A zero timestamp means unknown, not 'ancient'."""
    assert reclaim.idle_days(0, 1_000_000) is None

  def test_whole_days(self):
    """Days are truncated, not rounded."""
    now = 10 * reclaim.SECONDS_PER_DAY
    then = 7.9 * reclaim.SECONDS_PER_DAY
    assert reclaim.idle_days(then, now) == 2


class TestHuman:
  """Size formatting."""

  def test_bytes(self):
    """Small sizes stay in bytes without a decimal."""
    assert reclaim.human(512) == "512B"

  def test_gigabytes(self):
    """Large sizes read as G."""
    assert reclaim.human(5 * 1024**3) == "5.0G"


class TestCliGuards:
  """Command-line safety rails."""

  def _run(self, *args):
    return subprocess.run(
      [sys.executable,
       str(PROJECT_DIR / "bin" / "reclaim.py"), *args],
      capture_output=True, text=True, timeout=120,
    )

  def test_apply_without_scope_is_refused(self):
    """A bare --apply cannot wipe every workspace."""
    res = self._run("--apply")
    assert res.returncode != 0
    assert "refusing" in res.stderr

  def test_unknown_workspace_is_an_error(self):
    """A typo'd workspace name does not silently no-op."""
    res = self._run("--workspace", "no-such-workspace-xyz")
    assert res.returncode == 2
    assert "unknown workspace" in res.stderr


class TestDryRunDefault:
  """Dry run is the default, and it must really be dry.

  Driven in-process against a temporary tree, because the
  CLI otherwise walks the real workspaces directory.
  """

  @pytest.fixture
  def fake_tree(self, tmp_path, monkeypatch):
    """One workspace with reclaimable output."""
    ws = tmp_path / "workspaces" / "ws1"
    build = make_build_dir(ws, "build", payload_mb=1)
    monkeypatch.setattr(
      reclaim, "WORKSPACES_DIR", tmp_path / "workspaces"
    )
    monkeypatch.setattr(
      reclaim, "list_workspaces",
      lambda: [{"name": "ws1", "path": str(ws),
                "last_active": 1.0}],
    )
    return build

  def test_default_run_deletes_nothing(
    self, fake_tree, monkeypatch, capsys
  ):
    """A run with no --apply leaves the build dir alone."""
    monkeypatch.setattr(sys, "argv", ["reclaim.py"])
    assert reclaim.main() == 0
    out = capsys.readouterr().out
    assert "dry run" in out
    assert "nothing deleted" in out
    assert fake_tree.exists()

  def test_apply_refuses_when_service_is_silent(
    self, fake_tree, monkeypatch, capsys
  ):
    """Unknown run state must not be read as 'idle'.

    If the service cannot be reached, takt cannot tell
    which workspaces have runs in flight. Deleting a build
    tree out from under a live run would be worse than
    doing nothing.
    """
    monkeypatch.setattr(
      sys, "argv",
      ["reclaim.py", "--older-than", "0", "--apply"],
    )
    monkeypatch.setattr(
      reclaim, "active_workspaces", lambda: None
    )
    assert reclaim.main() == 3
    assert fake_tree.exists()

  def test_apply_spares_a_busy_workspace(
    self, fake_tree, monkeypatch, capsys
  ):
    """A workspace with a run in flight is not touched."""
    monkeypatch.setattr(
      sys, "argv",
      ["reclaim.py", "--older-than", "0", "--apply"],
    )
    monkeypatch.setattr(
      reclaim, "active_workspaces", lambda: {"ws1"}
    )
    assert reclaim.main() == 0
    out = capsys.readouterr().out
    assert "spared" in out
    assert fake_tree.exists()

  def test_apply_reclaims_an_idle_workspace(
    self, fake_tree, monkeypatch, capsys
  ):
    """With nothing running, the build dir is removed."""
    monkeypatch.setattr(
      sys, "argv",
      ["reclaim.py", "--older-than", "0", "--apply"],
    )
    monkeypatch.setattr(
      reclaim, "active_workspaces", lambda: set()
    )
    assert reclaim.main() == 0
    out = capsys.readouterr().out
    assert "freed" in out
    assert not fake_tree.exists()
