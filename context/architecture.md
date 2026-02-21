# System Architecture

## Overview

This is a multi-agent pipeline orchestration system for running
Claude CLI agents in parallel across multi-repo projects.

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
3. **Work**: Agent modifies workspace clones, pushes to root repo
   (its origin).
4. **Watch**: `pipeline_watch.py` detects branch changes in root
   repos, triggers next pipeline stage.
5. **Push**: Operator reviews and pushes from root repos to GitHub
   (`push_to_github.py`).

## Key Invariant

**Workspace name = branch name.** This single identifier ties
together all repos in a workspace across the entire pipeline.

## Agent Context Layering

1. **Root repo CLAUDE.md** — project-level truth (architecture,
   conventions, build commands). Checked into each repo.
2. **Workspace CLAUDE.md** — task-level context (role, task,
   acceptance criteria, session state). Generated per workspace.
3. **Context packets** — reference docs in `context/` directory
   (architecture, decisions, environment). Agents read as needed.

## Pipeline Stages

```
feature-agent -> test-agent -> review-agent -> docs-agent -> push
```

Each stage is a separate workspace (or the same workspace with a
different role). Not every change needs all stages.

## Target Management

Build/test targets are pooled resources (VMs and hardware).
Managed via file-based locks for exclusive access.

```
claim -> up -> run -> down -> release
```
