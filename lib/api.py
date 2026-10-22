"""REST + SSE API bridge for takt-service.

Thin HTTP layer on top of the existing ZMQ command handlers.
Runs inside the takt-service process on the same event loop.

REST endpoints map 1:1 to existing _handle_* methods.
SSE endpoint subscribes to ZMQ PUB topics and streams events.
"""

import asyncio
import json
import logging

from aiohttp import web

log = logging.getLogger("takt.api")


def build_app(service):
  """Create the aiohttp application.

  Args:
    service: TaktService instance — handlers call its
      _handle_* methods directly (no ZMQ round-trip).

  Returns:
    aiohttp.web.Application.
  """
  app = web.Application()
  app["service"] = service
  app["sse_clients"] = []

  # REST routes.
  app.router.add_get("/api/ping", handle_ping)
  app.router.add_get(
    "/api/workspaces", handle_list_workspaces,
  )
  app.router.add_post(
    "/api/workspaces", handle_create_workspace,
  )
  app.router.add_delete(
    "/api/workspaces/{name}", handle_delete_workspace,
  )
  app.router.add_get(
    "/api/workspaces/{name}/status",
    handle_workspace_status,
  )
  app.router.add_get("/api/targets", handle_list_targets)
  app.router.add_post(
    "/api/targets/{name}/claim", handle_claim_target,
  )
  app.router.add_post(
    "/api/targets/{name}/release", handle_release_target,
  )
  app.router.add_post(
    "/api/targets/{name}/up", handle_target_up,
  )
  app.router.add_post(
    "/api/targets/{name}/down", handle_target_down,
  )
  app.router.add_get("/api/runs", handle_list_runs)
  app.router.add_post("/api/runs", handle_trigger_run)
  app.router.add_post(
    "/api/runs/{id}/cancel", handle_cancel_run,
  )
  app.router.add_get(
    "/api/runs/{id}", handle_get_run,
  )
  app.router.add_get(
    "/api/runs/{id}/steps/{sid}",
    handle_get_step,
  )
  app.router.add_get(
    "/api/runs/{id}/steps/{sid}/output",
    handle_step_output,
  )
  app.router.add_get("/api/agents", handle_list_agents)
  app.router.add_post(
    "/api/agents/{id}/cancel", handle_cancel_agent,
  )
  app.router.add_get(
    "/api/pipeline/{workspace}", handle_get_pipeline,
  )
  app.router.add_put(
    "/api/pipeline/{workspace}", handle_set_pipeline,
  )
  app.router.add_get(
    "/api/meta-agents", handle_list_meta_agents,
  )
  app.router.add_post(
    "/api/meta-agents/{id}/run", handle_run_meta_agent,
  )
  app.router.add_get(
    "/api/meta-agents/{id}/runs",
    handle_list_meta_runs,
  )
  app.router.add_get(
    "/api/meta-agents/{id}/runs/{rid}/output",
    handle_meta_run_output,
  )
  app.router.add_post(
    "/api/meta-agents/{id}/runs/{rid}/cancel",
    handle_cancel_meta_run,
  )
  app.router.add_get("/api/repos", handle_list_repos)
  app.router.add_get(
    "/api/workspaces/{name}/claude-md",
    handle_get_workspace_claude_md,
  )
  app.router.add_put(
    "/api/workspaces/{name}/claude-md",
    handle_put_workspace_claude_md,
  )
  app.router.add_get(
    "/api/templates", handle_list_templates,
  )
  app.router.add_get(
    "/api/templates/{name}", handle_get_template,
  )
  app.router.add_put(
    "/api/templates/{name}", handle_put_template,
  )

  # Context files.
  app.router.add_get(
    "/api/context", handle_list_context,
  )
  # The context engine. These share the /api/context
  # prefix with the {name} file reader below. aiohttp
  # matches static paths ahead of dynamic ones regardless
  # of registration order, so they do not collide — but a
  # context file named exactly 'search', 'stats', 'docs',
  # 'reindex', 'facts' or 'supersede' would be unreachable
  # by name. Indexed files all carry a .md suffix, so this
  # costs nothing in practice.
  app.router.add_get(
    "/api/context/search", handle_context_search,
  )
  app.router.add_get(
    "/api/context/stats", handle_context_stats,
  )
  app.router.add_get(
    "/api/context/docs", handle_context_docs,
  )
  app.router.add_post(
    "/api/context/reindex", handle_context_reindex,
  )
  app.router.add_post(
    "/api/context/facts", handle_record_fact,
  )
  app.router.add_post(
    "/api/context/supersede", handle_context_supersede,
  )
  app.router.add_get(
    "/api/context/{name}", handle_get_context,
  )
  app.router.add_put(
    "/api/context/{name}", handle_put_context,
  )

  # Pipeline roles.
  app.router.add_get(
    "/api/roles", handle_list_roles,
  )

  # Search.
  app.router.add_get(
    "/api/search", handle_search,
  )

  # Agent usage + accounts.
  app.router.add_get(
    "/api/agent/usage", handle_agent_usage,
  )
  app.router.add_get(
    "/api/agent/accounts", handle_agent_accounts,
  )
  app.router.add_post(
    "/api/agent/accounts/active",
    handle_set_active_account,
  )

  # Issues.
  app.router.add_get("/api/issues", handle_list_issues)
  app.router.add_post(
    "/api/issues/assign", handle_assign_issue,
  )
  app.router.add_post(
    "/api/issues/unassign", handle_unassign_issue,
  )
  app.router.add_post(
    "/api/issues/status", handle_update_issue_status,
  )

  # SSE.
  app.router.add_get("/api/events", handle_sse)

  # CORS middleware.
  app.middlewares.append(cors_middleware)

  return app


