# Architecture Decision Records

## ADR-001: Workspace Name = Branch Name

**Status**: Accepted

**Context**: Need a way to tie together multiple repos working on the same feature/task.

**Decision**: The workspace name IS the branch name. All repos in a workspace use the same branch name.

**Consequences**: Simple mental model. One identifier to track across repos, tools, and git history.

---

## ADR-002: No Direct GitHub Push

**Status**: Accepted

**Context**: Agents need to push code, but we need a human gatekeeper before changes reach GitHub.

**Decision**: Agents push to origin (root repo at ~/dev/root/<repo>). The operator reviews and pushes to GitHub manually via `push_to_github.py`.

**Consequences**: Two-hop push (workspace -> root -> GitHub). Adds safety at the cost of one extra step.

---

## ADR-003: Session State in CLAUDE.md

**Status**: Accepted

**Context**: Agents need to persist progress between sessions and hand off context to the next pipeline stage.

**Decision**: Session state lives at the bottom of the workspace CLAUDE.md file. Agent always sees task + progress in one read.

**Consequences**: No separate state files to manage. CLAUDE.md gets longer over time but stays self-contained.

---

## ADR-004: Local Clones as Workspaces

**Status**: Accepted

**Context**: Need isolated working copies for parallel agents.

**Decision**: Workspaces are `git clone` from root repos (local filesystem clones). Origin of a workspace clone = root repo.

**Consequences**: Fast clones (local filesystem), natural git push path back to root repos. Each workspace is fully isolated.

---

## ADR-005: Pool Targets, Don't Clone

**Status**: Accepted

**Context**: Build/test targets (VMs, hardware) are RAM-limited. Can't run one per workspace.

**Decision**: Targets are pooled resources with file-based locks for exclusive access. Agents claim, use, and release.

**Consequences**: Only one agent can use a target at a time. Agents must handle claim failures gracefully.

---

## ADR-006: Progressive Context Disclosure

**Status**: Accepted

**Context**: Loading too much context upfront wastes tokens and confuses agents.

**Decision**: CLAUDE.md files are lean (<150 lines). They point to context packets and code files. Agents fetch what they need.

**Consequences**: Agents may need extra reads but get more relevant context. CLAUDE.md stays readable.
