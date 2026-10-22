"""Tests for lib.context_store — context engine."""

import pytest

from lib import context_store as cs
from lib import db


@pytest.fixture
def tmp_db(tmp_path):
  """Create a temporary database and migrate it."""
  path = tmp_path / "test.db"
  db.migrate(db_path=str(path))
  return str(path)


@pytest.fixture
def corpus(tmp_path):
  """Write a small markdown corpus and return its dirs."""
  ctx = tmp_path / "context"
  tpl = tmp_path / "templates"
  ctx.mkdir()
  tpl.mkdir()
  (ctx / "build.md").write_text(
    "# Building\n\n"
    "Intro paragraph.\n\n"
    "## OTC.SDK\n\n"
    "Run cmake with ninja.\n\n"
    "## Conveyor\n\n"
    "Belt timing uses milliseconds.\n"
  )
  (ctx / "vms.md").write_text(
    "# Virtual Machines\n\n"
    "## Clones\n\n"
    "Clones use qcow2 backing files.\n"
  )
  (tpl / "roles.md").write_text(
    "# Roles\n\n## feature\n\nImplement the feature.\n"
  )
  (ctx / "notes.txt").write_text("not markdown")
  return [("context", ctx), ("templates", tpl)]


class TestSplitMarkdown:
  """Section chunking tests."""

  def test_splits_on_headings(self):
    """Each heading starts a new chunk."""
    chunks = cs.split_markdown(
      "# A\n\nbody a\n\n## B\n\nbody b\n"
    )
    headings = [c["heading"] for c in chunks]
    assert headings == ["A", "B"]

  def test_builds_heading_path(self):
    """Nested headings accumulate into a path."""
    text = "# Top\n\n## Mid\n\n### Leaf\n\nbody\n"
    chunks = cs.split_markdown(text)
    leaf = [c for c in chunks if c["heading"] == "Leaf"][0]
    assert leaf["heading_path"] == "Top > Mid > Leaf"

  def test_pops_stack_on_shallower_heading(self):
    """A sibling heading replaces, not nests."""
    text = "# T\n\n## A\n\nx\n\n## B\n\ny\n"
    chunks = cs.split_markdown(text)
    b = [c for c in chunks if c["heading"] == "B"][0]
    assert b["heading_path"] == "T > B"

  def test_ignores_headings_in_code_fences(self):
    """A '#' inside a fence is a comment, not a heading."""
    text = (
      "# Real\n\n"
      "```sh\n# not a heading\necho hi\n```\n\n"
      "more body\n"
    )
    chunks = cs.split_markdown(text)
    assert len(chunks) == 1
    assert chunks[0]["heading"] == "Real"
    assert "not a heading" in chunks[0]["body"]

  def test_keeps_preamble_before_first_heading(self):
    """Content above the first heading is not dropped."""
    chunks = cs.split_markdown("lead in\n\n# A\n\nbody\n")
    assert chunks[0]["heading"] == ""
    assert "lead in" in chunks[0]["body"]

  def test_records_line_numbers(self):
    """Chunks carry their line range in the source."""
    text = "# A\n\nline three\n\n## B\n\nline seven\n"
    chunks = cs.split_markdown(text)
    a = chunks[0]
    assert a["start_line"] == 1
    assert a["end_line"] >= 3
    b = chunks[1]
    assert b["start_line"] == 5

  def test_splits_oversized_sections(self):
    """A section over the token budget becomes several."""
    para = ("word " * 60).strip()
    body = "\n\n".join([para] * 12)
    chunks = cs.split_markdown(
      f"# Big\n\n{body}\n", max_tokens=100
    )
    assert len(chunks) > 1
    assert all(c["heading"] == "Big" for c in chunks)
    assert all(
      c["heading_path"] == "Big" for c in chunks
    )

  def test_oversized_paragraph_kept_whole(self):
    """A single huge paragraph is not cut mid-sentence."""
    para = ("word " * 400).strip()
    chunks = cs.split_markdown(
      f"# Big\n\n{para}\n", max_tokens=50
    )
    assert len(chunks) == 1
    assert chunks[0]["body"].count("word") == 400

  def test_empty_document(self):
    """An empty document yields no chunks."""
    assert cs.split_markdown("") == []

  def test_no_body_loss(self):
    """Every non-blank source line lands in some chunk."""
    text = (
      "# A\n\nalpha\n\n## B\n\nbeta\n\n### C\n\ngamma\n"
    )
    chunks = cs.split_markdown(text)
    joined = "\n".join(c["body"] for c in chunks)
    for word in ("alpha", "beta", "gamma"):
      assert word in joined


