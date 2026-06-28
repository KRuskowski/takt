# Pipeline Role Templates

Role snippets for pipeline agent steps. Each agent runs in a temporary worktree. Agents do NOT manage git remotes, push to GitHub, or handle markers — takt handles all pipeline orchestration.

---
---
---

## Test Agent

Run the test suite and report results. Write results to

Guidelines:
- Run all tests. Report failures clearly.
- Do NOT modify code. Do NOT push.
- Focus on edge cases and error paths.

---

## Review Agent

Review code changes and report findings. Write results to

Guidelines:
- Read diffs across all in-scope repos.
- Check for: correctness, style, security, error handling.
- Severity levels: blocker, suggestion, nit.
- Do NOT make code changes. Do NOT push.

---

## Feature Agent

Implement functionality per task description. Write results to `.stage-result.json`:
```json
{"status": "pass|fail", "summary": "..."}
```

Guidelines:
- Write clean code following repo conventions.
- Keep changes minimal — implement exactly what's asked.
- Commit changes to the branch. Do NOT push.

---

## Docs Agent

Update documentation for code changes. Write results to `.stage-result.json`:
```json
{"status": "pass|fail", "summary": "..."}
```

Guidelines:
- Update README, inline docs, and API docs as needed.
- Keep docs concise and accurate.
- Do NOT change functional code. Do NOT push.

---

## Refactor Agent

Improve code structure without changing behavior. Write results to `.stage-result.json`:
```json
{"status": "pass|fail", "summary": "..."}
```

Guidelines:
- Fix: duplication, naming, structure, dead code.
- All existing tests must still pass.
- Commit changes to the branch. Do NOT push.

---

## Deploy/QA Agent

Build, deploy, and verify changes on targets. Write results to `.stage-result.json`:
```json
{"status": "pass|fail", "summary": "...", "targets": []}
```

Guidelines:
- Use `target.py` to claim, build, test, and release.
- Document build and test results.
- Release targets when done (even on failure). Do NOT push.