def _add_cors(resp):
  resp.headers["Access-Control-Allow-Origin"] = "*"
  resp.headers["Access-Control-Allow-Methods"] = (
    "GET, POST, PUT, DELETE, OPTIONS"
  )
  resp.headers["Access-Control-Allow-Headers"] = (
    "Content-Type"
  )


@web.middleware
async def cors_middleware(request, handler):
  """Add CORS headers for Tauri dev server."""
  if request.method == "OPTIONS":
    resp = web.Response(status=204)
    _add_cors(resp)
    return resp
  try:
    resp = await handler(request)
  except web.HTTPException as exc:
    _add_cors(exc)
    raise
  # web.Response (regular) gets headers; raw
  # StreamResponse (SSE) sets its own in handler.
  if isinstance(resp, web.Response):
    _add_cors(resp)
  return resp


async def _call(request, cmd, payload=None):
  """Call a service handler and return JSON response.

  Args:
    request: aiohttp request.
    cmd: ZMQ command name (maps to _handle_* method).
    payload: Dict payload for the handler.

  Returns:
    aiohttp.web.Response with JSON body.
  """
  svc = request.app["service"]
  handler = svc._cmd_handlers.get(cmd)
  if handler is None:
    return web.json_response(
      {"error": f"unknown command: {cmd}"}, status=500,
    )
  p = payload or {}
  p["cmd"] = cmd
  try:
    data = await handler(svc, p)
    return web.json_response({"status": "ok", "data": data})
  except (ValueError, KeyError) as e:
    return web.json_response(
      {"status": "error", "message": str(e)}, status=400,
    )
  except Exception as e:
    log.error("API handler %s failed: %s", cmd, e,
              exc_info=True)
    return web.json_response(
      {"status": "error", "message": str(e)}, status=500,
    )


# -- REST handlers --

async def handle_ping(request):
  return await _call(request, "ping")


async def handle_list_workspaces(request):
  """GET /api/workspaces — list workspaces."""
  from lib.workspace_ops import list_workspaces
  loop = asyncio.get_running_loop()
  ws = await loop.run_in_executor(None, list_workspaces)
  return web.json_response(
    {"status": "ok", "data": {"workspaces": ws}},
  )


async def handle_create_workspace(request):
  """POST /api/workspaces — create workspace."""
  body = await request.json()
  payload = {
    "name": body["name"],
    "repos": body["repos"],
  }
  if body.get("base_branch"):
    payload["base_branch"] = body["base_branch"]
  return await _call(
    request, "create_workspace", payload,
  )


async def handle_delete_workspace(request):
  """DELETE /api/workspaces/:name."""
  name = request.match_info["name"]
  return await _call(
    request, "delete_workspace", {"name": name},
  )


