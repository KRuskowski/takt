"""REST API for the takt web UI.

Exposes workspace, target, pipeline, run, and config data
over HTTP so the C++ UI adapter can proxy it via cpp-httplib.
SSE endpoint for live updates via takt-service PUB socket.

Run standalone:
  python3 -m lib.api [--port 7433] [--bind 127.0.0.1]

Or import and call create_app() to embed in another process.
"""

import asyncio
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

from aiohttp import web

from lib import db
from lib.config import (
  TEMPLATES_DIR,
  WORKSPACES_DIR,
  load_repos_config,
  load_takt_config,
  load_targets_config,
)
from lib.ssh_utils import SSHError, run_ssh
from lib.target_ops import (
  get_all_targets,
  get_target,
  get_vm_state,
  is_template,
  read_lock,
  release_lock,
  write_lock,
)
from lib.workspace_ops import (
  create_workspace,
  delete_workspace,
  get_workspace_status,
  list_workspaces,
)

log = logging.getLogger("takt.api")


def _json(data, status=200):
  """Return a JSON response."""
  return web.json_response(data, status=status)


def _error(message, status=400):
  """Return a JSON error response."""
  return web.json_response(
    {"error": message}, status=status,
  )


# -- Workspace routes --

async def handle_workspaces(request):
  """GET /api/workspaces — list all workspaces."""
  workspaces = list_workspaces()
  return _json(workspaces)


async def handle_workspace_status(request):
  """GET /api/workspaces/{name}/status."""
  name = request.match_info["name"]
  try:
    status = get_workspace_status(name)
    return _json(status)
  except FileNotFoundError:
    return _error(f"Workspace '{name}' not found.", 404)


async def handle_workspace_create(request):
  """POST /api/workspaces — create a workspace."""
  body = await request.json()
  name = body.get("name")
  repos = body.get("repos", [])
  if not name:
    return _error("Missing 'name'.")
  try:
    path = create_workspace(name, repos)
    return _json({
      "status": "created", "name": name,
      "path": str(path),
    }, status=201)
  except FileExistsError:
    return _error(
      f"Workspace '{name}' already exists.", 409,
    )
  except (ValueError, Exception) as e:
    return _error(str(e))


async def handle_workspace_delete(request):
  """DELETE /api/workspaces/{name}."""
  name = request.match_info["name"]
  try:
    delete_workspace(name)
    return _json({"status": "deleted", "name": name})
  except FileNotFoundError:
    return _error(f"Workspace '{name}' not found.", 404)


# -- Target routes --

async def handle_targets(request):
  """GET /api/targets — list all targets."""
  try:
    targets = get_all_targets()
    return _json(targets)
  except FileNotFoundError:
    return _error("targets.yaml not found.", 500)


async def handle_target_status(request):
  """GET /api/targets/{name}."""
  name = request.match_info["name"]
  try:
    target = get_target(name)
  except FileNotFoundError:
    return _error("targets.yaml not found.", 500)
  if target is None:
    return _error(f"Target '{name}' not found.", 404)
  lock = read_lock(name)
  info = {
    "name": name,
    "type": target.get("type"),
    "host": target.get("host"),
    "user": target.get("user"),
    "template": target.get("template", False),
    "description": target.get("description", ""),
    "lock": lock,
  }
  if target.get("type") == "vm":
    info["vm_state"] = get_vm_state(name)
  return _json(info)


async def handle_target_claim(request):
  """POST /api/targets/{name}/claim."""
  name = request.match_info["name"]
  body = await request.json()
  workspace = body.get("workspace")
  if not workspace:
    return _error("Missing 'workspace'.")
  try:
    target = get_target(name)
  except FileNotFoundError:
    return _error("targets.yaml not found.", 500)
  if target is None:
    return _error(f"Target '{name}' not found.", 404)
  if is_template(name):
    return _error(f"'{name}' is a template.")
  existing = read_lock(name)
  if existing:
    return _error(
      f"Already claimed by '{existing['workspace']}'.",
      409,
    )
  write_lock(name, workspace)
  return _json({
    "status": "claimed", "target": name,
    "workspace": workspace,
  })