class TestDocTitle:
  """Title derivation tests."""

  def test_uses_first_h1(self):
    """An H1 wins."""
    assert cs.doc_title(
      "context/a.md", "# Real Title\n\nbody"
    ) == "Real Title"

  def test_falls_back_to_filename(self):
    """Without an H1, the stem is humanised."""
    assert cs.doc_title(
      "context/vm-templates.md", "no heading"
    ) == "vm templates"

  def test_ignores_h2_as_title(self):
    """Only an H1 counts as the document title."""
    assert cs.doc_title(
      "context/my_doc.md", "## Section\n\nbody"
    ) == "my doc"


class TestQueryBuilding:
  """FTS5 query sanitisation tests."""

  def test_quotes_each_term(self):
    """Terms are quoted so they cannot act as operators."""
    assert cs.build_match("wal mode") == '"wal" AND "mode"'

  def test_bareword_operators_are_literals(self):
    """AND/OR/NOT/NEAR in user text do not become syntax."""
    assert cs.build_match("NEAR") == '"NEAR"'

  def test_keeps_quoted_phrase(self):
    """A quoted span stays a single phrase term."""
    assert cs.build_match('"wal mode"') == '"wal mode"'

  def test_strips_punctuation(self):
    """Punctuation-only input yields no terms."""
    assert cs.build_match("((  ))") == ""
    assert cs.build_match("") == ""
    assert cs.build_match(None) == ""

  def test_or_operator(self):
    """The operator is selectable for the OR fallback."""
    assert cs.build_match(
      "a b", operator="OR"
    ) == '"a" OR "b"'

  @pytest.mark.parametrize("query", [
    "AND", "OR", "NOT", "NEAR", "((", "))", '"unclosed',
    "a-b-c", "", "   ", "*", "C++ / CMake?", "it's",
    "^anchor", "col:val", "NEAR(a b)",
  ])
  def test_never_raises_on_hostile_input(
    self, query, tmp_db
  ):
    """No user string can produce an FTS5 syntax error."""
    cs.search(query, db_path=tmp_db)


