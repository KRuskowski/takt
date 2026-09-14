# Changelog

All notable changes to takt are recorded here. The format is based on [Keep a Changelog](https://keepachangelog.com/), and this file is meant to be readable without diving into the code.

## [Unreleased]

### Added
- **Issue ledger**: takt now tracks which issues from GitHub/GitLab are assigned to which workspaces. Multiple planning agents can see what's already being worked on, preventing duplicate effort. Issues are claimed atomically — if two agents try to grab the same issue, the second one gets a conflict error. Available through the REST API, MCP tools, and the takt agent shell.
- **Base branch selection**: When creating a workspace, you can now specify which branch to cut from (e.g. `--base release/v11.x`). Previously, workspaces always branched from whatever the root repo happened to have checked out, which could silently pick the wrong starting point.

### Changed
- The takt assistant integration (MCP server) is now available in every Claude session on this machine, not just when working inside the takt project folder. It no longer needs to be approved separately for each project.
- The takt tools now come with clearer, more detailed descriptions, so the assistant understands what each one does and when to use it (managing workspaces, claiming build/test machines, running pipelines, reading run results, and more).
- The takt service now listens only on localhost (127.0.0.1) instead of all network interfaces.

### Fixed
- Workspaces no longer disable GPG commit signing. New workspaces inherit the global signing configuration, so all commits are signed by default. The generated workspace instructions now tell agents to sign commits and stop (instead of retrying) if signing fails.
- The generated build instructions for OTC.SDK workspaces now include the actual cmake build steps instead of incorrectly saying "prebuilt, do not build".
- The repo table in generated workspace docs now shows the correct default branch from the root repo, instead of whatever stale branch the clone's origin/HEAD pointed to.
- The workspace repo table now shows the correct push order from repos.yaml (was always showing "?" due to a config lookup bug).
- Fixed a crash that took down the web UI when clicking **Reconnect** on a workspace's management tab. If a workspace terminal had ended on its own, reconnecting tore down the whole server (and every other open tab with it). The terminal connection is now safely cleaned up and restarted instead.
- Fixed a crash during web UI shutdown/restart. The background status poller could outlive the event channel it published to, crashing the server as it stopped. Shutdown now stops the poller first, so restarts are clean.
- Fixed the CLI and workspace terminals failing to connect in the web UI (immediate "reconnect" button). Spawned child processes (takt-cli, tmux) were inheriting the web server's listen socket, stealing incoming browser connections. Child processes now close inherited file descriptors before launching.
- Fixed the takt agent silently dropping responses on longer prompts. The Claude SDK didn't handle a new message type (`rate_limit_event`), causing the entire response stream to abort. The agent now skips unknown message types and delivers the full response.
- Build leftovers and cache folders are no longer tracked, keeping the project tidy.

## Earlier work

A summary of recent improvements, in plain terms:

- **Terminal and live view**: Replaced the old terminal bridge with a more reliable one, fixed window sizing, and made the live output connection sturdier so it survives reconnects and doesn't drop messages.
- **Web dashboard**: Redesigned the dashboard into a compact billboard view, added an in-browser editor, and fixed assorted display and reconnection glitches.
- **Setup and services**: Added a setup script and background services so takt can run continuously, with services bound to the local machine for safety.
- **Workspace health**: Added a one-glance health check for any workspace (how far behind it is, whether secrets slipped into the changes, and how the last run went).
- **Build system**: Added native builds for the takt command-line tool and web UI.
