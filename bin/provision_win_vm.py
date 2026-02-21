#!/usr/bin/env python3
"""Provision a Windows VM as a C/C++ build environment.

SSHes into a Windows target and installs VS 2022 Build Tools
with C++ workload, Git, and configures Samba shares. Also
configures the host-side Samba share. Idempotent — each step
checks whether it's already done.

Usage:
  python3 bin/provision_win_vm.py [target_name]

Default target: win-01
"""

import argparse
import subprocess
import sys
import textwrap
import time
from pathlib import Path

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from lib.config import load_targets_config

# Samba config.
SAMBA_SHARE_NAME = "dev"
SAMBA_SHARE_PATH = "/home/karl/dev"
SAMBA_USER = "karl"
SAMBA_MARKER = "# agent-orchestration: dev share"
SMB_CONF = Path("/etc/samba/smb.conf")

# Network.
NETWORK_GATEWAY = "10.101.0.1"
NETWORK_BRIDGE = "virbr-targets"

# VS Build Tools download URL.
VS_BUILDTOOLS_URL = (
  "https://aka.ms/vs/17/release/vs_BuildTools.exe"
)

# Git for Windows download URL.
GIT_URL = (
  "https://github.com/git-for-windows/git/releases"
  "/download/v2.47.1.windows.2"
  "/Git-2.47.1.2-64-bit.exe"
)


def _ssh_cmd(user, host, key):
  """Build the base SSH command list.

  Args:
    user: Remote username.
    host: Remote hostname or IP.
    key: Path to SSH private key.

  Returns:
    List of SSH command arguments.
  """
  return [
    "ssh",
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "BatchMode=yes",
    "-i", str(key),
    f"{user}@{host}",
  ]


def _run_remote(user, host, key, command, timeout=None):
  """Run a PowerShell command on the remote host via SSH.

  Args:
    user: Remote username.
    host: Remote hostname or IP.
    key: Path to SSH private key.
    command: PowerShell command string to execute.
    timeout: Optional timeout in seconds.

  Returns:
    CompletedProcess instance.

  Raises:
    SystemExit: If the command fails.
  """
  cmd = _ssh_cmd(user, host, key) + [command]
  print(f"  $ ssh {user}@{host} {command[:70]}...")
  result = subprocess.run(
    cmd, capture_output=True, text=True, timeout=timeout,
  )
  if result.returncode != 0:
    print(f"  stdout: {result.stdout.strip()}")
    print(f"  stderr: {result.stderr.strip()}")
    print(f"  Error: remote command failed (rc={result.returncode})")
    sys.exit(1)
  return result


def _check_remote(user, host, key, command):
  """Run a remote command and return True if it succeeds.

  The command should produce a truthy/falsy PowerShell output.
  This wrapper converts PowerShell's always-zero exit code
  into a proper boolean by wrapping the command in an
  if/else with explicit exit codes.

  Args:
    user: Remote username.
    host: Remote hostname or IP.
    key: Path to SSH private key.
    command: PowerShell expression that returns a boolean.

  Returns:
    True if the command output is truthy, False otherwise.
  """
  wrapped = f"if ({command}) {{ exit 0 }} else {{ exit 1 }}"
  cmd = _ssh_cmd(user, host, key) + [wrapped]
  result = subprocess.run(cmd, capture_output=True, text=True)
  return result.returncode == 0


def _run_local(cmd, check=True, capture=False):
  """Run a local shell command."""
  print(f"  $ {' '.join(cmd)}")
  return subprocess.run(
    cmd, check=check, capture_output=capture, text=True,
  )


def resolve_target(name):
  """Load target config from targets.yaml.

  Args:
    name: Target name.

  Returns:
    Tuple of (user, host, key_path).
  """
  config = load_targets_config()
  targets = config.get("targets", {})
  if name not in targets:
    print(
      f"Error: target '{name}' not found in"
      " config/targets.yaml"
    )
    sys.exit(1)

  t = targets[name]
  user = t["user"]
  host = t["host"]
  key = Path(t["ssh_key"]).expanduser()
  return user, host, key


