# Workspace: $workspace_name

## Role
$role_section

## Task
$task_section

## Acceptance Criteria
$acceptance_criteria

## Boundaries
- You may ONLY modify files inside this workspace
- NEVER modify files outside ~/dev/workspaces/$workspace_name/
- NEVER push to GitHub — the operator handles that
- NEVER run interactive auth (gh auth, ssh-add, etc.)
- If credentials are missing, report the blocker and stop

## Style
- Google style guide, 80 character line limit, 2 space indents
- No double newlines between top-level definitions in Python
- C++: follow .clang-format in repo

## Git
$git_rules

## Scope
**In-scope repos** (make changes here): $in_scope_repos

**Reference repos** (read-only context): $reference_repos

## Deployment Target
Each workspace gets an exclusive VM for building and running. Claim your target before deploying:

```bash
takt target claim <vm-name> $workspace_name
takt target run <vm-name> "<command>"
```

Available targets: run `~/dev/takt/bin/target.py list` for the live inventory and current claims. Templates are read-only — clone via `~/dev/takt/bin/clone_vm.py` for parallel work; clone IPs start at 10.101.0.100.

- ALWAYS deploy and run services on your claimed VM, never on the host
- Use `takt target run` or SSH directly (`ssh worker@<ip>`)
- Release the target when done: `takt target release <vm-name>`
- If all VMs are claimed, report the blocker and stop

## Building
$build_section

## Pipelines come after you, and they are the route upstream
The work described above happens **here, in this workspace** — you write it and you commit it.

A takt pipeline runs **post-implementation**, and it is how that work gets upstream: it tests, reviews, documents and QAs what you committed, then pushes it and opens a PR. A pipeline never implements anything.

So do not set up a pipeline, trigger a run, or wait on one in order to get this task done. A run executes in temporary worktrees built from what is already committed in the root repos — it cannot see your uncommitted work, and it is not given a task to implement. If you are blocked on implementing, report the blocker; a pipeline will not unblock it.

## Context Packets
Read these files for background as needed: $context_packets

## Repos in This Workspace
| Repo | Default Branch | Push Order |
|------|---------------|------------|
$repo_table

$pipeline_section
---

## Session State
_Updated by the agent at the end of each session._

### Status
$status

### Completed
- (none yet)

### In Progress
- (none yet)

### Decisions
- (none yet)

### Blockers
- (none)

### Next Steps
- Review task description and acceptance criteria
- Read relevant context packets
- Begin implementation
