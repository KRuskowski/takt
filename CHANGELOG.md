# Changelog

All notable changes to takt are recorded here. The format is based on [Keep a Changelog](https://keepachangelog.com/), and this file is meant to be readable without diving into the code.

## [Unreleased]

### Changed
- The takt assistant integration (MCP server) is now available in every Claude session on this machine, not just when working inside the takt project folder. It no longer needs to be approved separately for each project.
- The takt tools now come with clearer, more detailed descriptions, so the assistant understands what each one does and when to use it (managing workspaces, claiming build/test machines, running pipelines, reading run results, and more).

### Fixed
- Fixed a crash that took down the web UI when clicking **Reconnect** on a workspace's management tab. If a workspace terminal had ended on its own, reconnecting tore down the whole server (and every other open tab with it). The terminal connection is now safely cleaned up and restarted instead.
- Build leftovers and cache folders are no longer tracked, keeping the project tidy.

## Earlier work

A summary of recent improvements, in plain terms:

- **Terminal and live view**: Replaced the old terminal bridge with a more reliable one, fixed window sizing, and made the live output connection sturdier so it survives reconnects and doesn't drop messages.
- **Web dashboard**: Redesigned the dashboard into a compact billboard view, added an in-browser editor, and fixed assorted display and reconnection glitches.
- **Setup and services**: Added a setup script and background services so takt can run continuously, with services bound to the local machine for safety.
- **Workspace health**: Added a one-glance health check for any workspace (how far behind it is, whether secrets slipped into the changes, and how the last run went).
- **Build system**: Added native builds for the takt command-line tool and web UI.