class TestUpsertDoc:
  """Document indexing tests."""

  def test_creates_doc_and_chunks(self, tmp_db):
    """A new doc is stored with its chunks."""
    res = cs.upsert_doc(
      "context/a.md", "# A\n\nbody\n\n## B\n\nmore\n",
      db_path=tmp_db,
    )
    assert res["status"] == "created"
    assert res["chunks"] == 2

  def test_unchanged_content_is_skipped(self, tmp_db):
    """Re-indexing identical content does no work."""
    text = "# A\n\nbody\n"
    cs.upsert_doc("context/a.md", text, db_path=tmp_db)
    res = cs.upsert_doc("context/a.md", text, db_path=tmp_db)
    assert res["status"] == "unchanged"

  def test_changed_content_replaces_chunks(self, tmp_db):
    """An edit re-chunks rather than appending."""
    cs.upsert_doc(
      "context/a.md", "# A\n\nold text\n", db_path=tmp_db
    )
    cs.upsert_doc(
      "context/a.md", "# A\n\nnew text\n", db_path=tmp_db
    )
    doc = cs.get_doc("context/a.md", db_path=tmp_db)
    assert len(doc["chunks"]) == 1
    assert "new text" in doc["chunks"][0]["body"]
    assert "old text" not in doc["chunks"][0]["body"]

  def test_stale_text_leaves_the_search_index(self, tmp_db):
    """Replaced content is purged from the FTS index.

    Regression guard for the external-content FTS table:
    without the delete trigger, editing a doc orphans its
    old terms in the index. search() happens to hide them
    behind its join, so this asserts against the index
    directly — otherwise the test cannot fail. Orphans
    matter because the index grows without bound across
    edits, and any rowid reuse resurrects deleted text as
    a false match.
    """
    cs.upsert_doc(
      "context/a.md", "# A\n\nvoltaic pileup\n",
      db_path=tmp_db,
    )
    assert cs.search("voltaic", db_path=tmp_db)
    cs.upsert_doc(
      "context/a.md", "# A\n\nsomething else\n",
      db_path=tmp_db,
    )
    assert cs.search("voltaic", db_path=tmp_db) == []
    with db._connect(tmp_db) as conn:
      orphans = conn.execute(
        "SELECT COUNT(*) FROM context_fts "
        "WHERE context_fts MATCH ?", ('"voltaic"',)
      ).fetchone()[0]
    assert orphans == 0

  def test_fts_index_stays_consistent(self, tmp_db):
    """The index holds exactly one row per live chunk."""
    for i in range(5):
      cs.upsert_doc(
        "context/a.md",
        f"# A\n\nrevision {i}\n\n## B\n\nmore {i}\n",
        db_path=tmp_db,
      )
    with db._connect(tmp_db) as conn:
      chunks = conn.execute(
        "SELECT COUNT(*) FROM context_chunks"
      ).fetchone()[0]
      indexed = conn.execute(
        "SELECT COUNT(*) FROM context_fts"
      ).fetchone()[0]
      conn.execute(
        "INSERT INTO context_fts(context_fts) "
        "VALUES('integrity-check')"
      )
    assert chunks == 2
    assert indexed == chunks

  def test_records_wiki_links(self, tmp_db):
    """[[links]] are extracted for later traversal."""
    cs.upsert_doc(
      "context/a.md",
      "# A\n\nSee [[other-doc]] and [[third]].\n",
      db_path=tmp_db,
    )
    doc = cs.get_doc("context/a.md", db_path=tmp_db)
    assert doc["links"] == ["other-doc", "third"]

  def test_rejects_unknown_scope(self, tmp_db):
    """An invalid scope_kind is refused."""
    with pytest.raises(ValueError, match="invalid scope"):
      cs.upsert_doc(
        "context/a.md", "# A\n", scope_kind="planet",
        db_path=tmp_db,
      )

  def test_requires_scope_value(self, tmp_db):
    """A repo scope without a repo name is refused."""
    with pytest.raises(ValueError, match="scope_value"):
      cs.upsert_doc(
        "context/a.md", "# A\n", scope_kind="repo",
        db_path=tmp_db,
      )


class TestReindex:
  """Filesystem sync tests."""

  def test_indexes_markdown_only(self, tmp_db, corpus):
    """Non-markdown files are ignored."""
    stats = cs.reindex(dirs=corpus, db_path=tmp_db)
    assert stats["created"] == 3
    assert all(
      p.endswith(".md") for p in stats["paths"]
    )

  def test_second_run_is_a_no_op(self, tmp_db, corpus):
    """Unchanged files are not re-chunked."""
    cs.reindex(dirs=corpus, db_path=tmp_db)
    stats = cs.reindex(dirs=corpus, db_path=tmp_db)
    assert stats["unchanged"] == 3
    assert stats["created"] == 0
    assert stats["updated"] == 0

  def test_detects_edits(self, tmp_db, corpus):
    """An edited file is re-indexed."""
    cs.reindex(dirs=corpus, db_path=tmp_db)
    (corpus[0][1] / "build.md").write_text(
      "# Building\n\nrewritten\n"
    )
    stats = cs.reindex(dirs=corpus, db_path=tmp_db)
    assert stats["updated"] == 1

  def test_prunes_deleted_files(self, tmp_db, corpus):
    """A removed file drops out of the index."""
    cs.reindex(dirs=corpus, db_path=tmp_db)
    (corpus[0][1] / "vms.md").unlink()
    stats = cs.reindex(dirs=corpus, db_path=tmp_db)
    assert stats["removed"] == 1
    assert cs.get_doc("context/vms.md", db_path=tmp_db) is None

  def test_never_prunes_facts(self, tmp_db, corpus):
    """Recorded facts survive a reindex."""
    cs.record_fact(
      "# Kept\n\nA durable fact.", db_path=tmp_db
    )
    cs.reindex(dirs=corpus, db_path=tmp_db)
    facts = cs.list_docs(kind="fact", db_path=tmp_db)
    assert len(facts) == 1

  def test_missing_directory_is_tolerated(
    self, tmp_db, tmp_path
  ):
    """A directory that does not exist is skipped."""
    stats = cs.reindex(
      dirs=[("ghost", tmp_path / "nope")], db_path=tmp_db
    )
    assert stats["created"] == 0