def wait_for_ready(user, host, key, timeout=120):
  """Wait for the Windows setup completion marker.

  Args:
    user: Remote username.
    host: Remote hostname or IP.
    key: Path to SSH private key.
    timeout: Maximum seconds to wait.

  Returns:
    True if marker found, False on timeout.
  """
  print("[..] Checking for setup completion marker...")
  deadline = time.time() + timeout
  while time.time() < deadline:
    if _check_remote(
      user, host, key,
      "Test-Path C:\\setup-complete.marker",
    ):
      print("[ok] Setup completion marker found")
      return True
    time.sleep(5)

  print(f"[warn] Marker not found after {timeout}s")
  return False


def install_vs_buildtools(user, host, key):
  """Download and install VS 2022 Build Tools with C++ workload.

  Args:
    user: Remote username.
    host: Remote hostname or IP.
    key: Path to SSH private key.
  """
  # Check if already installed.
  vs_path = (
    "C:\\Program Files (x86)\\Microsoft Visual Studio"
    "\\2022\\BuildTools"
  )
  if _check_remote(user, host, key, f"Test-Path '{vs_path}'"):
    print("[ok] VS Build Tools already installed")
    return

  print("[..] Downloading VS Build Tools (~2MB installer)...")
  _run_remote(
    user, host, key,
    f"Invoke-WebRequest -Uri '{VS_BUILDTOOLS_URL}'"
    f" -OutFile C:\\vs_BuildTools.exe",
  )

  print("[..] Installing VS Build Tools (~10 min)...")
  _run_remote(
    user, host, key,
    "Start-Process -FilePath C:\\vs_BuildTools.exe"
    " -ArgumentList '--quiet','--wait','--norestart',"
    "'--add','Microsoft.VisualStudio.Workload.VCTools',"
    "'--add',"
    "'Microsoft.VisualStudio.Component.VC.CMake.Project',"
    "'--includeRecommended'"
    " -Wait -NoNewWindow",
    timeout=1200,
  )

  # Clean up installer.
  _run_remote(
    user, host, key,
    "Remove-Item C:\\vs_BuildTools.exe -Force",
  )
  print("[ok] VS Build Tools installed")


def install_git(user, host, key):
  """Download and install Git for Windows.

  Args:
    user: Remote username.
    host: Remote hostname or IP.
    key: Path to SSH private key.
  """
  if _check_remote(
    user, host, key,
    "Test-Path 'C:\\Program Files\\Git\\cmd\\git.exe'",
  ):
    print("[ok] Git already installed")
    return

  print("[..] Downloading Git for Windows...")
  _run_remote(
    user, host, key,
    f"Invoke-WebRequest -Uri '{GIT_URL}'"
    f" -OutFile C:\\git-installer.exe",
  )

  print("[..] Installing Git...")
  _run_remote(
    user, host, key,
    "Start-Process -FilePath C:\\git-installer.exe"
    " -ArgumentList '/VERYSILENT','/NORESTART',"
    "'/NOCANCEL','/SP-',"
    "'/CLOSEAPPLICATIONS','/RESTARTAPPLICATIONS',"
    "'/COMPONENTS=ext,ext\\shellhere,ext\\guihere,"
    "gitlfs,assoc,assoc_sh'"
    " -Wait -NoNewWindow",
  )

  # Clean up installer.
  _run_remote(
    user, host, key,
    "Remove-Item C:\\git-installer.exe -Force",
  )
  print("[ok] Git installed")


