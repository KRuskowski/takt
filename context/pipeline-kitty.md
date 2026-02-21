# Pipeline Kitty Terminal

## Why Kitty

Pipeline agents run Claude CLI, which captures all keyboard
input. Tmux keybindings are unusable from inside an agent.
Kitty's tab shortcuts (`ctrl+shift+left/right`) work above
the application layer.

## Setup

A dedicated Kitty instance listens on a unix socket. The
pipeline watcher (`bin/pipeline_watch.py`) and dashboard
(`tui/widgets/pipeline.py`) use `kitten @` to open tabs in
that instance. Other Kitty windows are unaffected.

Shell alias (in `~/.zshrc`):

```bash
alias kittenpipeline='env -u CLAUDECODE kitty \
  --override allow_remote_control=socket-only \
  --listen-on unix:/tmp/kitty-pipeline'
```

## Usage

```bash
# 1. Launch the pipeline terminal
kittenpipeline

# 2. Start the watcher (in any terminal)
bin/pipeline_watch.py

# Or use the dashboard and press 'w' to enable watching
bin/dashboard.py
```

When a stage receives a push, the watcher:
1. Reads `.pipeline-push` marker files
2. Builds a trigger prompt with commit log
3. Opens a kitty tab titled `ws/role` in the pipeline
   terminal
4. Runs `claude <prompt>` in that tab

## Socket Discovery

`_find_kitty_socket()` in `bin/pipeline_watch.py:44` checks:
1. Exact path: `/tmp/kitty-pipeline` (CLI `--listen-on`)
2. Glob: `/tmp/kitty-pipeline-*` (config `listen_on`
   appends `-{pid}`)

## Tab Navigation

| Shortcut              | Action             |
|-----------------------|--------------------|
| `ctrl+shift+right`   | Next tab           |
| `ctrl+shift+left`    | Previous tab       |
| `ctrl+shift+t`       | New tab            |
| `ctrl+shift+q`       | Close tab          |
| `ctrl+shift+.`       | Move tab right     |
| `ctrl+shift+,`       | Move tab left      |