class TestSearch:
  """Retrieval tests."""

  def test_finds_section_not_whole_file(
    self, tmp_db, corpus
  ):
    """A hit points at the matching section."""
    cs.reindex(dirs=corpus, db_path=tmp_db)
    hits = cs.search("qcow2", db_path=tmp_db)
    assert hits
    assert hits[0]["path"] == "context/vms.md"
    assert hits[0]["heading"] == "Clones"
    assert hits[0]["heading_path"] == (
      "Virtual Machines > Clones"
    )

  def test_requires_all_terms_when_possible(
    self, tmp_db, corpus
  ):
    """A fully matching query reports 'all'."""
    cs.reindex(dirs=corpus, db_path=tmp_db)
    hits = cs.search("qcow2 backing", db_path=tmp_db)
    assert hits
    assert hits[0]["matched"] == "all"

  def test_falls_back_to_any_term(self, tmp_db, corpus):
    """A query with an absent word still returns hits."""
    cs.reindex(dirs=corpus, db_path=tmp_db)
    hits = cs.search(
      "how do I make a qcow2 snapshot", db_path=tmp_db
    )
    assert hits
    assert hits[0]["matched"] == "any"

  def test_no_fallback_for_single_term(
    self, tmp_db, corpus
  ):
    """A single absent term returns nothing, not noise."""
    cs.reindex(dirs=corpus, db_path=tmp_db)
    assert cs.search(
      "zzzznonexistent", db_path=tmp_db
    ) == []

  def test_scope_isolates_repos(self, tmp_db):
    """A repo-scoped search excludes other repos."""
    cs.record_fact(
      "# arm64 needs --disable-asm\n\nffmpeg quirk.",
      scope_kind="repo", scope_value="OTC.SDK",
      db_path=tmp_db,
    )
    cs.record_fact(
      "# belt delay is in ms\n\nffmpeg unrelated.",
      scope_kind="repo", scope_value="Conveyor",
      db_path=tmp_db,
    )
    hits = cs.search(
      "ffmpeg", scope_kind="repo", scope_value="OTC.SDK",
      db_path=tmp_db,
    )
    assert len(hits) == 1
    assert hits[0]["scope_value"] == "OTC.SDK"

  def test_scope_still_sees_global(self, tmp_db, corpus):
    """Global docs remain visible inside a repo scope."""
    cs.reindex(dirs=corpus, db_path=tmp_db)
    hits = cs.search(
      "qcow2", scope_kind="repo", scope_value="OTC.SDK",
      db_path=tmp_db,
    )
    assert hits
    assert hits[0]["scope_kind"] == "global"

  def test_scoped_results_outrank_global(self, tmp_db):
    """A scoped fact sorts above a global one."""
    cs.record_fact(
      "# Global build note\n\nUse ninja for builds.",
      db_path=tmp_db,
    )
    cs.record_fact(
      "# SDK build note\n\nUse ninja for builds.",
      scope_kind="repo", scope_value="OTC.SDK",
      db_path=tmp_db,
    )
    hits = cs.search(
      "ninja builds", scope_kind="repo",
      scope_value="OTC.SDK", db_path=tmp_db,
    )
    assert hits[0]["scope_value"] == "OTC.SDK"

  def test_filters_by_kind(self, tmp_db, corpus):
    """kind='fact' excludes indexed files."""
    cs.reindex(dirs=corpus, db_path=tmp_db)
    cs.record_fact(
      "# qcow2 note\n\nA fact about qcow2.",
      db_path=tmp_db,
    )
    hits = cs.search("qcow2", kind="fact", db_path=tmp_db)
    assert hits
    assert all(h["kind"] == "fact" for h in hits)

  def test_hides_superseded_by_default(self, tmp_db):
    """A retired fact drops out of search."""
    cs.record_fact(
      "# Old\n\nglargle was once true.",
      path="facts/old.md", db_path=tmp_db,
    )
    cs.record_fact(
      "# New\n\nglargle is now this.",
      path="facts/new.md", db_path=tmp_db,
    )
    cs.supersede(
      "facts/old.md", "facts/new.md", db_path=tmp_db
    )
    hits = cs.search("glargle", db_path=tmp_db)
    assert [h["path"] for h in hits] == ["facts/new.md"]
    hits = cs.search(
      "glargle", include_superseded=True, db_path=tmp_db
    )
    assert len(hits) == 2

  def test_respects_limit(self, tmp_db, corpus):
    """The limit caps the result count."""
    cs.reindex(dirs=corpus, db_path=tmp_db)
    hits = cs.search("the", limit=2, db_path=tmp_db)
    assert len(hits) <= 2

  def test_returns_provenance(self, tmp_db):
    """Results carry where the knowledge came from."""
    cs.record_fact(
      "# Flaky test\n\nIt fails under load.",
      scope_kind="workspace", scope_value="sdk-fence-races",
      source_run=77, source_workspace="sdk-fence-races",
      db_path=tmp_db,
    )
    hit = cs.search("flaky load", db_path=tmp_db)[0]
    assert hit["source_run"] == 77
    assert hit["source_workspace"] == "sdk-fence-races"
    assert hit["source"] == "agent"

  def test_empty_query_returns_nothing(self, tmp_db):
    """An empty query is not a match-all."""
    assert cs.search("", db_path=tmp_db) == []
    assert cs.search("   ", db_path=tmp_db) == []


