# Workspace: $workspace_name

## Role
$role_section

## Task
$task_section

## Acceptance Criteria
$acceptance_criteria

## Scope
**In-scope repos** (make changes here):
$in_scope_repos

**Reference repos** (read-only context):
$reference_repos

## Context Packets
Read these files for background as needed:
$context_packets

## Repos in This Workspace
| Repo | Default Branch | Push Order |
|------|---------------|------------|
$repo_table

## Git Rules
- Branch name: `$workspace_name` (same across all repos)
- Push to origin only (origin = root repo at ~/dev/<repo>)
- NEVER push to GitHub. The operator handles GitHub pushes.
- Sign all commits.
- Push order follows dependency chain (upstream first).

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
