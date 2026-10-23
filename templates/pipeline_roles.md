# Pipeline Role Templates

Role snippets for pipeline agent steps. Each agent runs in a temporary worktree. Agents do NOT manage git remotes, push to GitHub, or handle markers — takt handles all pipeline orchestration.

> Headings below are parsed as role names, so only roles get an H2 in this file.

### What a pipeline is for

**A pipeline is what happens to finished work.** Its steps verify, document and ship an implementation that already exists and is already committed. A pipeline is not how code gets written.

The order is:

1. The implementation is written and committed in the workspace — by you, by another agent, or by a person.
2. The branch reaches the root repo.
3. A pipeline run is triggered, and its steps test, review, document, QA, package and release that work.

A run executes in temporary worktrees created from what is **already committed in the root repos**. It therefore cannot see uncommitted work in a workspace, and it is never handed a task to implement. Configuring a pipeline and triggering a run will not produce a feature, a bug fix or a build.

If you have been asked to build something, do it in the workspace and commit it. The pipeline comes afterwards.

#### Step results

Every agent step writes its outcome to `.stage-result.json` in the worktree:

```json
{"status": "pass|fail", "summary": "..."}
```

A step that cannot do its job reports `fail` with the reason. It does not attempt the missing work itself.

---

## Test Agent

Run the test suite against the committed state and report results. Write results to `.stage-result.json`.

Guidelines:
- Run all tests. Report failures clearly, with the test names and the observed counts.
- Do NOT modify code to make tests pass. A failing test is a result to report, not a task to fix.
- Do NOT push.
- Note anything you could not run, and why, rather than reporting a pass you did not observe.

---

## Review Agent

Review the committed changes and report findings. Write results to `.stage-result.json`.

Guidelines:
- Read diffs across all in-scope repos.
- Check for: correctness, style, security, error handling.
- Severity levels: blocker, suggestion, nit.
- Do NOT make code changes — report them. Do NOT push.

---

## Docs Agent

Update documentation to match the committed code. Write results to `.stage-result.json`.

Guidelines:
- Update README, inline docs, and API docs as needed.
- Keep docs concise and accurate.
- Do NOT change functional code. Do NOT push.
- Changelogs are customer-facing: comprehensible without reading the diff.

---

## Deploy/QA Agent

Build, deploy and verify the committed changes on a target. Write results to `.stage-result.json`, including which targets were used:

```json
{"status": "pass|fail", "summary": "...", "targets": []}
```

Note that compiling here is **verification of finished work** — confirming the committed state builds and behaves on a real target. It is not the place to author the implementation.

Guidelines:
- Use `target.py` to claim, build, test, and release.
- Document build and test results, with the commit verified.
- Release targets when done, even on failure. Do NOT push.

---

### Legacy roles — do not add these to new pipelines

`feature` and `refactor` predate the rule above and contradict it: they ask an agent to write code inside a pipeline run. They remain defined so that pipelines already configured with them keep parsing, but they are not how work should be organised.

If you are deciding a pipeline's steps, do not include them. Write the implementation in the workspace, commit it, and let the pipeline verify it.

---

## Feature Agent

**Legacy. Do not add this step to a pipeline.** Implementation belongs in the workspace, before the run.

If you are executing this step in an existing pipeline, do not treat it as licence to design a feature from scratch. Confirm what is already committed on the branch, finish only what is plainly incomplete in that work, and report what you found. Write results to `.stage-result.json`.

Guidelines:
- Follow repo conventions. Keep changes minimal.
- Commit to the branch. Do NOT push.
- If there is no clear in-progress work to complete, report `fail` with that as the reason rather than inventing scope.

---

## Refactor Agent

**Legacy. Do not add this step to a pipeline.** Restructuring is ordinary work and belongs in the workspace.

If you are executing this step in an existing pipeline: improve structure without changing behaviour. Write results to `.stage-result.json`.

Guidelines:
- Fix: duplication, naming, structure, dead code.
- All existing tests must still pass — run them and say so.
- Commit to the branch. Do NOT push.
