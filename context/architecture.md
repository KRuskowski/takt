# System Architecture

## Overview

This is a multi-agent pipeline orchestration system for running Claude CLI agents in parallel across multi-repo projects.

## Repository Layout

```
GitHub (upstream)
  |
  v
~/dev/root/<repo>     Root repos (local mirrors of GitHub)
  |
  v
~/dev/workspaces/     Workspace clones (one per task/feature)
  └── feature-x/
      ├── repo-a/     Clone of ~/dev/root/repo-a, branch: feature-x
      ├── repo-b/     Clone of ~/dev/root/repo-b, branch: feature-x
      └── CLAUDE.md   Workspace-level instructions
```

## Data Flow

1. **Pull**: GitHub -> root repos (manual `git pull`).
2. **Clone**: Root repos -> workspace clones (`workspace.py create`).
3. **Work**: Agent writes and commits the implementation in the workspace clones, pushes to root repo (its origin). All building happens here.
4. **Watch**: `pipeline_watch.py` detects branch changes in root repos and creates a pipeline run, which verifies and documents the now-committed work.
5. **Push**: Operator reviews and pushes from root repos to GitHub (`push_to_github.py`).

## Key Invariant

**Workspace name = branch name.** This single identifier ties together all repos in a workspace across the entire pipeline.

## Agent Context Layering

1. **Root repo CLAUDE.md** — project-level truth (architecture, conventions, build commands). Checked into each repo.
2. **Workspace CLAUDE.md** — task-level context (role, task, acceptance criteria, session state). Generated per workspace.
3. **Context packets** — reference docs in `context/` directory (architecture, decisions, environment). Agents read as needed.

## Pipeline Stages

A pipeline is **what happens to finished work**. Its stages verify, document and ship an implementation that already exists and is already committed. A pipeline does not build anything.

```
(implementation committed in the workspace)
        |
        v
test -> review -> docs -> deploy_qa -> push -> create_pr
```

Each stage runs in a temporary worktree created from what is **already committed in the root repos**. A stage therefore cannot see uncommitted work, and is never handed a task to implement. Triggering a run will not produce a feature, a fix or a build.

Not every change needs all stages.

`feature` and `refactor` roles still exist for pipelines configured with them historically, but they contradict the above and should not be added to new pipelines — implementation belongs in the workspace, before the run. See `templates/pipeline_roles.md`.

## Target Management

Build/test targets are pooled resources (VMs and hardware). Managed via file-based locks for exclusive access.

```
claim -> up -> run -> down -> release
```