async def handle_target_release(request):
  """POST /api/targets/{name}/release."""
  name = request.match_info["name"]
  prev = release_lock(name)
  if prev is None:
    return _error(f"Target '{name}' is not claimed.", 404)
  return _json({
    "status": "released", "target": name,
    "was_workspace": prev.get("workspace"),
  })


async def handle_target_up(request):
  """POST /api/targets/{name}/up."""
  name = request.match_info["name"]
  try:
    target = get_target(name)
  except FileNotFoundError:
    return _error("targets.yaml not found.", 500)
  if target is None:
    return _error(f"Target '{name}' not found.", 404)
  if is_template(name):
    return _error(f"'{name}' is a template.")
  if target.get("type") == "hardware":
    return _json({
      "status": "ok",
      "message": "Hardware — always on.",
    })
  if not shutil.which("virsh"):
    return _error("virsh not installed.", 500)
  result = await asyncio.to_thread(
    subprocess.run,
    ["virsh", "start", name],
    capture_output=True, text=True,
  )
  if result.returncode == 0:
    return _json({"status": "started", "target": name})
  return _error(
    f"Failed: {result.stderr.strip()}", 500,
  )


async def handle_target_down(request):
  """POST /api/targets/{name}/down."""
  name = request.match_info["name"]
  try:
    target = get_target(name)
  except FileNotFoundError:
    return _error("targets.yaml not found.", 500)
  if target is None:
    return _error(f"Target '{name}' not found.", 404)
  if target.get("type") == "hardware":
    return _error("Hardware — cannot shut down.")
  if not shutil.which("virsh"):
    return _error("virsh not installed.", 500)
  result = await asyncio.to_thread(
    subprocess.run,
    ["virsh", "shutdown", name],
    capture_output=True, text=True,
  )
  if result.returncode == 0:
    return _json({
      "status": "shutting_down", "target": name,
    })
  return _error(
    f"Failed: {result.stderr.strip()}", 500,
  )


async def handle_target_run(request):
  """POST /api/targets/{name}/run."""
  name = request.match_info["name"]
  body = await request.json()
  command = body.get("command")
  if not command:
    return _error("Missing 'command'.")
  try:
    target = get_target(name)
  except FileNotFoundError:
    return _error("targets.yaml not found.", 500)
  if target is None:
    return _error(f"Target '{name}' not found.", 404)
  if is_template(name):
    return _error(f"'{name}' is a template.")
  host = target.get("host")
  if not host:
    return _error(f"No host for '{name}'.")
  user = target.get("user")
  port = target.get("port")
  key = target.get("ssh_key")
  if key:
    key = str(Path(key).expanduser())
  try:
    output = await asyncio.to_thread(
      run_ssh, host, command,
      user, port, key,
    )
    return _json({
      "status": "ok", "target": name,
      "output": output,
    })
  except SSHError as e:
    return _error(f"SSH error: {e}", 502)


# -- Pipeline routes --

async def handle_pipeline(request):
  """GET /api/pipeline/{workspace}."""
  workspace = request.match_info["workspace"]
  db.migrate()
  steps = db.get_pipeline(workspace)
  return _json(steps)


async def handle_pipeline_set(request):
  """PUT /api/pipeline/{workspace}."""
  workspace = request.match_info["workspace"]
  body = await request.json()
  steps = body.get("steps", [])
  db.migrate()
  step_defs = []
  for name in steps:
    step_defs.append({
      "name": name, "step_type": "agent",
      "config": {}, "timeout_secs": 1800,
    })
  db.define_pipeline(workspace, step_defs)
  return _json({
    "status": "ok", "workspace": workspace,
    "steps": steps,
  })


# -- Run routes --

async def handle_runs(request):
  """GET /api/runs[?workspace=X&limit=N]."""
  workspace = request.query.get("workspace")
  limit = int(request.query.get("limit", "20"))
  db.migrate()
  runs = db.list_runs(workspace=workspace, limit=limit)
  return _json(runs)