class TestRecordFact:
  """Fact write-path tests."""

  def test_derives_path_from_title(self, tmp_db):
    """The slug makes re-recording idempotent."""
    res = cs.record_fact(
      "# ffmpeg needs --disable-asm\n\nbody",
      scope_kind="repo", scope_value="OTC.SDK",
      db_path=tmp_db,
    )
    assert res["path"] == (
      "facts/otc-sdk/ffmpeg-needs-disable-asm.md"
    )

  def test_same_subject_updates_in_place(self, tmp_db):
    """Correcting a fact does not duplicate it."""
    cs.record_fact(
      "# Build flag\n\nFirst understanding.",
      db_path=tmp_db,
    )
    res = cs.record_fact(
      "# Build flag\n\nCorrected understanding.",
      db_path=tmp_db,
    )
    assert res["status"] == "updated"
    assert len(cs.list_docs(kind="fact", db_path=tmp_db)) == 1

  def test_stamps_verified_at(self, tmp_db):
    """A verified fact records when it was confirmed."""
    cs.record_fact("# A\n\nbody", db_path=tmp_db)
    doc = cs.list_docs(kind="fact", db_path=tmp_db)[0]
    assert doc["verified_at"]

  def test_unverified_fact_has_no_stamp(self, tmp_db):
    """An unverified fact is distinguishable."""
    cs.record_fact(
      "# A\n\nbody", verified=False, db_path=tmp_db
    )
    doc = cs.list_docs(kind="fact", db_path=tmp_db)[0]
    assert doc["verified_at"] is None

  def test_rejects_empty_body(self, tmp_db):
    """An empty fact is refused."""
    with pytest.raises(ValueError, match="empty"):
      cs.record_fact("   ", db_path=tmp_db)

  def test_explicit_path_is_respected(self, tmp_db):
    """A caller can choose the identifier."""
    res = cs.record_fact(
      "# A\n\nbody", path="facts/custom.md",
      db_path=tmp_db,
    )
    assert res["path"] == "facts/custom.md"


