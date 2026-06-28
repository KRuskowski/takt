#!/usr/bin/env python3
"""Set up takt services on a fresh workstation.

Installs systemd units, builds the C++ UI and CLI,
configures /etc/hosts, and installs the MCP server.
Run after cloning the repo and installing Python deps.

Usage:
  python3 bin/setup_takt.py
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

TAKT_DIR = Path(__file__).resolve().parent.parent
HOME = Path.home()
SYSTEMD_USER = HOME / ".config/systemd/user"
HOSTS_ENTRY = "127.0.0.1 takt"


def run(cmd, **kwargs):
  """Run a command, printing it first."""
  print(f"  $ {' '.join(str(c) for c in cmd)}")
  subprocess.run(cmd, check=True, **kwargs)


def section(title):
  """Print a section header."""
  print(f"\n{'='*50}")
  print(f"  {title}")
  print(f"{'='*50}")


def setup_venv():
  """Create venv and install Python deps."""
  section("Python virtual environment")
  venv = TAKT_DIR / ".venv"
  if not venv.exists():
    run([sys.executable, "-m", "venv", str(venv)])
  pip = str(venv / "bin/pip")
  req = TAKT_DIR / "requirements.txt"
  if req.exists():
    run([pip, "install", "-q", "-r", str(req)])
  print("  done")


def setup_hosts():
  """Add takt to /etc/hosts if missing."""
  section("/etc/hosts")
  hosts = Path("/etc/hosts").read_text()
  if "takt" in hosts:
    print("  already configured")
    return
  print(f"  adding: {HOSTS_ENTRY}")
  run(
    ["sudo", "tee", "-a", "/etc/hosts"],
    input=f"\n{HOSTS_ENTRY}\n".encode(),
    stdout=subprocess.DEVNULL,
  )


def setup_build():
  """Build takt-ui and takt-cli."""
  section("C++ build (takt-ui, takt-cli)")
  build_dir = TAKT_DIR / "build"
  deps = [
    "cmake", "ninja-build", "clang", "pkg-config",
    "libssl-dev", "libzmq3-dev", "libsodium-dev",
    "libyaml-cpp-dev",
  ]
  missing = [
    d for d in deps
    if subprocess.run(
      ["dpkg", "-s", d],
      capture_output=True,
    ).returncode != 0
  ]
  if missing:
    print(f"  installing: {' '.join(missing)}")
    run(["sudo", "apt-get", "install", "-y"] + missing)

  run([
    "cmake", "-B", str(build_dir),
    "-DCMAKE_BUILD_TYPE=Debug",
    f"-S{TAKT_DIR}",
  ])
  cpus = os.cpu_count() or 4
  run([
    "cmake", "--build", str(build_dir),
    "--target", "takt-ui", "takt-cli",
    f"-j{cpus}",
  ])


def setup_services():
  """Install and enable systemd services."""
  section("systemd services")
  SYSTEMD_USER.mkdir(parents=True, exist_ok=True)

  # User services.
  for svc in ["takt-service.service",
              "einheit-ui.service"]:
    src = TAKT_DIR / "config" / svc
    dst = SYSTEMD_USER / svc
    shutil.copy2(src, dst)
    print(f"  installed {svc}")

  run(["systemctl", "--user", "daemon-reload"])
  run(["systemctl", "--user", "enable",
       "takt-service", "einheit-ui"])
  run(["systemctl", "--user", "start",
       "takt-service", "einheit-ui"])

  # System service (socat port 80).
  socat_src = TAKT_DIR / "config/takt-socat.service"
  socat_dst = Path("/etc/systemd/system/takt-socat.service")
  run(["sudo", "cp", str(socat_src), str(socat_dst)])
  run(["sudo", "systemctl", "daemon-reload"])
  run(["sudo", "systemctl", "enable", "takt-socat"])
  run(["sudo", "systemctl", "start", "takt-socat"])


def setup_mcp():
  """Install MCP server config for all claude accounts."""
  section("MCP server")
  import json
  mcp_config = {
    "mcpServers": {
      "takt": {
        "command": str(TAKT_DIR / ".venv/bin/python3"),
        "args": [str(TAKT_DIR / "bin/takt_mcp.py")],
      }
    }
  }
  mcp_json = json.dumps(mcp_config, indent=2)

  for d in [HOME, HOME / "claude-work",
            HOME / "claude-private"]:
    if d.exists():
      path = d / ".mcp.json"
      path.write_text(mcp_json)
      print(f"  wrote {path}")

  # Also in project dir.
  proj_mcp = TAKT_DIR / ".mcp.json"
  proj_mcp.write_text(mcp_json)
  print(f"  wrote {proj_mcp}")


def setup_dirs():
  """Create required directories."""
  section("directories")
  for d in ["workspaces", "root", "runs"]:
    path = HOME / "dev" / d
    path.mkdir(parents=True, exist_ok=True)
    print(f"  {path}")
  state = TAKT_DIR / ".state"
  state.mkdir(exist_ok=True)


def verify():
  """Quick smoke test."""
  section("verification")
  try:
    run(["curl", "-s", "-o", "/dev/null",
         "-w", "%{http_code}",
         "http://takt/"])
    print("  http://takt/ is live")
  except subprocess.CalledProcessError:
    print("  WARNING: http://takt/ not responding")

  try:
    run(["curl", "-s", "-o", "/dev/null",
         "-w", "%{http_code}",
         "http://127.0.0.1:7433/api/ping"])
    print("  takt API is live")
  except subprocess.CalledProcessError:
    print("  WARNING: takt API not responding")


def main():
  print("takt setup")
  print(f"project: {TAKT_DIR}")
  setup_dirs()
  setup_venv()
  setup_hosts()
  setup_build()
  setup_services()
  setup_mcp()
  verify()
  section("done")
  print("  http://takt/ — web UI")
  print("  systemctl --user status takt-service")
  print("  systemctl --user status einheit-ui")


if __name__ == "__main__":
  main()
