"""Structured context store for takt.

Retrieval over takt's knowledge: the markdown under context/
and templates/, plus facts recorded by agents during runs.

Two things distinguish this from grepping the files:

  Chunks, not files. Documents are split at markdown
  headings, so a search returns the one relevant section
  with its heading path and line range instead of a whole
  document the caller has to re-read.

  Scope and provenance. Every doc carries a scope (global,
  or a specific repo/workspace/target) and, for facts, the
  run and workspace that produced it. Retrieval can narrow
  to "what do we know about OTC.SDK" and callers can see
  where a claim came from and whether it was superseded.

Backed by SQLite FTS5 in the same .state/takt.db as the
rest of takt's state — no extra service, no index to keep
on disk separately.
"""

import hashlib
import re

from lib.config import CONTEXT_DIR, TEMPLATES_DIR
from lib.db import _connect

# Scopes a doc can be attached to. 'global' means it applies
# everywhere; the others pair with a scope_value naming the
# repo, workspace or target.
VALID_SCOPES = ("global", "repo", "workspace", "target")

# A chunk above this many estimated tokens is split further
# at paragraph boundaries. Sized so a handful of chunks fit
# comfortably in a prompt alongside the actual task.
MAX_CHUNK_TOKENS = 400

# Directories indexed by reindex(), as (label, path) pairs.
# The label becomes the first path segment, so a doc is
# addressed as 'context/architecture.md'.
INDEXED_DIRS = (
  ("context", CONTEXT_DIR),
  ("templates", TEMPLATES_DIR),
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
# Matches a quoted phrase, or a run of word characters.
_TERM_RE = re.compile(r'"[^"]+"|[^\W_]+', re.UNICODE)


def est_tokens(text):
  """Estimate token count for a string.

  Deliberately crude — four characters per token is close
  enough to size chunks, and avoids a tokenizer dependency.

  Args:
    text: String to measure.

  Returns:
    Estimated token count, at least 1 for non-empty input.
  """
  if not text:
    return 0
  return max(1, len(text) // 4)


def content_hash(text):
  """Return a stable hash of document content."""
  return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _paragraphs(body, start_line):
  """Group body lines into paragraphs with line numbers.

  Args:
    body: Section text.
    start_line: Line number of body's first line.

  Returns:
    List of (text, first_line, last_line) tuples.
  """
  lines = body.split("\n")
  out = []
  cur = []
  cur_start = None
  for idx, line in enumerate(lines):
    line_no = start_line + idx
    if line.strip():
      if cur_start is None:
        cur_start = line_no
      cur.append(line)
      continue
    if cur:
      out.append(("\n".join(cur), cur_start, line_no - 1))
      cur = []
      cur_start = None
  if cur:
    out.append((
      "\n".join(cur), cur_start,
      start_line + len(lines) - 1,
    ))
  return out


def _split_paragraphs(body, start_line, max_tokens):
  """Split an oversized section body into sub-chunks.

  Packs whole paragraphs until the token budget is reached.
  A single paragraph over budget is emitted whole rather
  than cut mid-sentence — a truncated code block or table
  is worse than a long chunk.

  Args:
    body: Section text.
    start_line: Line number of body's first line.
    max_tokens: Target ceiling per sub-chunk.

  Returns:
    List of (text, first_line, last_line) tuples.
  """
  out = []
  buf = []
  buf_start = None
  buf_end = None
  for text, first, last in _paragraphs(body, start_line):
    if buf and est_tokens(
      "\n\n".join(buf + [text])
    ) > max_tokens:
      out.append(("\n\n".join(buf), buf_start, buf_end))
      buf = [text]
      buf_start, buf_end = first, last
      continue
    if not buf:
      buf_start = first
    buf.append(text)
    buf_end = last
  if buf:
    out.append(("\n\n".join(buf), buf_start, buf_end))
  return out


def split_markdown(text, max_tokens=MAX_CHUNK_TOKENS):
  """Split markdown into section-level chunks.

  Sections are delimited by ATX headings (``#``..``######``).
  Headings inside fenced code blocks are ignored. Content
  before the first heading becomes a leading chunk. Each
  chunk carries the heading path above it, so a section
  called "Verify" is retrievable as "Setup > Services >
  Verify".

  Args:
    text: Markdown source.
    max_tokens: Chunks larger than this are split further
      at paragraph boundaries.

  Returns:
    List of dicts with keys: heading, heading_path, body,
    start_line, end_line.
  """
  lines = text.split("\n")
  # (level, title) for the headings currently in scope.
  stack = []
  sections = []
  cur = {"heading": "", "path": "", "lines": [], "start": 1}
  in_fence = False

  def close(end_line):
    """Finish the section under construction."""
    body = "\n".join(cur["lines"]).strip("\n")
    if body.strip() or cur["heading"]:
      sections.append({
        "heading": cur["heading"],
        "heading_path": cur["path"],
        "body": body,
        "start_line": cur["start"],
        "end_line": max(cur["start"], end_line),
      })

  for i, line in enumerate(lines, start=1):
    if _FENCE_RE.match(line):
      in_fence = not in_fence
      cur["lines"].append(line)
      continue
    m = None if in_fence else _HEADING_RE.match(line)
    if not m:
      cur["lines"].append(line)
      continue
    close(i - 1)
    level = len(m.group(1))
    title = m.group(2).strip()
    while stack and stack[-1][0] >= level:
      stack.pop()
    stack.append((level, title))
    cur = {
      "heading": title,
      "path": " > ".join(t for _, t in stack),
      "lines": [],
      "start": i,
    }
  close(len(lines))

  chunks = []
  for sec in sections:
    if est_tokens(sec["body"]) <= max_tokens:
      chunks.append(sec)
      continue
    for body, first, last in _split_paragraphs(
      sec["body"], sec["start_line"] + 1, max_tokens
    ):
      if not body.strip():
        continue
      chunks.append({
        "heading": sec["heading"],
        "heading_path": sec["heading_path"],
        "body": body,
        "start_line": first,
        "end_line": last,
      })
  return chunks


def doc_title(path, text):
  """Derive a display title for a document.

  Uses the first H1 if present, else the filename stem with
  dashes and underscores turned into spaces.

  Args:
    path: Document path, e.g. 'context/architecture.md'.
    text: Document content.

  Returns:
    Title string.
  """
  for line in text.split("\n")[:40]:
    m = _HEADING_RE.match(line)
    if m and len(m.group(1)) == 1:
      return m.group(2).strip()
  stem = path.rsplit("/", 1)[-1]
  if stem.endswith(".md"):
    stem = stem[:-3]
  return stem.replace("-", " ").replace("_", " ")


def _validate_scope(scope_kind, scope_value):
  """Check a scope pair, raising ValueError if invalid."""
  if scope_kind not in VALID_SCOPES:
    raise ValueError(
      f"invalid scope_kind {scope_kind!r}; "
      f"expected one of {', '.join(VALID_SCOPES)}"
    )
  if scope_kind != "global" and not scope_value:
    raise ValueError(
      f"scope_kind {scope_kind!r} requires a scope_value"
    )


def upsert_doc(path, text, kind="doc", scope_kind="global",
               scope_value="", source="file", source_run=None,
               source_workspace=None, title=None,
               db_path=None):
  """Index a document, replacing any previous version.

  Content-addressed: if the stored hash already matches,
  the document and its chunks are left untouched and the
  status is 'unchanged'. This makes reindex() cheap to run
  on every service start.

  Args:
    path: Stable identifier, e.g. 'context/decisions.md'.
    text: Document content.
    kind: 'doc' for files, 'fact' for recorded knowledge.
    scope_kind: One of VALID_SCOPES.
    scope_value: Repo/workspace/target name, or ''.
    source: 'file', 'agent' or 'run'.
    source_run: Run id that produced this, if any.
    source_workspace: Workspace that produced this, if any.
    title: Override the derived title.
    db_path: Override path for testing.

  Returns:
    Dict with keys: doc_id, status ('created', 'updated' or
    'unchanged'), chunks.
  """
  _validate_scope(scope_kind, scope_value)
  digest = content_hash(text)
  resolved_title = title or doc_title(path, text)
  with _connect(db_path) as conn:
    row = conn.execute(
      "SELECT id, content_hash FROM context_docs "
      "WHERE path = ?",
      (path,),
    ).fetchone()
    if row and row["content_hash"] == digest:
      n = conn.execute(
        "SELECT COUNT(*) FROM context_chunks "
        "WHERE doc_id = ?",
        (row["id"],),
      ).fetchone()[0]
      return {
        "doc_id": row["id"],
        "status": "unchanged",
        "chunks": n,
      }
    if row:
      doc_id = row["id"]
      conn.execute(
        "UPDATE context_docs SET title = ?, kind = ?, "
        "scope_kind = ?, scope_value = ?, source = ?, "
        "source_run = ?, source_workspace = ?, "
        "content_hash = ?, updated_at = "
        "strftime('%Y-%m-%dT%H:%M:%fZ','now') "
        "WHERE id = ?",
        (resolved_title, kind, scope_kind, scope_value,
         source, source_run, source_workspace, digest,
         doc_id),
      )
      # Deleting the chunks fires the FTS delete trigger,
      # so the index never keeps stale rows.
      conn.execute(
        "DELETE FROM context_chunks WHERE doc_id = ?",
        (doc_id,),
      )
      conn.execute(
        "DELETE FROM context_links WHERE src_doc = ?",
        (doc_id,),
      )
      status = "updated"
    else:
      cur = conn.execute(
        "INSERT INTO context_docs "
        "(path, title, kind, scope_kind, scope_value, "
        " source, source_run, source_workspace, "
        " content_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (path, resolved_title, kind, scope_kind,
         scope_value, source, source_run,
         source_workspace, digest),
      )
      doc_id = cur.lastrowid
      status = "created"

    chunks = split_markdown(text)
    for seq, ch in enumerate(chunks):
      conn.execute(
        "INSERT INTO context_chunks "
        "(doc_id, seq, heading, heading_path, body, "
        " start_line, end_line) VALUES (?,?,?,?,?,?,?)",
        (doc_id, seq, ch["heading"], ch["heading_path"],
         ch["body"], ch["start_line"], ch["end_line"]),
      )
    for target in sorted(set(_LINK_RE.findall(text))):
      conn.execute(
        "INSERT OR IGNORE INTO context_links "
        "(src_doc, target) VALUES (?,?)",
        (doc_id, target.strip()),
      )
    return {
      "doc_id": doc_id,
      "status": status,
      "chunks": len(chunks),
    }


def delete_doc(path, db_path=None):
  """Remove a document and its chunks from the index.

  Args:
    path: Document path.
    db_path: Override path for testing.

  Returns:
    True if a document was removed.
  """
  with _connect(db_path) as conn:
    row = conn.execute(
      "SELECT id FROM context_docs WHERE path = ?",
      (path,),
    ).fetchone()
    if not row:
      return False
    # Explicit, so the FTS delete trigger fires even if
    # foreign keys are off.
    conn.execute(
      "DELETE FROM context_chunks WHERE doc_id = ?",
      (row["id"],),
    )
    conn.execute(
      "DELETE FROM context_docs WHERE id = ?", (row["id"],)
    )
    return True


def _scope_for_file(label, name):
  """Infer a scope for an indexed file.

  Files under context/ and templates/ are project-wide
  reference material, so they index as global. Per-repo and
  per-workspace knowledge arrives through record_fact().

  Args:
    label: Directory label ('context' or 'templates').
    name: File name.

  Returns:
    (scope_kind, scope_value) tuple.
  """
  del label, name
  return ("global", "")


def reindex(dirs=None, db_path=None):
  """Sync the index with the files on disk.

  Adds new files, re-chunks changed ones, skips unchanged
  ones, and drops docs whose file has disappeared. Only
  file-sourced docs are pruned — recorded facts are never
  touched.

  Args:
    dirs: Override the (label, path) pairs to index.
    db_path: Override path for testing.

  Returns:
    Dict with counts: created, updated, unchanged, removed,
    chunks, and the list of indexed paths.
  """
  targets = dirs if dirs is not None else INDEXED_DIRS
  stats = {
    "created": 0, "updated": 0, "unchanged": 0,
    "removed": 0, "chunks": 0, "paths": [],
  }
  seen = set()
  for label, directory in targets:
    if not directory or not directory.exists():
      continue
    for f in sorted(directory.iterdir()):
      if not f.is_file() or f.suffix.lower() != ".md":
        continue
      path = f"{label}/{f.name}"
      try:
        text = f.read_text()
      except (OSError, UnicodeDecodeError):
        continue
      scope_kind, scope_value = _scope_for_file(label, f.name)
      res = upsert_doc(
        path, text, kind="doc", scope_kind=scope_kind,
        scope_value=scope_value, source="file",
        db_path=db_path,
      )
      stats[res["status"]] += 1
      stats["chunks"] += res["chunks"]
      stats["paths"].append(path)
      seen.add(path)

  labels = tuple(label for label, _ in targets)
  with _connect(db_path) as conn:
    rows = conn.execute(
      "SELECT path FROM context_docs "
      "WHERE source = 'file'"
    ).fetchall()
  for row in rows:
    path = row["path"]
    if path in seen:
      continue
    if not path.startswith(tuple(f"{p}/" for p in labels)):
      continue
    if delete_doc(path, db_path=db_path):
      stats["removed"] += 1
  return stats


def query_terms(query):
  """Extract quoted FTS5 terms from user text.

  Raw user text cannot go to MATCH — bare punctuation and
  the bareword operators (AND/OR/NOT/NEAR) make FTS5 raise
  a syntax error. Terms are extracted and quoted instead,
  so a term like "NEAR" is a literal, not an operator.
  Double-quoted spans in the input survive as phrases.

  Args:
    query: User query text.

  Returns:
    List of quoted term strings, possibly empty.
  """
  terms = []
  for raw in _TERM_RE.findall(query or ""):
    term = raw.strip('"').strip()
    if not term:
      continue
    terms.append('"%s"' % term.replace('"', ""))
  return terms


def build_match(query, operator="AND"):
  """Turn a user query into an FTS5 MATCH expression.

  Args:
    query: User query text.
    operator: 'AND' to require every term, 'OR' for any.

  Returns:
    MATCH expression, or '' if the query has no usable
    terms.
  """
  return f" {operator} ".join(query_terms(query))


def search(query, scope_kind=None, scope_value=None,
           kind=None, limit=10, include_superseded=False,
           db_path=None):
  """Search the context index, best match first.

  Ranked with BM25, weighting heading matches above body
  matches. When a scope is given, results for that scope
  sort ahead of global ones, and other scopes are excluded
  — asking about OTC.SDK should not surface a fact that is
  only true of Conveyor.

  Args:
    query: Free text. Quote a span to require a phrase.
    scope_kind: Restrict to this scope plus 'global'.
    scope_value: Scope name, required unless the kind is
      'global'.
    kind: 'doc' or 'fact' to restrict by document kind.
    limit: Maximum results.
    include_superseded: Include docs replaced by a newer
      version.
    db_path: Override path for testing.

  Returns:
    List of dicts: path, title, kind, scope_kind,
    scope_value, heading, heading_path, start_line,
    end_line, snippet, body, score, chunk_id, matched
    ('all' or 'any').
  """
  terms = query_terms(query)
  if not terms:
    return []
  # Require every term first, which is precise. A natural
  # language question usually contains a word the corpus
  # does not use, so fall back to matching any term and let
  # BM25 rank — chunks containing more of the terms come
  # out on top anyway. Precision when it is available,
  # recall when it is not.
  rows = _search_match(
    " AND ".join(terms), scope_kind, scope_value, kind,
    limit, include_superseded, db_path,
  )
  matched = "all"
  if not rows and len(terms) > 1:
    rows = _search_match(
      " OR ".join(terms), scope_kind, scope_value, kind,
      limit, include_superseded, db_path,
    )
    matched = "any"
  for r in rows:
    r["matched"] = matched
  return rows


def _search_match(match, scope_kind, scope_value, kind,
                  limit, include_superseded, db_path):
  """Run one MATCH expression against the index.

  Args:
    match: FTS5 MATCH expression.
    scope_kind: Restrict to this scope plus 'global'.
    scope_value: Scope name.
    kind: 'doc' or 'fact'.
    limit: Maximum results.
    include_superseded: Include retired docs.
    db_path: Override path for testing.

  Returns:
    List of result dicts, best match first.
  """
  where = ["context_fts MATCH ?"]
  params = [match]
  if not include_superseded:
    where.append("d.superseded_by IS NULL")
  if kind:
    where.append("d.kind = ?")
    params.append(kind)
  if scope_kind:
    _validate_scope(scope_kind, scope_value)
    if scope_kind == "global":
      where.append("d.scope_kind = 'global'")
    else:
      where.append(
        "(d.scope_kind = 'global' OR "
        " (d.scope_kind = ? AND d.scope_value = ?))"
      )
      params.extend([scope_kind, scope_value])
  sql = (
    "SELECT c.id AS chunk_id, d.path, d.title, d.kind, "
    "       d.scope_kind, d.scope_value, d.source, "
    "       d.source_run, d.source_workspace, "
    "       d.updated_at, c.heading, c.heading_path, "
    "       c.body, c.start_line, c.end_line, "
    "       snippet(context_fts, 1, '<<', '>>', ' … ', 14) "
    "         AS snippet, "
    "       bm25(context_fts, 3.0, 1.0) AS score, "
    "       CASE WHEN d.scope_kind = 'global' "
    "            THEN 1 ELSE 0 END AS scope_rank "
    "FROM context_fts "
    "JOIN context_chunks c ON c.id = context_fts.rowid "
    "JOIN context_docs d ON d.id = c.doc_id "
    "WHERE " + " AND ".join(where) + " "
    "ORDER BY scope_rank ASC, score ASC "
    "LIMIT ?"
  )
  params.append(int(limit))
  with _connect(db_path) as conn:
    rows = conn.execute(sql, params).fetchall()
  return [dict(r) for r in rows]


def get_doc(path, db_path=None):
  """Fetch a document with its chunks.

  Args:
    path: Document path.
    db_path: Override path for testing.

  Returns:
    Dict with doc metadata plus 'chunks' and 'links', or
    None if not found.
  """
  with _connect(db_path) as conn:
    row = conn.execute(
      "SELECT * FROM context_docs WHERE path = ?", (path,)
    ).fetchone()
    if not row:
      return None
    doc = dict(row)
    doc["chunks"] = [
      dict(r) for r in conn.execute(
        "SELECT seq, heading, heading_path, body, "
        "start_line, end_line FROM context_chunks "
        "WHERE doc_id = ? ORDER BY seq",
        (row["id"],),
      ).fetchall()
    ]
    doc["links"] = [
      r["target"] for r in conn.execute(
        "SELECT target FROM context_links "
        "WHERE src_doc = ? ORDER BY target",
        (row["id"],),
      ).fetchall()
    ]
  doc["text"] = "\n\n".join(
    c["body"] for c in doc["chunks"] if c["body"].strip()
  )
  return doc


def record_fact(body, scope_kind="global", scope_value="",
                title=None, source="agent", source_run=None,
                source_workspace=None, path=None,
                verified=True, db_path=None):
  """Record a fact learned during work.

  The write half of the engine. An agent that discovers
  something durable — a build flag that matters, a VM that
  needs a quirk, an API that does not behave as documented
  — stores it here with its scope and provenance instead of
  leaving it in a run log nobody reads again.

  Re-recording the same path updates it in place, so an
  agent can correct itself without creating a duplicate.

  Args:
    body: The fact, as markdown. A leading '# Title' line
      is used as the title if none is given.
    scope_kind: One of VALID_SCOPES.
    scope_value: Repo/workspace/target this applies to.
    title: Short summary. Derived from body if omitted.
    source: 'agent' or 'run'.
    source_run: Run id that produced this.
    source_workspace: Workspace that produced this.
    path: Stable id. Defaults to a slug of the title under
      'facts/', so re-recording the same subject updates
      rather than duplicates.
    verified: Stamp verified_at with the current time.
    db_path: Override path for testing.

  Returns:
    Dict with doc_id, path, status, chunks.
  """
  if not body or not body.strip():
    raise ValueError("fact body is empty")
  _validate_scope(scope_kind, scope_value)
  resolved_title = title or doc_title("fact", body)
  doc_path = path or _fact_path(resolved_title, scope_value)
  res = upsert_doc(
    doc_path, body, kind="fact", scope_kind=scope_kind,
    scope_value=scope_value, source=source,
    source_run=source_run,
    source_workspace=source_workspace,
    title=resolved_title, db_path=db_path,
  )
  if verified:
    with _connect(db_path) as conn:
      conn.execute(
        "UPDATE context_docs SET verified_at = "
        "strftime('%Y-%m-%dT%H:%M:%fZ','now') "
        "WHERE id = ?",
        (res["doc_id"],),
      )
  res["path"] = doc_path
  return res


def _fact_path(title, scope_value=""):
  """Build a stable 'facts/...' path from a fact title."""
  slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
  slug = slug[:60] or "fact"
  if scope_value:
    prefix = re.sub(
      r"[^a-z0-9]+", "-", scope_value.lower()
    ).strip("-")
    return f"facts/{prefix}/{slug}.md"
  return f"facts/{slug}.md"


def supersede(path, by_path, db_path=None):
  """Mark a doc as replaced by another.

  Superseded docs drop out of search by default but stay
  readable, so the record of what was once believed — and
  what replaced it — survives.

  Args:
    path: Doc being retired.
    by_path: Doc that replaces it.
    db_path: Override path for testing.

  Returns:
    True if the link was recorded.
  """
  with _connect(db_path) as conn:
    old = conn.execute(
      "SELECT id FROM context_docs WHERE path = ?", (path,)
    ).fetchone()
    new = conn.execute(
      "SELECT id FROM context_docs WHERE path = ?",
      (by_path,),
    ).fetchone()
    if not old or not new:
      return False
    if old["id"] == new["id"]:
      raise ValueError("a doc cannot supersede itself")
    conn.execute(
      "UPDATE context_docs SET superseded_by = ?, "
      "updated_at = "
      "strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ?",
      (new["id"], old["id"]),
    )
    return True


def list_docs(kind=None, scope_kind=None, scope_value=None,
              include_superseded=False, limit=200,
              db_path=None):
  """List indexed documents, newest first.

  Args:
    kind: 'doc' or 'fact'.
    scope_kind: Restrict to this scope exactly.
    scope_value: Scope name.
    include_superseded: Include retired docs.
    limit: Maximum rows.
    db_path: Override path for testing.

  Returns:
    List of doc metadata dicts with a chunk count.
  """
  where = []
  params = []
  if kind:
    where.append("d.kind = ?")
    params.append(kind)
  if scope_kind:
    where.append("d.scope_kind = ?")
    params.append(scope_kind)
    if scope_value:
      where.append("d.scope_value = ?")
      params.append(scope_value)
  if not include_superseded:
    where.append("d.superseded_by IS NULL")
  clause = ("WHERE " + " AND ".join(where)) if where else ""
  params.append(int(limit))
  with _connect(db_path) as conn:
    rows = conn.execute(
      "SELECT d.*, "
      "(SELECT COUNT(*) FROM context_chunks c "
      " WHERE c.doc_id = d.id) AS chunks "
      f"FROM context_docs d {clause} "
      "ORDER BY d.updated_at DESC, d.id DESC LIMIT ?",
      params,
    ).fetchall()
  return [dict(r) for r in rows]


def stats(db_path=None):
  """Summarise index size.

  Args:
    db_path: Override path for testing.

  Returns:
    Dict with docs, facts, chunks, est_tokens, scopes.
  """
  with _connect(db_path) as conn:
    docs = conn.execute(
      "SELECT COUNT(*) FROM context_docs "
      "WHERE kind = 'doc'"
    ).fetchone()[0]
    facts = conn.execute(
      "SELECT COUNT(*) FROM context_docs "
      "WHERE kind = 'fact'"
    ).fetchone()[0]
    chunks = conn.execute(
      "SELECT COUNT(*) FROM context_chunks"
    ).fetchone()[0]
    chars = conn.execute(
      "SELECT COALESCE(SUM(LENGTH(body)), 0) "
      "FROM context_chunks"
    ).fetchone()[0]
    scopes = [
      dict(r) for r in conn.execute(
        "SELECT scope_kind, scope_value, COUNT(*) AS docs "
        "FROM context_docs GROUP BY scope_kind, "
        "scope_value ORDER BY docs DESC"
      ).fetchall()
    ]
  return {
    "docs": docs,
    "facts": facts,
    "chunks": chunks,
    "est_tokens": chars // 4,
    "scopes": scopes,
  }