def configure_samba_share(user, host, key):
  """Configure Samba share on host and map it in guest.

  Args:
    user: Remote username.
    host: Remote hostname or IP.
    key: Path to SSH private key.
  """
  # --- Host side: Samba share ---
  if SMB_CONF.exists():
    content = SMB_CONF.read_text()
    if SAMBA_MARKER in content:
      print("[ok] Samba share already configured on host")
    else:
      print("[..] Adding Samba share on host...")
      share_config = textwrap.dedent(f"""\

        {SAMBA_MARKER}
        [{SAMBA_SHARE_NAME}]
          path = {SAMBA_SHARE_PATH}
          browseable = yes
          read only = no
          guest ok = no
          valid users = {SAMBA_USER}
          create mask = 0644
          directory mask = 0755
      """)
      try:
        with open(SMB_CONF, "a") as f:
          f.write(share_config)
      except PermissionError:
        print("[skip] No permission to write smb.conf"
              " (run with sudo for Samba setup)")
        return

      # Restart smbd to pick up changes.
      _run_local(
        ["systemctl", "restart", "smbd"],
        check=False,
      )
      print("[ok] Samba share added on host")
  else:
    print("[skip] /etc/samba/smb.conf not found")

  # Allow SMB through the targets bridge firewall.
  _run_local(
    ["iptables", "-C", "INPUT",
     "-i", NETWORK_BRIDGE,
     "-p", "tcp", "--dport", "445",
     "-j", "ACCEPT"],
    check=False, capture=True,
  )
  # Add rule if check failed (not present).
  _run_local(
    ["iptables", "-I", "INPUT", "1",
     "-i", NETWORK_BRIDGE,
     "-p", "tcp", "--dport", "445",
     "-j", "ACCEPT"],
    check=False, capture=True,
  )

  # --- Guest side: map network drive ---
  if _check_remote(
    user, host, key,
    "Get-PSDrive W -ErrorAction SilentlyContinue",
  ):
    print("[ok] W: drive already mapped in guest")
    return

  print("[..] Mapping network drive in guest...")
  _run_remote(
    user, host, key,
    f"cmdkey /add:{NETWORK_GATEWAY}"
    f" /user:{SAMBA_USER} /pass:changeme",
  )

  # Create a persistent drive mapping via scheduled task
  # (PSDrive doesn't persist across SSH sessions well).
  _run_remote(
    user, host, key,
    f"net use W: \\\\{NETWORK_GATEWAY}\\{SAMBA_SHARE_NAME}"
    f" /persistent:yes",
  )
  print("[ok] W: drive mapped to host dev share")


def configure_vs_path(user, host, key):
  """Create PowerShell profile that sources vcvars64.bat.

  Args:
    user: Remote username.
    host: Remote hostname or IP.
    key: Path to SSH private key.
  """
  marker = "# agent-orchestration: vcvars"
  if _check_remote(
    user, host, key,
    f"Select-String -Path $PROFILE.AllUsersAllHosts"
    f" -Pattern '{marker}'"
    f" -ErrorAction SilentlyContinue",
  ):
    print("[ok] VS environment already in PowerShell profile")
    return

  print("[..] Configuring VS environment for SSH sessions...")
  # Allow PowerShell profile scripts to run on SSH login.
  _run_remote(
    user, host, key,
    "powershell -NoProfile -Command"
    " \"Set-ExecutionPolicy -ExecutionPolicy RemoteSigned"
    " -Scope LocalMachine -Force\"",
  )

  # The profile script runs vcvars64.bat in a cmd subprocess,
  # captures the env vars, and imports them into PowerShell.
  # Use base64 encoding to avoid shell escaping issues.
  import base64
  profile_script = textwrap.dedent(f"""\
    {marker}
    $vcvars = "C:\\Program Files (x86)\\Microsoft Visual Studio\\2022\\BuildTools\\VC\\Auxiliary\\Build\\vcvars64.bat"
    if (Test-Path $vcvars) {{
      $out = cmd /c "`"$vcvars`" >nul 2>&1 && set"
      foreach ($line in $out) {{
        if ($line -match '^([^=]+)=(.*)$') {{
          [System.Environment]::SetEnvironmentVariable(
            $matches[1], $matches[2], 'Process')
        }}
      }}
    }}
  """)
  encoded = base64.b64encode(
    profile_script.encode("utf-8")
  ).decode("ascii")

  _run_remote(
    user, host, key,
    "$p = $PROFILE.AllUsersAllHosts;"
    " $d = Split-Path $p;"
    " if (!(Test-Path $d)) { New-Item -Path $d"
    " -ItemType Directory -Force };"
    f" $b = [System.Convert]::FromBase64String('{encoded}');"
    " $s = [System.Text.Encoding]::UTF8.GetString($b);"
    " Add-Content -Path $p -Value $s",
  )
  print("[ok] VS environment configured for SSH sessions")