async def handle_workspace_status(request):
  """GET /api/workspaces/:name/status."""
  from lib.workspace_ops import get_workspace_status
  name = request.match_info["name"]
  loop = asyncio.get_running_loop()
  try:
    status = await loop.run_in_executor(
      None, lambda: get_workspace_status(name),
    )
    return web.json_response(
      {"status": "ok", "data": {"repos": status}},
    )
  except FileNotFoundError as e:
    return web.json_response(
      {"status": "error", "message": str(e)}, status=404,
    )


async def handle_list_targets(request):
  """GET /api/targets."""
  from lib.target_ops import get_all_targets
  loop = asyncio.get_running_loop()
  targets = await loop.run_in_executor(
    None, get_all_targets,
  )
  return web.json_response(
    {"status": "ok", "data": {"targets": targets}},
  )


async def handle_claim_target(request):
  """POST /api/targets/:name/claim."""
  name = request.match_info["name"]
  body = await request.json()
  from lib.commands import target_claim
  loop = asyncio.get_running_loop()
  result = await loop.run_in_executor(
    None,
    lambda: target_claim(name, body["workspace"]),
  )
  if not result.ok:
    return web.json_response(
      {"status": "error", "message": result.output},
      status=400,
    )
  return web.json_response(
    {"status": "ok", "data": {"name": name}},
  )


async def handle_release_target(request):
  """POST /api/targets/:name/release."""
  name = request.match_info["name"]
  from lib.commands import target_release
  loop = asyncio.get_running_loop()
  result = await loop.run_in_executor(
    None, lambda: target_release(name),
  )
  return web.json_response(
    {"status": "ok", "data": {"name": name}},
  )


async def handle_target_up(request):
  """POST /api/targets/:name/up."""
  name = request.match_info["name"]
  from lib.commands import target_up
  loop = asyncio.get_running_loop()
  result = await loop.run_in_executor(
    None, lambda: target_up(name),
  )
  status = 200 if result.ok else 400
  return web.json_response(
    {"status": "ok" if result.ok else "error",
     "message": result.output},
    status=status,
  )


async def handle_target_down(request):
  """POST /api/targets/:name/down."""
  name = request.match_info["name"]
  from lib.commands import target_down
  loop = asyncio.get_running_loop()
  result = await loop.run_in_executor(
    None, lambda: target_down(name),
  )
  status = 200 if result.ok else 400
  return web.json_response(
    {"status": "ok" if result.ok else "error",
     "message": result.output},
    status=status,
  )


async def handle_list_runs(request):
  """GET /api/runs?workspace=X&limit=N."""
  workspace = request.query.get("workspace")
  limit = int(request.query.get("limit", "20"))
  return await _call(request, "list_runs", {
    "workspace": workspace,
    "limit": limit,
  })


async def handle_trigger_run(request):
  """POST /api/runs — trigger a pipeline run."""
  body = await request.json()
  return await _call(request, "trigger_run", {
    "workspace": body["workspace"],
  })


async def handle_cancel_run(request):
  """POST /api/runs/:id/cancel."""
  run_id = int(request.match_info["id"])
  return await _call(request, "cancel_run", {
    "run_id": run_id,
  })


async def handle_get_run(request):
  """GET /api/runs/:id."""
  run_id = int(request.match_info["id"])
  return await _call(request, "get_run_detail", {
    "run_id": run_id,
  })


async def handle_get_step(request):
  """GET /api/runs/:id/steps/:sid."""
  step_id = int(request.match_info["sid"])
  return await _call(request, "get_step_detail", {
    "step_id": step_id,
  })


async def handle_step_output(request):
  """GET /api/runs/:id/steps/:sid/output?from=0."""
  step_id = int(request.match_info["sid"])
  from_line = int(request.query.get("from", "0"))
  return await _call(request, "replay_output", {
    "step_id": step_id,
    "from_line": from_line,
  })


async def handle_list_agents(request):
  """GET /api/agents."""
  return await _call(request, "list_agents", {})


async def handle_cancel_agent(request):
  """POST /api/agents/:id/cancel."""
  agent_id = request.match_info["id"]
  return await _call(request, "cancel_agent", {
    "agent_id": agent_id,
  })


