"""Tests for setting a machine up as a worker.

The real thing runs commands on another machine over SSH, so
ssh() is stubbed and these cover the decisions provisioning
makes: what it refuses, what it writes, and that it reports
the cause rather than the symptom.
"""

import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib import provision


class FakeProc:
  """Stand-in for subprocess.CompletedProcess."""

  def __init__(self, stdout="", stderr="", returncode=0):
    self.stdout = stdout
    self.stderr = stderr
    self.returncode = returncode


class Recorder:
  """Record ssh calls and answer them from a script."""

  def __init__(self, answers=None):
    self.calls = []
    self.answers = answers or {}

  def __call__(self, host, command, timeout=120, check=True):
    self.calls.append(command)
    for needle, proc in self.answers.items():
      if needle in command:
        if check and proc.returncode != 0:
          raise provision.ProvisionError(command)
        return proc
    return FakeProc()

  def commands_matching(self, needle):
    return [c for c in self.calls if needle in c]


class TestCheckChannel:
  """Catching a chatty login shell."""

  def test_clean_channel_returns_the_hostname(
    self, monkeypatch
  ):
    rec = Recorder({
      "takt-channel-ok": FakeProc("takt-channel-ok"),
      "hostname": FakeProc("klaptop\n"),
    })
    monkeypatch.setattr(provision, "ssh", rec)
    assert provision.check_channel("laptop") == "klaptop"

  def test_extra_output_is_refused(self, monkeypatch):
    """A banner must be reported, not tolerated.

    Anything on stdout at login is spliced in front of every
    command's output, and git and rsync then fail with
    "protocol incompatibility", which names neither the file
    nor the machine at fault.
    """
    rec = Recorder({
      "takt-channel-ok": FakeProc("!!!\ntakt-channel-ok"),
    })
    monkeypatch.setattr(provision, "ssh", rec)
    with pytest.raises(provision.ProvisionError) as e:
      provision.check_channel("laptop")
    message = str(e.value)
    assert "not clean" in message
    assert "!!!" in message

  def test_the_message_names_where_to_look(
    self, monkeypatch
  ):
    """The whole value of the check is the diagnosis."""
    monkeypatch.setattr(
      provision, "ssh",
      Recorder({"takt-channel-ok": FakeProc("hi\ntakt-channel-ok")}),
    )
    with pytest.raises(provision.ProvisionError) as e:
      provision.check_channel("laptop")
    message = str(e.value)
    for hint in ("Banner", ".ssh/rc", ".zshenv"):
      assert hint in message, f"should mention {hint}"


class TestCheckPrereqs:
  """What the machine must already have."""

  def test_missing_git_is_refused(self, monkeypatch):
    def fake_ssh(host, command, timeout=120, check=True):
      if "command -v git" in command:
        return FakeProc(returncode=1)
      return FakeProc()
    monkeypatch.setattr(provision, "ssh", fake_ssh)
    with pytest.raises(provision.ProvisionError, match="git"):
      provision.check_prereqs("laptop")

  def test_missing_yaml_module_is_refused(self, monkeypatch):
    """yaml is the one third-party module a worker needs."""
    def fake_ssh(host, command, timeout=120, check=True):
      if "import yaml" in command:
        return FakeProc(returncode=1)
      return FakeProc()
    monkeypatch.setattr(provision, "ssh", fake_ssh)
    with pytest.raises(
      provision.ProvisionError, match="yaml"
    ):
      provision.check_prereqs("laptop")

  def test_missing_claude_is_not_fatal(self, monkeypatch):
    """A build-only machine is legitimate.

    It simply never gets handed an agent step.
    """
    def fake_ssh(host, command, timeout=120, check=True):
      if "command -v claude" in command:
        return FakeProc(returncode=1)
      return FakeProc()
    monkeypatch.setattr(provision, "ssh", fake_ssh)
    found = provision.check_prereqs("laptop")
    assert found["claude"] is False
    assert found["git"] is True


