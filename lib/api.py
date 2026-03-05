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
    "/api/templates/{name}", handle_get_template,
  )
  app.router.add_put(
    "/api/templates/{name}", handle_put_template,
  )

  # SSE.
  app.router.add_get("/api/events", handle_sse)

  # CORS middleware.
  app.middlewares.append(cors_middleware)

  return app


@web.middleware
async def cors_middleware(request, handler):
  """Add CORS headers for Tauri dev server."""
  resp = await handler(request)
  resp.headers["Access-Control-Allow-Origin"] = "*"
  resp.headers["Access-Control-Allow-Methods"] = (
    "GET, POST, PUT, DELETE, OPTIONS"
  )
  resp.headers["Access-Control-Allow-Headers"] = (
    "Content-Type"
  )
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
  return await _call(request, "create_workspace", {
    "name": body["name"],
    "repos": body["repos"],
  })


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
  from lib.config import TEMPLATES_DIR
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
  return web.json_response(
    {"status": "ok", "data": {"name": name}},
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