async def handle_get_pipeline(request):
  """GET /api/pipeline/:workspace."""
  workspace = request.match_info["workspace"]
  from lib import db
  pipeline = db.get_pipeline(workspace)
  return web.json_response(
    {"status": "ok", "data": {"steps": pipeline}},
  )


async def handle_set_pipeline(request):
  """PUT /api/pipeline/:workspace."""
  workspace = request.match_info["workspace"]
  body = await request.json()
  from lib import db
  db.define_pipeline(workspace, body["steps"])
  return web.json_response(
    {"status": "ok", "data": {"workspace": workspace}},
  )


async def handle_list_meta_agents(request):
  """GET /api/meta-agents."""
  return await _call(request, "list_meta_agents", {})


async def handle_run_meta_agent(request):
  """POST /api/meta-agents/:id/run."""
  agent_id = int(request.match_info["id"])
  return await _call(request, "run_meta_agent", {
    "meta_agent_id": agent_id,
  })


async def handle_list_meta_runs(request):
  """GET /api/meta-agents/:id/runs."""
  agent_id = int(request.match_info["id"])
  return await _call(request, "list_meta_runs", {
    "meta_agent_id": agent_id,
  })


async def handle_meta_run_output(request):
  """GET /api/meta-agents/:id/runs/:rid/output?from=0."""
  run_id = int(request.match_info["rid"])
  from_line = int(request.query.get("from", "0"))
  return await _call(request, "replay_meta_output", {
    "run_id": run_id,
    "from_line": from_line,
  })


async def handle_cancel_meta_run(request):
  """POST /api/meta-agents/:id/runs/:rid/cancel."""
  run_id = int(request.match_info["rid"])
  return await _call(request, "cancel_meta_run", {
    "run_id": run_id,
  })


async def handle_get_workspace_claude_md(request):
  """GET /api/workspaces/:name/claude-md."""
  from lib.config import WORKSPACES_DIR
  name = request.match_info["name"]
  path = WORKSPACES_DIR / name / "CLAUDE.md"
  if not path.exists():
    return web.json_response(
      {"status": "error", "message": "not found"},
      status=404,
    )
  content = path.read_text()
  return web.json_response(
    {"status": "ok", "data": {"content": content}},
  )


async def handle_put_workspace_claude_md(request):
  """PUT /api/workspaces/:name/claude-md."""
  from lib.config import WORKSPACES_DIR
  name = request.match_info["name"]
  path = WORKSPACES_DIR / name / "CLAUDE.md"
  if not path.parent.exists():
    return web.json_response(
      {"status": "error", "message": "workspace not found"},
      status=404,
    )
  body = await request.json()
  content = body.get("content", "")
  path.write_text(content)
  return web.json_response(
    {"status": "ok", "data": {"name": name}},
  )


async def handle_list_templates(request):
  """GET /api/templates — list template files."""
  from lib.config import TEMPLATES_DIR
  files = sorted(
    f.name for f in TEMPLATES_DIR.glob("*.md")
    if f.is_file()
  )
  return web.json_response(
    {"status": "ok", "data": {"templates": files}},
  )


async def handle_get_template(request):
  """GET /api/templates/:name — read a template file."""
  from lib.config import TEMPLATES_DIR
  name = request.match_info["name"]
  if "/" in name or "\\" in name:
    return web.json_response(
      {"status": "error", "message": "invalid name"},
      status=400,
    )
  path = TEMPLATES_DIR / name
  if not path.exists():
    return web.json_response(
      {"status": "error", "message": "not found"},
      status=404,
    )
  content = path.read_text()
  return web.json_response(
    {"status": "ok", "data": {"content": content}},
  )


async def handle_put_template(request):
  """PUT /api/templates/:name — write a template file."""
  from lib import context_store
  from lib.config import TEMPLATES_DIR
  loop = asyncio.get_running_loop()
  name = request.match_info["name"]
  if "/" in name or "\\" in name:
    return web.json_response(
      {"status": "error", "message": "invalid name"},
      status=400,
    )
  body = await request.json()
  content = body.get("content", "")
  path = TEMPLATES_DIR / name
  path.write_text(content)
  # Keep the search index in step with the file.
  indexed = None
  if name.lower().endswith(".md"):
    indexed = await loop.run_in_executor(
      None,
      lambda: context_store.upsert_doc(
        f"templates/{name}", content, source="file",
      ),
    )
  return web.json_response(
    {"status": "ok",
     "data": {"name": name, "indexed": indexed}},
  )


