"""Tests for lib/config.py loaders and role parsing."""

import textwrap

import pytest

from lib import config
from lib.config import (
  _slugify_role,
  parse_pipeline_roles,
  parse_pipeline_roles_full,
  save_pipeline_roles,
)


SAMPLE_ROLES_MD = textwrap.dedent("""\
  # Pipeline Role Templates

  Preamble paragraph.

  ---

  ## Test Agent

  Run the test suite.

  Guidelines:
  - Run all tests.

  ---

  ## Deploy/QA Agent

  Build and deploy.
""")


@pytest.fixture()
def roles_file(tmp_path, monkeypatch):
  """Write sample roles file and patch TEMPLATES_DIR."""
  monkeypatch.setattr(
    "lib.config.TEMPLATES_DIR", tmp_path
  )
  path = tmp_path / "pipeline_roles.md"
  path.write_text(SAMPLE_ROLES_MD)
  return path


class TestParsePipelineRolesFull:
  """Tests for parse_pipeline_roles_full()."""

  def test_returns_list_of_dicts(self, roles_file):
    roles = parse_pipeline_roles_full()
    assert isinstance(roles, list)
    assert len(roles) == 2
    for role in roles:
      assert "slug" in role
      assert "heading" in role
      assert "text" in role

  def test_preserves_headings(self, roles_file):
    roles = parse_pipeline_roles_full()
    assert roles[0]["heading"] == "Test Agent"
    assert roles[1]["heading"] == "Deploy/QA Agent"

  def test_correct_slugs(self, roles_file):
    roles = parse_pipeline_roles_full()
    assert roles[0]["slug"] == "test"
    assert roles[1]["slug"] == "deploy_qa"

  def test_preserves_order(self, roles_file):
    roles = parse_pipeline_roles_full()
    slugs = [r["slug"] for r in roles]
    assert slugs == ["test", "deploy_qa"]

  def test_text_content(self, roles_file):
    roles = parse_pipeline_roles_full()
    assert "Run the test suite." in roles[0]["text"]
    assert "Build and deploy." in roles[1]["text"]

  def test_empty_file(self, tmp_path, monkeypatch):
    monkeypatch.setattr(
      "lib.config.TEMPLATES_DIR", tmp_path
    )
    (tmp_path / "pipeline_roles.md").write_text("")
    assert parse_pipeline_roles_full() == []

  def test_missing_file(self, tmp_path, monkeypatch):
    monkeypatch.setattr(
      "lib.config.TEMPLATES_DIR", tmp_path
    )
    assert parse_pipeline_roles_full() == []


class TestSavePipelineRoles:
  """Tests for save_pipeline_roles()."""

  def test_round_trip(self, roles_file):
    """Parse, save, parse again — content matches."""
    original = parse_pipeline_roles_full()
    save_pipeline_roles(original)
    reloaded = parse_pipeline_roles_full()
    assert len(reloaded) == len(original)
    for orig, new in zip(original, reloaded):
      assert orig["slug"] == new["slug"]
      assert orig["heading"] == new["heading"]
      assert orig["text"] == new["text"]

  def test_preserves_preamble(self, roles_file):
    """Preamble text before first ## is preserved."""
    roles = parse_pipeline_roles_full()
    save_pipeline_roles(roles)
    content = roles_file.read_text()
    assert "# Pipeline Role Templates" in content
    assert "Preamble paragraph." in content

  def test_slug_dict_matches(self, roles_file):
    """Round-tripped file yields same parse_pipeline_roles."""
    original_dict = parse_pipeline_roles()
    roles = parse_pipeline_roles_full()
    save_pipeline_roles(roles)
    reloaded_dict = parse_pipeline_roles()
    assert set(original_dict.keys()) == set(
      reloaded_dict.keys()
    )
    for key in original_dict:
      assert original_dict[key] == reloaded_dict[key]

  def test_creates_file(self, tmp_path, monkeypatch):
    """save_pipeline_roles creates file if missing."""
    monkeypatch.setattr(
      "lib.config.TEMPLATES_DIR", tmp_path
    )
    roles = [{
      "heading": "New Agent",
      "text": "Do new things.",
    }]
    save_pipeline_roles(roles)
    path = tmp_path / "pipeline_roles.md"
    assert path.exists()
    content = path.read_text()
    assert "## New Agent" in content
    assert "Do new things." in content


class TestSlugifyRole:
  """Tests for _slugify_role()."""

  def test_basic(self):
    assert _slugify_role("Test Agent") == "test"

  def test_slash(self):
    assert _slugify_role("Deploy/QA Agent") == "deploy_qa"

  def test_no_agent_suffix(self):
    assert _slugify_role("Feature") == "feature"

  def test_multi_word(self):
    assert _slugify_role("Code Review Agent") == "code_review"


class TestLoadReposConfig:
  """Reading the repo registry off disk.

  These read a real file on purpose. Every other test in
  the suite mocks load_repos_config() with the nested
  shape, so nothing checked that the loader and the file
  that ships actually agree -- and they did not. The flat
  file that ships made .get("repos", {}) return nothing,
  which is what the 16 callers all do.
  """

  def _load(self, tmp_path, monkeypatch, text):
    (tmp_path / "repos.yaml").write_text(text)
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    return config.load_repos_config()

  def test_flat_file_is_normalised(
    self, tmp_path, monkeypatch
  ):
    """A flat mapping is the shape that ships."""
    data = self._load(tmp_path, monkeypatch, (
      "OTC.SDK:\n"
      "  push_order: 1\n"
      "  github: Optris/OTC.SDK\n"
      "OTC.Relay:\n"
      "  push_order: 3\n"
    ))
    repos = data.get("repos", {})
    assert set(repos) == {"OTC.SDK", "OTC.Relay"}
    assert repos["OTC.SDK"]["push_order"] == 1

  def test_nested_file_is_passed_through(
    self, tmp_path, monkeypatch
  ):
    data = self._load(tmp_path, monkeypatch, (
      "repos:\n"
      "  OTC.SDK:\n"
      "    push_order: 1\n"
    ))
    assert set(data["repos"]) == {"OTC.SDK"}

  def test_the_shipped_file_yields_repos(self):
    """The real config/repos.yaml must not read as empty.

    This is the assertion that would have caught the bug:
    whatever shape the operator's file is in, the registry
    the callers read must not be empty.
    """
    path = config.CONFIG_DIR / "repos.yaml"
    if not path.exists():
      pytest.skip("no repos.yaml on this machine")
    repos = config.load_repos_config().get("repos", {})
    assert repos, (
      "config/repos.yaml parses to an empty registry; "
      "every caller doing .get('repos', {}) sees no repos"
    )

  def test_empty_file_is_safe(self, tmp_path, monkeypatch):
    data = self._load(tmp_path, monkeypatch, "")
    assert data.get("repos", {}) == {}

  def test_a_list_is_not_treated_as_a_registry(
    self, tmp_path, monkeypatch
  ):
    """A malformed file must not crash the callers."""
    data = self._load(tmp_path, monkeypatch, "- a\n- b\n")
    assert data.get("repos", {}) == {}
