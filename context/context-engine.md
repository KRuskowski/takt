# Context Engine

takt's retrieval layer over its own knowledge: the markdown in `context/` and `templates/`, plus facts recorded by agents as they work. Lives in `lib/context_store.py`, backed by SQLite FTS5 tables in the same `.state/takt.db` as all other state.

## Why it exists

The old `search` endpoint grepped for a substring across `context/` and `templates/` and returned filenames with line numbers. At 51 KB of corpus that was adequate. It stops being adequate for two reasons, and only one of them is size:

- **Granularity.** A filename-and-line hit makes the caller read the whole document to use the answer. Since the corpus is intended to grow to a few hundred files, that means pulling ~5 KB to answer a question whose answer is 40 lines long.
- **No write path.** Everything an agent learned during a run — a build flag that turned out to be required, an API that does not match its docs, a VM quirk — died with the session. The next agent rediscovered it. Nothing in the system could accumulate knowledge, only serve what a human had checked in.

So the engine does two things the grep could not: it retrieves **sections**, and it **accepts writes** with scope and provenance attached.

## Model

**Docs.** One indexed unit. Either a file (`context/architecture.md`) or a fact recorded by an agent (`facts/otc-sdk/ffmpeg-arm64.md`). Content-addressed by SHA-256, so re-indexing unchanged files is free.

**Chunks.** Docs are split at markdown headings. Each chunk carries its heading path (`Workstation Setup > Services > Verify`) and line range, so a result can be cited precisely or opened at the right offset. Sections over ~400 estimated tokens are split further at paragraph boundaries; a single oversized paragraph is kept whole rather than cut mid-sentence, because a truncated code block or table is worse than a long chunk. Headings inside fenced code blocks are not treated as headings.

**Scope.** Every doc is `global`, or attached to a specific `repo`, `workspace` or `target`. A repo-scoped search sees that repo's facts plus global ones, and nothing from other repos — asking about OTC.SDK must not surface something only true of Conveyor. Scoped results rank above global ones.

**Provenance.** Facts record which run and workspace produced them, when they were verified, and what superseded them. A superseded fact drops out of search but stays readable, so the record of what was once believed survives.

## Retrieval

BM25 ranking, with heading matches weighted 3× body matches.

Query handling is two-pass: every term is required first (precise), and if that returns nothing and there was more than one term, it retries requiring any term and lets BM25 rank (recall). Natural-language questions almost always contain a word the corpus does not use, and the fallback is what makes them work. A single term that misses returns nothing rather than noise.

User text never reaches FTS5 raw. Terms are extracted and individually quoted, so punctuation cannot produce a syntax error and a word like `NEAR` or `AND` is a literal rather than an operator. A double-quoted span in the input survives as a phrase.

## Tools

Through MCP (the primary interface):

| Tool | Purpose |
|------|---------|
| `search` | Ask a question, get ranked sections with heading path, line range, snippet and provenance. Optional scope and kind filters. |
| `record_fact` | Store something learned, scoped and attributed. Re-recording the same title updates in place. |
| `list_facts` | What is already known about a repo or workspace. |
| `supersede_fact` | Retire a fact in favour of a newer one. |
| `context_stats` | Index size and scope breakdown. |
| `reindex_context` | Re-scan the files. Only needed after editing them directly on disk. |

Over REST:

```
GET  /api/context/search?q=&scope_kind=&scope_value=&kind=&limit=
GET  /api/context/stats
GET  /api/context/docs?kind=&scope_kind=&scope_value=
POST /api/context/reindex
POST /api/context/facts
POST /api/context/supersede
```

These share the `/api/context` prefix with the `{name}` file reader. aiohttp matches static paths ahead of dynamic ones regardless of registration order, so they coexist; the only cost is that a context file named exactly `search`, `stats`, `docs`, `reindex`, `facts` or `supersede` (with no extension) would be unreachable by name.

## Freshness

The index is maintained in three places, so it should never need a manual reindex:

- `takt-service` reindexes at startup. Content hashing makes this near-free when nothing changed.
- `PUT /api/context/{name}` and `PUT /api/templates/{name}` reindex that file as part of the write.
- `reindex()` prunes docs whose file has disappeared. It only prunes file-sourced docs — recorded facts are never touched by a reindex.

## On vectors

Deliberately not implemented yet. At a few hundred files, BM25 over section-level chunks is the larger win, and the chunking decision matters more than keyword-versus-semantic. Embeddings are worth adding as a *second* retriever fused with BM25 (reciprocal rank fusion), not as a replacement — but building that before there is evidence of which real queries keyword search misses means tuning blind. `sqlite-vec` loads into the same database with no migration, so deferring costs nothing. The step before adding it is logging queries and their click-through, so there is something to evaluate against.

## Schema

Tables in `.state/takt.db` (see `_SCHEMA` in `lib/db.py`):

- `context_docs` — one row per doc: path, title, kind, scope, source, provenance, content hash, `superseded_by`
- `context_chunks` — section chunks with heading path and line range
- `context_fts` — FTS5 external-content index over `context_chunks`, kept in sync by three triggers
- `context_links` — outbound `[[wiki-style]]` references, resolved lazily so a link may point at a doc that does not exist yet

The FTS table uses `content='context_chunks'`, so chunk text is stored once. The delete and update triggers matter: without them, editing a doc orphans its old terms in the index, which grows without bound and would resurrect deleted text as a false match if a rowid were ever reused. `tests/test_context_store.py::TestUpsertDoc::test_stale_text_leaves_the_search_index` asserts against the index directly, because `search()` masks orphans behind its join and so cannot detect them.

## Tests

`tests/test_context_store.py` (78) and `tests/test_context_api.py` (19). The API tests drive a real aiohttp test server, so routing is exercised rather than just handler bodies.