async def handle_list_repos(request):
  """GET /api/repos — list configured repos."""
  from lib.config import load_repos_config
  loop = asyncio.get_running_loop()
  config = await loop.run_in_executor(
    None, load_repos_config,
  )
  repos = config.get("repos", {})
  result = [
    {
      "name": name,
      "push_order": cfg.get("push_order", 99),
    }
    for name, cfg in sorted(repos.items())
  ]
  return web.json_response(
    {"status": "ok", "data": {"repos": result}},
  )


# -- Agent usage + accounts --

async def handle_agent_usage(request):
  """GET /api/agent/usage — token usage from stats-cache."""
  import json as _json
  from pathlib import Path
  from lib.config import load_takt_config
  cfg = load_takt_config()
  agent_cfg = cfg.get("agent", {})
  accounts = agent_cfg.get("accounts", {})
  active = agent_cfg.get("active_account", "default")
  result = []
  for name, acfg in accounts.items():
    claude_home = acfg.get("claude_home", "~/.claude")
    stats_path = (
      Path(claude_home).expanduser() / "stats-cache.json"
    )
    entry = {
      "account": name,
      "label": acfg.get("label", name),
      "active": name == active,
      "tier": None,
      "subscription": None,
      "models": {},
      "total_sessions": 0,
      "total_messages": 0,
      "last_computed": None,
    }
    creds_path = (
      Path(claude_home).expanduser() / ".credentials.json"
    )
    if creds_path.exists():
      try:
        creds = _json.loads(creds_path.read_text())
        oauth = creds.get("claudeAiOauth", {})
        entry["tier"] = oauth.get("rateLimitTier")
        entry["subscription"] = oauth.get(
          "subscriptionType",
        )
      except Exception:
        pass
    if stats_path.exists():
      try:
        data = _json.loads(stats_path.read_text())
        entry["total_sessions"] = data.get(
          "totalSessions", 0,
        )
        entry["total_messages"] = data.get(
          "totalMessages", 0,
        )
        entry["last_computed"] = data.get(
          "lastComputedDate",
        )
        for model, usage in data.get(
          "modelUsage", {},
        ).items():
          short = model.replace("claude-", "")
          for suffix in [
            "-20250929", "-20251101", "-20251001",
          ]:
            short = short.replace(suffix, "")
          entry["models"][short] = {
            "input": usage.get("inputTokens", 0),
            "output": usage.get("outputTokens", 0),
            "cache_read": usage.get(
              "cacheReadInputTokens", 0,
            ),
            "cache_write": usage.get(
              "cacheCreationInputTokens", 0,
            ),
          }
      except Exception:
        pass
    result.append(entry)
  return web.json_response({
    "status": "ok",
    "data": {"accounts": result},
  })


async def handle_agent_accounts(request):
  """GET /api/agent/accounts."""
  from lib.config import load_takt_config
  cfg = load_takt_config()
  agent_cfg = cfg.get("agent", {})
  accounts = agent_cfg.get("accounts", {})
  active = agent_cfg.get("active_account", "default")
  result = []
  for name, acfg in accounts.items():
    result.append({
      "name": name,
      "label": acfg.get("label", name),
      "claude_home": acfg.get("claude_home", ""),
      "active": name == active,
    })
  return web.json_response({
    "status": "ok",
    "data": {"accounts": result, "active": active},
  })


async def handle_set_active_account(request):
  """POST /api/agent/accounts/active."""
  import yaml
  from lib.config import CONFIG_DIR
  body = await request.json()
  account = body.get("account")
  if not account:
    return web.json_response(
      {"status": "error", "message": "missing account"},
      status=400,
    )
  path = CONFIG_DIR / "takt.yaml"
  with open(path) as f:
    cfg = yaml.safe_load(f) or {}
  agent_cfg = cfg.setdefault("agent", {})
  accounts = agent_cfg.get("accounts", {})
  if account not in accounts:
    return web.json_response(
      {"status": "error",
       "message": f"unknown account: {account}"},
      status=400,
    )
  agent_cfg["active_account"] = account
  with open(path, "w") as f:
    yaml.dump(cfg, f, default_flow_style=False,
              sort_keys=False)
  return web.json_response({
    "status": "ok",
    "data": {"active": account},
  })