class TestWriteEnv:
  """Installing the credential file."""

  def test_writes_url_id_and_token(self, monkeypatch):
    rec = Recorder({"stat -c": FakeProc("600\n")})
    monkeypatch.setattr(provision, "ssh", rec)
    provision.write_env(
      "laptop", "http://sched:7433", "s3cret", "lap-01"
    )
    written = rec.commands_matching("TAKT_API_URL")[0]
    assert "TAKT_API_URL=http://sched:7433" in written
    assert "TAKT_API_TOKEN=s3cret" in written
    assert "TAKT_WORKER_ID=lap-01" in written
    assert "umask 077" in written

  def test_no_token_line_when_unauthenticated(
    self, monkeypatch
  ):
    rec = Recorder({"stat -c": FakeProc("600\n")})
    monkeypatch.setattr(provision, "ssh", rec)
    provision.write_env(
      "laptop", "http://sched:7433", None, "lap-01"
    )
    written = rec.commands_matching("TAKT_API_URL")[0]
    assert "TAKT_API_TOKEN" not in written

  def test_a_readable_file_is_refused(self, monkeypatch):
    """The file holds the API token."""
    rec = Recorder({"stat -c": FakeProc("644\n")})
    monkeypatch.setattr(provision, "ssh", rec)
    with pytest.raises(
      provision.ProvisionError, match="644"
    ):
      provision.write_env(
        "laptop", "http://s:1", "t", "lap-01"
      )


class TestReposToSeed:
  """Choosing what to push."""

  @pytest.fixture
  def fake_root(self, tmp_path, monkeypatch):
    """Two managed repos present, one named but absent."""
    for name in ("repo-a", "repo-b"):
      (tmp_path / name / ".git").mkdir(parents=True)
    monkeypatch.setattr(provision, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(
      provision, "load_repos_config",
      lambda: {"repos": {
        "repo-a": {}, "repo-b": {}, "repo-gone": {},
      }},
    )
    return tmp_path

  def test_all_managed_repos_by_default(self, fake_root):
    names = [n for n, _ in provision.repos_to_seed()]
    assert names == ["repo-a", "repo-b"]

  def test_absent_repo_is_skipped_not_fatal(
    self, fake_root
  ):
    """One missing root repo must not block the rest."""
    names = [n for n, _ in provision.repos_to_seed()]
    assert "repo-gone" not in names

  def test_naming_an_absent_repo_is_an_error(
    self, fake_root
  ):
    """Explicit beats implicit: a typo must be reported."""
    with pytest.raises(
      provision.ProvisionError, match="repo-gone"
    ):
      provision.repos_to_seed(["repo-gone"])

  def test_unmanaged_repo_is_refused(self, fake_root):
    with pytest.raises(
      provision.ProvisionError, match="not managed"
    ):
      provision.repos_to_seed(["../../etc"])

  def test_a_subset_is_honoured(self, fake_root):
    names = [n for n, _ in provision.repos_to_seed(["repo-b"])]
    assert names == ["repo-b"]


class TestProvisionGuards:
  """Refusals that happen before anything is changed."""

  def test_loopback_scheduler_is_refused(self, monkeypatch):
    """On the worker, loopback means the worker itself.

    It would register against a scheduler on its own machine
    and look healthy while taking no work from the real
    queue.
    """
    monkeypatch.setattr(
      provision, "check_channel", lambda host: "klaptop"
    )
    monkeypatch.setattr(
      provision, "check_prereqs", lambda host: {"claude": True}
    )
    monkeypatch.setattr(
      provision, "api_base_url",
      lambda: "http://127.0.0.1:7433",
    )
    with pytest.raises(
      provision.ProvisionError, match="loopback"
    ):
      provision.provision("laptop")

  def test_localhost_is_refused_too(self, monkeypatch):
    monkeypatch.setattr(
      provision, "check_channel", lambda host: "klaptop"
    )
    monkeypatch.setattr(
      provision, "check_prereqs", lambda host: {}
    )
    with pytest.raises(
      provision.ProvisionError, match="loopback"
    ):
      provision.provision(
        "laptop", scheduler_url="http://localhost:7433"
      )

  def test_checks_run_before_anything_is_installed(
    self, monkeypatch
  ):
    """A chatty shell must fail before a 4 GB push.

    Ordering is the point: discovering a bad machine after
    the slow step wastes the slow step.
    """
    installed = []
    monkeypatch.setattr(
      provision, "check_channel",
      lambda host: (_ for _ in ()).throw(
        provision.ProvisionError("dirty channel")
      ),
    )
    monkeypatch.setattr(
      provision, "install_takt",
      lambda *a, **k: installed.append(1),
    )
    monkeypatch.setattr(
      provision, "seed_mirror",
      lambda *a, **k: installed.append(2),
    )
    with pytest.raises(provision.ProvisionError):
      provision.provision("laptop")
    assert not installed, "changed something despite failing"