async def handle_run(request):
  """GET /api/runs/{id}."""
  run_id = int(request.match_info["id"])
  db.migrate()
  run = db.get_run(run_id)
  if run is None:
    return _error(f"Run {run_id} not found.", 404)
  steps = db.get_run_steps(run_id)
  return _json({"run": run, "steps": steps})


async def handle_run_step(request):
  """GET /api/runs/{id}/steps/{sid}."""
  step_id = int(request.match_info["sid"])
  db.migrate()
  step = db.get_step(step_id)
  if step is None:
    return _error(f"Step {step_id} not found.", 404)
  return _json(step)


async def handle_run_step_output(request):
  """GET /api/runs/{id}/steps/{sid}/output[?from=N]."""
  step_id = int(request.match_info["sid"])
  from_line = int(request.query.get("from", "0"))
  db.migrate()
  lines = db.get_output(step_id, from_line=from_line)
  return _json(lines)


async def handle_run_trigger(request):
  """POST /api/runs — trigger a manual pipeline run."""
  body = await request.json()
  workspace = body.get("workspace")
  if not workspace:
    return _error("Missing 'workspace'.")
  ws_dir = WORKSPACES_DIR / workspace
  if not ws_dir.is_dir():
    return _error(
      f"Workspace '{workspace}' not found.", 404,
    )
  db.migrate()
  try:
    status = get_workspace_status(workspace)
  except FileNotFoundError:
    return _error(
      f"Workspace '{workspace}' not found.", 404,
    )
  repos = [s["repo"] for s in status]
  refs = {}
  for s in status:
    repo_dir = ws_dir / s["repo"]
    try:
      head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True, text=True,
        cwd=str(repo_dir),
      )
      if head.returncode == 0:
        refs[s["repo"]] = head.stdout.strip()
    except Exception:
      pass
  run_id = db.create_run(
    workspace, "manual", repos,
    {f"{r}:{workspace}": h for r, h in refs.items()},
  )
  return _json({
    "status": "triggered",
    "run_id": run_id,
    "workspace": workspace,
  }, status=201)


async def handle_run_cancel(request):
  """POST /api/runs/{id}/cancel."""
  run_id = int(request.match_info["id"])
  db.migrate()
  run = db.get_run(run_id)
  if run is None:
    return _error(f"Run {run_id} not found.", 404)
  if run.get("status") not in ("queued", "running"):
    return _error(
      f"Run {run_id} is '{run.get('status')}'.", 409,
    )
  steps = db.get_run_steps(run_id)
  for step in steps:
    if step.get("status") in (
      "queued", "running", "pending",
    ):
      db.advance_step(
        step["id"], "cancelled",
        reason="manual cancel",
      )
  db.advance_run(run_id)
  return _json({"status": "cancelled", "run_id": run_id})


# -- Agent routes --

async def handle_agents(request):
  """GET /api/agents — list active agent steps."""
  db.migrate()
  agents = db.list_agent_steps(limit=50)
  return _json(agents)


# -- Config routes --

async def handle_repos(request):
  """GET /api/repos."""
  try:
    config = load_repos_config()
    return _json(config)
  except FileNotFoundError:
    return _error("repos.yaml not found.", 500)


async def handle_templates(request):
  """GET /api/templates."""
  if not TEMPLATES_DIR.is_dir():
    return _json([])
  files = sorted(TEMPLATES_DIR.glob("*.md"))
  return _json([f.stem for f in files])


async def handle_template(request):
  """GET /api/templates/{name}."""
  name = request.match_info["name"]
  path = TEMPLATES_DIR / f"{name}.md"
  if not path.is_file():
    return _error(f"Template '{name}' not found.", 404)
  return web.Response(
    text=path.read_text(),
    content_type="text/markdown",
  )


# -- SSE for live updates --