# -- Context engine --

async def handle_context_search(request):
  """GET /api/context/search — search the context index.

  Query params: q (required), scope_kind, scope_value,
  kind ('doc'/'fact'), limit, include_superseded.
  """
  from lib import context_store
  loop = asyncio.get_running_loop()
  query = request.query.get("q", "")
  if not query.strip():
    resp = web.json_response(
      {"status": "error", "message": "q is required"},
      status=400,
    )
    _add_cors(resp)
    return resp
  try:
    limit = int(request.query.get("limit", 10))
  except ValueError:
    limit = 10
  kwargs = {
    "scope_kind": request.query.get("scope_kind"),
    "scope_value": request.query.get("scope_value", ""),
    "kind": request.query.get("kind"),
    "limit": max(1, min(limit, 100)),
    "include_superseded": request.query.get(
      "include_superseded"
    ) in ("1", "true", "yes"),
  }
  try:
    results = await loop.run_in_executor(
      None,
      lambda: context_store.search(query, **kwargs),
    )
  except ValueError as e:
    resp = web.json_response(
      {"status": "error", "message": str(e)}, status=400,
    )
    _add_cors(resp)
    return resp
  resp = web.json_response({
    "status": "ok",
    "data": {"query": query, "results": results},
  })
  _add_cors(resp)
  return resp


async def handle_context_stats(request):
  """GET /api/context/stats — index size summary."""
  from lib import context_store
  loop = asyncio.get_running_loop()
  data = await loop.run_in_executor(
    None, context_store.stats,
  )
  resp = web.json_response({"status": "ok", "data": data})
  _add_cors(resp)
  return resp


async def handle_context_docs(request):
  """GET /api/context/docs — list indexed documents.

  Query params: kind, scope_kind, scope_value, limit,
  include_superseded.
  """
  from lib import context_store
  loop = asyncio.get_running_loop()
  try:
    limit = int(request.query.get("limit", 200))
  except ValueError:
    limit = 200
  kwargs = {
    "kind": request.query.get("kind"),
    "scope_kind": request.query.get("scope_kind"),
    "scope_value": request.query.get("scope_value", ""),
    "limit": max(1, min(limit, 1000)),
    "include_superseded": request.query.get(
      "include_superseded"
    ) in ("1", "true", "yes"),
  }
  docs = await loop.run_in_executor(
    None, lambda: context_store.list_docs(**kwargs),
  )
  resp = web.json_response({
    "status": "ok", "data": {"docs": docs},
  })
  _add_cors(resp)
  return resp


async def handle_context_reindex(request):
  """POST /api/context/reindex — resync from disk."""
  from lib import context_store
  loop = asyncio.get_running_loop()
  stats = await loop.run_in_executor(
    None, context_store.reindex,
  )
  resp = web.json_response({"status": "ok", "data": stats})
  _add_cors(resp)
  return resp


async def handle_record_fact(request):
  """POST /api/context/facts — record a fact.

  Body: body (required), scope_kind, scope_value, title,
  source_run, source_workspace, path, verified.
  """
  from lib import context_store
  loop = asyncio.get_running_loop()
  try:
    payload = await request.json()
  except Exception:
    payload = {}
  try:
    result = await loop.run_in_executor(
      None,
      lambda: context_store.record_fact(
        payload.get("body", ""),
        scope_kind=payload.get("scope_kind", "global"),
        scope_value=payload.get("scope_value", ""),
        title=payload.get("title"),
        source=payload.get("source", "agent"),
        source_run=payload.get("source_run"),
        source_workspace=payload.get("source_workspace"),
        path=payload.get("path"),
        verified=payload.get("verified", True),
      ),
    )
  except ValueError as e:
    resp = web.json_response(
      {"status": "error", "message": str(e)}, status=400,
    )
    _add_cors(resp)
    return resp
  resp = web.json_response({"status": "ok", "data": result})
  _add_cors(resp)
  return resp


