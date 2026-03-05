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
**In-scope repos** (make changes here):
$in_scope_repos

**Reference repos** (read-only context):
$reference_repos

## Building
$build_section

## Context Packets
Read these files for background as needed:
$context_packets

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
