# Changelog

All notable changes to takt are recorded here. The format is based on [Keep a Changelog](https://keepachangelog.com/), and this file is meant to be readable without diving into the code.

## [Unreleased]

### Added
- **Work can now be spread across several machines.** Until now everything ran on the one computer hosting takt, so the amount of work that could happen at once was limited by that machine — builds, test suites and virtual machines all competing for the same processor and memory. Other computers can now take on that work, whether they sit on the same network or are rented in the cloud.
  - **One machine stays in charge.** It holds the records, the queue of pending work and the master copies of the code. The others ask it for work, do it, and report back.
  - **Helper machines dial out.** They make the connection themselves, so a rented cloud machine needs no special network setup to be reachable — which is usually the hardest part of using one.
  - **A job is only ever done once.** A machine takes out a time-limited claim on a job. If it loses power or its network mid-way, the claim lapses and another machine picks the job up. The original machine is refused if it comes back later, so it cannot overwrite work that has since been completed by someone else.
  - **Finished code is handed back safely.** A helper machine never publishes anything itself: it passes the commits back to the machine in charge, which remains the only thing that talks to GitHub. Who gets to publish has not changed.
  - **Machines describe themselves.** A machine without an AI assistant installed is used for building and testing only, and is never given work it cannot do. A machine running low on disk stops accepting new work rather than failing partway through, and cleans up after each job.
  - **Working from anywhere.** Because the machine in charge can now be reached over the network, you can drive takt from whichever computer you happen to be sitting at. Reaching it from off-machine requires an access token, and takt refuses to start in a state where it would be reachable without one.
- **Context engine — takt now remembers what its agents learn.** Previously, anything an agent worked out during a job (a build flag that turns out to be required, a machine with a quirk, an interface that doesn't behave the way its documentation claims) was lost when that job ended, and the next agent had to work it out again from scratch. Agents can now write those findings down, and search everything that has been written down before starting.
  - **Answers are sections, not whole documents.** Asking "how do I provision a Windows VM" returns the specific part of the specific document that answers it, with its heading and line numbers, rather than a filename to go read. Results are ranked best-first.
  - **Findings are filed against what they apply to** — a particular code repository, workspace or machine, or marked as applying everywhere. Something learned about one repository will not surface as advice while working on an unrelated one.
  - **Every finding says where it came from**: which job recorded it, in which workspace, and when it was last confirmed. A finding that later turns out to be wrong can be replaced, and the old version stops appearing in results but stays readable — so the record of what was once believed, and what corrected it, is preserved.
  - **Questions can be asked in plain language.** The search tries for an exact match on every word first, and widens automatically when a question contains wording the documents don't use.
  - The search index keeps itself current: it refreshes when the service starts and whenever a document is edited through takt. No manual step is needed unless files are edited directly on disk.
- **Issue ledger**: takt now tracks which issues from GitHub/GitLab are assigned to which workspaces. Multiple planning agents can see what's already being worked on, preventing duplicate effort. Issues are claimed atomically — if two agents try to grab the same issue, the second one gets a conflict error. Available through the REST API, MCP tools, and the takt agent shell.
- **Base branch selection**: When creating a workspace, you can now specify which branch to cut from (e.g. `--base release/v11.x`). Previously, workspaces always branched from whatever the root repo happened to have checked out, which could silently pick the wrong starting point.

### Removed
- **All user interfaces are gone.** takt no longer ships a web UI, a desktop app, or a terminal dashboard. In practice takt is driven by AI agents through the assistant integration (MCP) and by scripts through its API, so the interfaces were unused code that still had to be kept working. Removing them cut the project roughly in half (232 files down to 81) and dropped the need for a C++ compiler, CMake, Node.js and Rust just to install takt.
  - The `einheit-ui` web server (previously at `http://takt/`) and its companion port-80 redirect service have been stopped, disabled and deleted.
  - The in-browser shell and code editor are gone, along with the API endpoints that served them.
  - The Textual terminal dashboard is gone. It had already become unreachable — nothing could launch it.
  - **What remains, and is unaffected:** the assistant integration (MCP), the REST/event API on port 7433, the `takt` command-line tool, and the background service that runs pipelines. Everything you can do today, you can still do.

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
- Fixed the takt service freezing on issue ledger writes (assign, status change, unassign). The API handlers were calling SQLite directly on the async event loop, which blocked all other requests until the write finished. Writes now run in a background thread so the service stays responsive.
- Fixed the takt service hanging on startup when the GPG signing key wasn't cached. The startup check was using the wrong GPG key (an old 1024-bit key from 2008) and waiting for a passphrase dialog that would never be answered. It now checks the correct key and fails gracefully if the passphrase isn't cached.
- Fixed the takt service failing to connect to its database during heavy disk activity (e.g. multiple parallel builds). Every database call tried to switch SQLite to WAL mode, which requires a write lock — even for read-only requests. Now it only switches once, and the lock timeout is long enough to ride out I/O storms.
- Fixed two machines being able to claim the same build VM at once. Claiming a machine checked whether it was free and then marked it taken as two separate steps, so two requests arriving together could both pass the check and the second would silently take it over. Claiming is now a single indivisible step. Claims also moved from files into takt's database, which is what lets them work across several computers — a file on one machine is invisible to the others. Existing claims are carried over automatically.
- Fixed run and issue lists coming back in an unpredictable order. Two items created in the same millisecond share a timestamp, and the list was sorted by timestamp alone, so the order of such a pair was left to chance — visible as a "newest first" list occasionally showing the wrong item first. Sorting now falls back to creation order for ties.
- Build leftovers and cache folders are no longer tracked, keeping the project tidy.

## Earlier work

A summary of recent improvements, in plain terms:

- **Terminal and live view**: Replaced the old terminal bridge with a more reliable one, fixed window sizing, and made the live output connection sturdier so it survives reconnects and doesn't drop messages.
- **Web dashboard**: Redesigned the dashboard into a compact billboard view, added an in-browser editor, and fixed assorted display and reconnection glitches.
- **Setup and services**: Added a setup script and background services so takt can run continuously, with services bound to the local machine for safety.
- **Workspace health**: Added a one-glance health check for any workspace (how far behind it is, whether secrets slipped into the changes, and how the last run went).
- **Build system**: Added native builds for the takt command-line tool and web UI.