async def handle_context_supersede(request):
  """POST /api/context/supersede — retire a doc.

  Body: path, by_path.
  """
  from lib import context_store
  loop = asyncio.get_running_loop()
  try:
    payload = await request.json()
  except Exception:
    payload = {}
  path = payload.get("path", "")
  by_path = payload.get("by_path", "")
  if not path or not by_path:
    resp = web.json_response(
      {"status": "error",
       "message": "path and by_path are required"},
      status=400,
    )
    _add_cors(resp)
    return resp
  try:
    ok = await loop.run_in_executor(
      None,
      lambda: context_store.supersede(path, by_path),
    )
  except ValueError as e:
    resp = web.json_response(
      {"status": "error", "message": str(e)}, status=400,
    )
    _add_cors(resp)
    return resp
  if not ok:
    resp = web.json_response(
      {"status": "error", "message": "doc not found"},
      status=404,
    )
    _add_cors(resp)
    return resp
  resp = web.json_response({
    "status": "ok",
    "data": {"path": path, "superseded_by": by_path},
  })
  _add_cors(resp)
  return resp


# -- Context files --

async def handle_list_context(request):
  """GET /api/context — list context directory files."""
  from lib.config import CONTEXT_DIR
  if not CONTEXT_DIR.exists():
    return web.json_response(
      {"status": "ok", "data": {"files": []}},
    )
  files = sorted(
    f.name for f in CONTEXT_DIR.iterdir()
    if f.is_file()
  )
  return web.json_response(
    {"status": "ok", "data": {"files": files}},
  )


async def handle_get_context(request):
  """GET /api/context/:name — read a context file."""
  from lib.config import CONTEXT_DIR
  name = request.match_info["name"]
  if "/" in name or "\\" in name:
    return web.json_response(
      {"status": "error", "message": "invalid name"},
      status=400,
    )
  path = CONTEXT_DIR / name
  if not path.exists():
    return web.json_response(
      {"status": "error", "message": "not found"},
      status=404,
    )
  content = path.read_text()
  return web.json_response(
    {"status": "ok", "data": {"content": content}},
  )


async def handle_put_context(request):
  """PUT /api/context/:name — write a context file."""
  from lib import context_store
  from lib.config import CONTEXT_DIR
  loop = asyncio.get_running_loop()
  name = request.match_info["name"]
  if "/" in name or "\\" in name:
    return web.json_response(
      {"status": "error", "message": "invalid name"},
      status=400,
    )
  body = await request.json()
  content = body.get("content", "")
  path = CONTEXT_DIR / name
  path.write_text(content)
  # Keep the search index in step with the file, so an edit
  # is retrievable immediately instead of at next reindex.
  indexed = None
  if name.lower().endswith(".md"):
    indexed = await loop.run_in_executor(
      None,
      lambda: context_store.upsert_doc(
        f"context/{name}", content, source="file",
      ),
    )
  return web.json_response(
    {"status": "ok",
     "data": {"name": name, "indexed": indexed}},
  )


# -- Pipeline roles --

async def handle_list_roles(request):
  """GET /api/roles — list pipeline roles with text."""
  from lib.config import parse_pipeline_roles_full
  loop = asyncio.get_running_loop()
  roles = await loop.run_in_executor(
    None, parse_pipeline_roles_full,
  )
  return web.json_response(
    {"status": "ok", "data": {"roles": roles}},
  )


# -- Search --

async def handle_search(request):
  """GET /api/search?q=term — search context + templates."""
  from lib.config import CONTEXT_DIR, TEMPLATES_DIR
  query = request.query.get("q", "").lower().strip()
  if not query:
    return web.json_response(
      {"status": "ok", "data": {"results": []}},
    )
  results = []
  dirs = [
    ("context", CONTEXT_DIR),
    ("templates", TEMPLATES_DIR),
  ]
  for section, d in dirs:
    if not d.exists():
      continue
    for f in sorted(d.iterdir()):
      if not f.is_file():
        continue
      try:
        text = f.read_text()
      except Exception:
        continue
      if query in f.name.lower():
        results.append({
          "section": section,
          "file": f.name,
          "match": "filename",
          "lines": [],
        })
        continue
      matches = []
      for i, line in enumerate(text.splitlines()):
        if query in line.lower():
          matches.append({
            "line": i + 1,
            "text": line.strip()[:120],
          })
      if matches:
        results.append({
          "section": section,
          "file": f.name,
          "match": "content",
          "lines": matches[:5],
        })
  return web.json_response(
    {"status": "ok", "data": {"results": results}},
  )