class TestSupersede:
  """Retirement tests."""

  def test_links_old_to_new(self, tmp_db):
    """Superseding records which doc replaced which."""
    cs.record_fact(
      "# A\n\nbody", path="facts/a.md", db_path=tmp_db
    )
    cs.record_fact(
      "# B\n\nbody", path="facts/b.md", db_path=tmp_db
    )
    assert cs.supersede(
      "facts/a.md", "facts/b.md", db_path=tmp_db
    )
    old = cs.get_doc("facts/a.md", db_path=tmp_db)
    new = cs.get_doc("facts/b.md", db_path=tmp_db)
    assert old["superseded_by"] == new["id"]

  def test_superseded_doc_stays_readable(self, tmp_db):
    """History is retained, not deleted."""
    cs.record_fact(
      "# A\n\nonce believed", path="facts/a.md",
      db_path=tmp_db,
    )
    cs.record_fact(
      "# B\n\nnow true", path="facts/b.md", db_path=tmp_db
    )
    cs.supersede(
      "facts/a.md", "facts/b.md", db_path=tmp_db
    )
    doc = cs.get_doc("facts/a.md", db_path=tmp_db)
    assert "once believed" in doc["text"]

  def test_missing_doc_returns_false(self, tmp_db):
    """Superseding an unknown path is not an error."""
    assert not cs.supersede(
      "facts/nope.md", "facts/also-nope.md",
      db_path=tmp_db,
    )

  def test_self_supersede_is_refused(self, tmp_db):
    """A doc cannot replace itself."""
    cs.record_fact(
      "# A\n\nbody", path="facts/a.md", db_path=tmp_db
    )
    with pytest.raises(ValueError, match="itself"):
      cs.supersede(
        "facts/a.md", "facts/a.md", db_path=tmp_db
      )


class TestGetDoc:
  """Document read tests."""

  def test_returns_chunks_in_order(self, tmp_db):
    """Chunks come back in document order."""
    cs.upsert_doc(
      "context/a.md",
      "# A\n\none\n\n## B\n\ntwo\n\n## C\n\nthree\n",
      db_path=tmp_db,
    )
    doc = cs.get_doc("context/a.md", db_path=tmp_db)
    assert [c["heading"] for c in doc["chunks"]] == [
      "A", "B", "C",
    ]

  def test_missing_doc_returns_none(self, tmp_db):
    """An unknown path is None, not an exception."""
    assert cs.get_doc("context/nope.md", db_path=tmp_db) is None


class TestDeleteDoc:
  """Deletion tests."""

  def test_removes_from_search(self, tmp_db):
    """A deleted doc is no longer retrievable."""
    cs.upsert_doc(
      "context/a.md", "# A\n\nzyzzyva\n", db_path=tmp_db
    )
    assert cs.search("zyzzyva", db_path=tmp_db)
    assert cs.delete_doc("context/a.md", db_path=tmp_db)
    assert cs.search("zyzzyva", db_path=tmp_db) == []

  def test_missing_doc_returns_false(self, tmp_db):
    """Deleting an unknown path is not an error."""
    assert not cs.delete_doc(
      "context/nope.md", db_path=tmp_db
    )


class TestStats:
  """Index summary tests."""

  def test_counts_docs_and_facts_separately(
    self, tmp_db, corpus
  ):
    """Files and facts are reported apart."""
    cs.reindex(dirs=corpus, db_path=tmp_db)
    cs.record_fact("# F\n\nbody", db_path=tmp_db)
    st = cs.stats(db_path=tmp_db)
    assert st["docs"] == 3
    assert st["facts"] == 1
    assert st["chunks"] > 3
    assert st["est_tokens"] > 0

  def test_groups_by_scope(self, tmp_db):
    """Scope breakdown is reported."""
    cs.record_fact(
      "# A\n\nbody", scope_kind="repo",
      scope_value="OTC.SDK", db_path=tmp_db,
    )
    st = cs.stats(db_path=tmp_db)
    scopes = {
      (s["scope_kind"], s["scope_value"]): s["docs"]
      for s in st["scopes"]
    }
    assert scopes[("repo", "OTC.SDK")] == 1


class TestEstTokens:
  """Token estimate tests."""

  def test_empty_is_zero(self):
    """No text, no tokens."""
    assert cs.est_tokens("") == 0

  def test_scales_with_length(self):
    """Roughly four characters per token."""
    assert cs.est_tokens("x" * 400) == 100
