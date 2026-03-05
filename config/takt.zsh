# takt CLI integration for zsh.
# Source from ~/.zshrc:
#   source /home/karl/dev/takt/config/takt.zsh

takt() {
  /home/karl/dev/takt/bin/takt.py "$@"
}

_takt() {
  local curcontext="$curcontext" state
  local -a groups ws_cmds target_cmds pipeline_cmds
  local -a service_cmds

  groups=(
    'ws:Workspace management'
    'target:Target management'
    'pipeline:Pipeline management'
    'push:Push branches to GitHub'
    'service:takt-service management'
  )

  ws_cmds=(
    'list:List all workspaces'
    'create:Create a new workspace'
    'delete:Delete a workspace'
    'status:Show repo status'
  )

  target_cmds=(
    'list:List all targets'
    'claim:Claim a target'
    'release:Release a target'
    'up:Start a VM'
    'down:Stop a VM'
    'run:Run command on target'
    'status:Show target details'
  )

  pipeline_cmds=(
    'set:Define pipeline steps'
    'show:Show pipeline config'
    'runs:Show run history'
  )

  service_cmds=(
    'start:Start service'
    'stop:Stop service'
    'restart:Restart service'
    'status:Show service status'
  )

  _arguments -C \
    '1:group:->group' \
    '*::arg:->args'

  case $state in
    group)
      _describe 'command group' groups
      ;;
    args)
      case $words[1] in
        ws)
          if (( CURRENT == 2 )); then
            _describe 'ws command' ws_cmds
          else
            case $words[2] in
              delete|status)
                if (( CURRENT == 3 )); then
                  _takt_workspaces
                fi
                ;;
              create)
                if (( CURRENT == 3 )); then
                  _message 'workspace name'
                else
                  _takt_repos
                fi
                ;;
            esac
          fi
          ;;
        target)
          if (( CURRENT == 2 )); then
            _describe 'target command' target_cmds
          else
            case $words[2] in
              claim)
                if (( CURRENT == 3 )); then
                  _takt_targets
                elif (( CURRENT == 4 )); then
                  _takt_workspaces
                fi
                ;;
              release|up|down|run|status)
                if (( CURRENT == 3 )); then
                  _takt_targets
                fi
                ;;
            esac
          fi
          ;;
        pipeline)
          if (( CURRENT == 2 )); then
            _describe 'pipeline command' pipeline_cmds
          else
            case $words[2] in
              set|show|runs)
                if (( CURRENT == 3 )); then
                  _takt_workspaces
                fi
                ;;
            esac
          fi
          ;;
        push)
          if (( CURRENT == 2 )); then
            _takt_workspaces
          fi
          ;;
        service)
          if (( CURRENT == 2 )); then
            _describe 'service command' service_cmds
          fi
          ;;
      esac
      ;;
  esac
}

# Dynamic completions using --names-only.
_takt_workspaces() {
  local -a names
  names=(${(f)"$(takt ws list --names-only 2>/dev/null)"})
  _describe 'workspace' names
}

_takt_targets() {
  local -a names
  names=(${(f)"$(takt target list --names-only 2>/dev/null)"})
  _describe 'target' names
}

_takt_repos() {
  local -a names
  local repos_yaml="/home/karl/dev/takt/config/repos.yaml"
  if [[ -f "$repos_yaml" ]]; then
    names=($(grep -oP '^\s+\K\S+(?=:)' "$repos_yaml" \
      | grep -v '^repos$'))
    _describe 'repo' names
  fi
}

compdef _takt takt