# -- SSE handler --

async def handle_sse(request):
  """GET /api/events?topics=topic1,topic2,...

  Streams Server-Sent Events from ZMQ PUB topics.
  """
  topics_param = request.query.get("topics", "")
  topics = [
    t.strip() for t in topics_param.split(",") if t.strip()
  ]

  resp = web.StreamResponse()
  resp.content_type = "text/event-stream"
  resp.headers["Cache-Control"] = "no-cache"
  resp.headers["Connection"] = "keep-alive"
  resp.headers["Access-Control-Allow-Origin"] = "*"
  await resp.prepare(request)

  queue = asyncio.Queue(maxsize=256)
  client = {"queue": queue, "topics": topics}
  request.app["sse_clients"].append(client)

  try:
    while True:
      event_topic, data = await queue.get()
      payload = (
        f"event: {event_topic}\n"
        f"data: {json.dumps(data)}\n\n"
      )
      await resp.write(payload.encode())
  except (asyncio.CancelledError, ConnectionResetError):
    pass
  finally:
    request.app["sse_clients"].remove(client)

  return resp


async def handle_list_issues(request):
  """GET /api/issues — list issues with optional filters."""
  from lib import db
  loop = asyncio.get_running_loop()
  status = request.query.get("status")
  workspace = request.query.get("workspace")
  tracker = request.query.get("tracker")
  issues = await loop.run_in_executor(
    None,
    lambda: db.list_issues(
      status=status, workspace=workspace,
      tracker=tracker,
    ),
  )
  resp = web.json_response(
    {"status": "ok", "data": issues},
  )
  _add_cors(resp)
  return resp


async def handle_assign_issue(request):
  """POST /api/issues/assign — assign issue to workspace."""
  from lib import db
  loop = asyncio.get_running_loop()
  body = await request.json()
  try:
    result = await loop.run_in_executor(
      None,
      lambda: db.assign_issue(
        body["tracker"], body["issue_id"],
        body["workspace"],
        summary=body.get("summary", ""),
      ),
    )
    resp = web.json_response(
      {"status": "ok", "data": result},
    )
  except ValueError as e:
    resp = web.json_response(
      {"status": "error", "message": str(e)},
      status=409,
    )
  _add_cors(resp)
  return resp


async def handle_unassign_issue(request):
  """POST /api/issues/unassign — remove assignment."""
  from lib import db
  loop = asyncio.get_running_loop()
  body = await request.json()
  result = await loop.run_in_executor(
    None,
    lambda: db.unassign_issue(
      body["tracker"], body["issue_id"],
    ),
  )
  if result:
    resp = web.json_response(
      {"status": "ok", "data": result},
    )
  else:
    resp = web.json_response(
      {"status": "error", "message": "Issue not found"},
      status=404,
    )
  _add_cors(resp)
  return resp


async def handle_update_issue_status(request):
  """POST /api/issues/status — transition issue status."""
  from lib import db
  loop = asyncio.get_running_loop()
  body = await request.json()
  try:
    result = await loop.run_in_executor(
      None,
      lambda: db.update_issue_status(
        body["tracker"], body["issue_id"],
        body["status"],
      ),
    )
    resp = web.json_response(
      {"status": "ok", "data": result},
    )
  except KeyError as e:
    resp = web.json_response(
      {"status": "error", "message": str(e)},
      status=404,
    )
  except ValueError as e:
    resp = web.json_response(
      {"status": "error", "message": str(e)},
      status=400,
    )
  _add_cors(resp)
  return resp


def broadcast_to_sse(app, topic, data):
  """Push an event to all connected SSE clients.

  Called by the service when it publishes on PUB socket.

  Args:
    app: aiohttp application.
    topic: Event topic string.
    data: Event data dict.
  """
  clients = app.get("sse_clients", [])
  for client in clients:
    # Match if client has no filter or topic matches.
    client_topics = client["topics"]
    if not client_topics or any(
      topic.startswith(t) for t in client_topics
    ):
      try:
        client["queue"].put_nowait((topic, data))
      except asyncio.QueueFull:
        pass