def disable_autologon(user, host, key):
  """Remove AutoLogon registry keys.

  Args:
    user: Remote username.
    host: Remote hostname or IP.
    key: Path to SSH private key.
  """
  if not _check_remote(
    user, host, key,
    "Get-ItemProperty"
    " 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT"
    "\\CurrentVersion\\Winlogon'"
    " -Name AutoAdminLogon"
    " -ErrorAction SilentlyContinue"
    " | Where-Object { $_.AutoAdminLogon -eq '1' }",
  ):
    print("[ok] AutoLogon already disabled")
    return

  print("[..] Disabling AutoLogon...")
  _run_remote(
    user, host, key,
    "Set-ItemProperty"
    " 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT"
    "\\CurrentVersion\\Winlogon'"
    " -Name AutoAdminLogon -Value '0';"
    " Remove-ItemProperty"
    " 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT"
    "\\CurrentVersion\\Winlogon'"
    " -Name DefaultPassword"
    " -ErrorAction SilentlyContinue",
  )
  print("[ok] AutoLogon disabled")


def eject_cdroms(vm_name):
  """Detach CD-ROM images from the VM.

  Args:
    vm_name: Name of the libvirt VM.
  """
  print("[..] Ejecting CD-ROMs...")
  result = _run_local(
    ["virsh", "domblklist", vm_name],
    check=False, capture=True,
  )
  if result.returncode != 0:
    print("[skip] Could not list VM block devices")
    return

  ejected = 0
  for line in result.stdout.splitlines():
    parts = line.split()
    if len(parts) >= 2 and parts[0].startswith("sda"):
      # SATA CD-ROM devices.
      _run_local(
        ["virsh", "change-media", vm_name,
         parts[0], "--eject"],
        check=False, capture=True,
      )
      ejected += 1
    elif len(parts) >= 2 and parts[0].startswith("sdb"):
      _run_local(
        ["virsh", "change-media", vm_name,
         parts[0], "--eject"],
        check=False, capture=True,
      )
      ejected += 1
    elif len(parts) >= 2 and parts[0].startswith("sdc"):
      _run_local(
        ["virsh", "change-media", vm_name,
         parts[0], "--eject"],
        check=False, capture=True,
      )
      ejected += 1

  if ejected > 0:
    print(f"[ok] Ejected {ejected} CD-ROM(s)")
  else:
    print("[ok] No CD-ROMs to eject")


def provision(name):
  """Provision a Windows VM as a C/C++ build environment.

  Args:
    name: Target name from config/targets.yaml.
  """
  print(f"Provisioning Windows target: {name}")
  user, host, key = resolve_target(name)
  print(f"  Host: {host}")
  print(f"  User: {user}")
  print()

  wait_for_ready(user, host, key)
  install_vs_buildtools(user, host, key)
  install_git(user, host, key)
  configure_samba_share(user, host, key)
  configure_vs_path(user, host, key)
  disable_autologon(user, host, key)
  eject_cdroms(name)

  print()
  print(f"Provisioning complete for {name}.")
  print()
  print("  Verify with:")
  print(f"    bin/target.py status {name}")
  print(f"    bin/target.py run {name}"
        f" \"cl.exe 2>&1 | Select -First 1\"")
  print(f"    bin/target.py run {name} \"cmake --version\"")
  print(f"    bin/target.py run {name} \"git --version\"")


def main():
  """Parse arguments and run provisioning."""
  parser = argparse.ArgumentParser(
    description=(
      "Provision a Windows VM as a C/C++ build environment."
    ),
  )
  parser.add_argument(
    "target", nargs="?", default="win-01",
    help="Target name from config/targets.yaml"
         " (default: win-01)",
  )
  args = parser.parse_args()
  provision(args.target)


if __name__ == "__main__":
  main()