async def handle_events(request):
  """GET /api/events — SSE stream.

  Subscribes to takt-service PUB socket and forwards
  events as SSE messages. Falls back gracefully if the
  service is not running.
  """
  resp = web.StreamResponse(
    status=200,
    reason="OK",
    headers={
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "Connection": "keep-alive",
      "X-Accel-Buffering": "no",
    },
  )
  await resp.prepare(request)
  try:
    from lib.service_client import ServiceClient
    client = ServiceClient()
    await client.connect()
    ok = await client.is_service_running()
    if not ok:
      await resp.write(
        b"event: error\ndata: "
        b"{\"message\": \"service not running\"}\n\n"
      )
      await client.disconnect()
      return resp
    queue = asyncio.Queue()
    def on_event(topic, data):
      queue.put_nowait({"topic": topic, "data": data})
    for topic in (
      "step.update", "agent.output",
      "pipeline.event", "meta.update",
    ):
      client.subscribe(topic)
      client.on(topic, on_event)
    try:
      while True:
        try:
          event = await asyncio.wait_for(
            queue.get(), timeout=30,
          )
          msg = (
            f"event: {event['topic']}\n"
            f"data: {json.dumps(event['data'])}\n\n"
          )
          await resp.write(msg.encode())
        except asyncio.TimeoutError:
          await resp.write(b": keepalive\n\n")
    except (ConnectionResetError, asyncio.CancelledError):
      pass
    finally:
      await client.disconnect()
  except Exception as e:
    log.debug("SSE connection failed: %s", e)
    await resp.write(
      f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"
      .encode()
    )
  return resp


def create_app():
  """Create and configure the aiohttp application."""
  app = web.Application()
  app.router.add_get(
    "/api/workspaces", handle_workspaces,
  )
  app.router.add_post(
    "/api/workspaces", handle_workspace_create,
  )
  app.router.add_get(
    "/api/workspaces/{name}/status",
    handle_workspace_status,
  )
  app.router.add_delete(
    "/api/workspaces/{name}", handle_workspace_delete,
  )
  app.router.add_get("/api/targets", handle_targets)
  app.router.add_get(
    "/api/targets/{name}", handle_target_status,
  )
  app.router.add_post(
    "/api/targets/{name}/claim", handle_target_claim,
  )
  app.router.add_post(
    "/api/targets/{name}/release", handle_target_release,
  )
  app.router.add_post(
    "/api/targets/{name}/up", handle_target_up,
  )
  app.router.add_post(
    "/api/targets/{name}/down", handle_target_down,
  )
  app.router.add_post(
    "/api/targets/{name}/run", handle_target_run,
  )
  app.router.add_get(
    "/api/pipeline/{workspace}", handle_pipeline,
  )
  app.router.add_put(
    "/api/pipeline/{workspace}", handle_pipeline_set,
  )
  app.router.add_get("/api/runs", handle_runs)
  app.router.add_post("/api/runs", handle_run_trigger)
  app.router.add_get("/api/runs/{id}", handle_run)
  app.router.add_post(
    "/api/runs/{id}/cancel", handle_run_cancel,
  )
  app.router.add_get(
    "/api/runs/{id}/steps/{sid}", handle_run_step,
  )
  app.router.add_get(
    "/api/runs/{id}/steps/{sid}/output",
    handle_run_step_output,
  )
  app.router.add_get("/api/agents", handle_agents)
  app.router.add_get("/api/repos", handle_repos)
  app.router.add_get(
    "/api/templates", handle_templates,
  )
  app.router.add_get(
    "/api/templates/{name}", handle_template,
  )
  app.router.add_get("/api/events", handle_events)
  return app


def main():
  """Run the API server standalone."""
  import argparse
  parser = argparse.ArgumentParser(
    description="takt REST API server",
  )
  parser.add_argument(
    "--port", type=int, default=7433,
    help="Listen port (default: 7433).",
  )
  parser.add_argument(
    "--bind", default="127.0.0.1",
    help="Bind address (default: 127.0.0.1).",
  )
  args = parser.parse_args()
  logging.basicConfig(level=logging.INFO)
  app = create_app()
  web.run_app(app, host=args.bind, port=args.port)


if __name__ == "__main__":
  main()
