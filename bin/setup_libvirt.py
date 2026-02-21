#!/usr/bin/env python3
"""Automated QEMU/KVM + libvirt setup for VM build targets.

Installs packages, configures networking, generates SSH keys,
and provisions a Debian 12 cloud VM. Requires sudo. Idempotent
— skips steps that are already done.

Network: 10.101.0.0/20 with host at 10.101.0.1 acting as
the NAT gateway to the outside.
"""

import os
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

# Network config.
NETWORK_NAME = "targets"
NETWORK_BRIDGE = "virbr-targets"
NETWORK_SUBNET = "10.101.0.0"
NETWORK_PREFIX = 20
NETWORK_GATEWAY = "10.101.0.1"
NETWORK_DHCP_START = "10.101.8.1"
NETWORK_DHCP_END = "10.101.15.254"
NETWORK_NETMASK = "255.255.240.0"

# VM defaults.
VM_NAME = "deb-01"
VM_VCPUS = 4
VM_RAM_MB = 4096
VM_DISK_GB = 40
VM_IP = "10.101.0.20"
VM_USER = "worker"

CLOUD_IMAGE_URL = (
  "https://cloud.debian.org/images/cloud/bookworm/latest/"
  "debian-12-generic-amd64.qcow2"
)
IMAGES_DIR = Path("/var/lib/libvirt/images")

# Resolve the real user's home, not root's, when run via sudo.
_REAL_USER = os.environ.get("SUDO_USER", os.environ.get("USER"))
_REAL_HOME = Path(
  os.path.expanduser(f"~{_REAL_USER}")
  if _REAL_USER else os.path.expanduser("~")
)
SSH_KEY_PATH = _REAL_HOME / ".ssh" / "id_ed25519_targets"

REQUIRED_PACKAGES = [
  "qemu-system-x86",
  "qemu-utils",
  "libvirt-daemon-system",
  "libvirt-clients",
  "virtinst",
  "ovmf",
  "genisoimage",
  "wget",
]

NETWORK_XML = f"""\
<network>
  <name>{NETWORK_NAME}</name>
  <bridge name='{NETWORK_BRIDGE}' stp='on' delay='0'/>
  <forward mode='nat'>
    <nat>
      <port start='1024' end='65535'/>
    </nat>
  </forward>
  <ip address='{NETWORK_GATEWAY}' netmask='{NETWORK_NETMASK}'>
    <dhcp>
      <range start='{NETWORK_DHCP_START}' \
end='{NETWORK_DHCP_END}'/>
    </dhcp>
  </ip>
</network>
"""


def run(cmd, check=True, capture=False, **kwargs):
  """Run a shell command, printing it first."""
  print(f"  $ {' '.join(cmd)}")
  return subprocess.run(
    cmd,
    check=check,
    capture_output=capture,
    text=True,
    **kwargs,
  )


def is_root():
  """Check if running as root."""
  return os.geteuid() == 0


def check_kvm():
  """Verify /dev/kvm exists."""
  if not Path("/dev/kvm").exists():
    print("Error: /dev/kvm not found.")
    print("Enable hardware virtualization (VT-x/AMD-V) in BIOS.")
    sys.exit(1)
  print("[ok] /dev/kvm present")


def install_packages():
  """Install QEMU/libvirt packages if missing."""
  result = run(
    ["dpkg", "-s"] + REQUIRED_PACKAGES,
    check=False, capture=True,
  )
  if result.returncode == 0:
    print("[ok] All packages already installed")
    return

  print("[..] Installing packages...")
  run(["apt-get", "update", "-qq"])
  run(["apt-get", "install", "-y", "-qq"] + REQUIRED_PACKAGES)
  print("[ok] Packages installed")


def add_user_to_groups():
  """Add the invoking user to libvirt and kvm groups."""
  user = os.environ.get("SUDO_USER", os.environ.get("USER"))
  if not user or user == "root":
    print("[skip] Running as root, no user groups to add")
    return

  for group in ("libvirt", "kvm"):
    result = run(
      ["id", "-nG", user], check=False, capture=True,
    )
    if group in result.stdout.split():
      print(f"[ok] {user} already in {group} group")
    else:
      run(["usermod", "-aG", group, user])
      print(f"[ok] Added {user} to {group} group")


def start_libvirtd():
  """Enable and start libvirtd."""
  result = run(
    ["systemctl", "is-active", "libvirtd"],
    check=False, capture=True,
  )
  if result.stdout.strip() == "active":
    print("[ok] libvirtd already running")
  else:
    run(["systemctl", "enable", "--now", "libvirtd"])
    print("[ok] libvirtd started and enabled")


def setup_network():
  """Create and start the targets NAT network."""
  result = run(
    ["virsh", "net-info", NETWORK_NAME],
    check=False, capture=True,
  )
  if result.returncode != 0:
    print(f"[..] Creating '{NETWORK_NAME}' network...")
    with tempfile.NamedTemporaryFile(
      mode="w", suffix=".xml", delete=False,
    ) as f:
      f.write(NETWORK_XML)
      xml_path = f.name
    try:
      run(["virsh", "net-define", xml_path])
    finally:
      os.unlink(xml_path)
    run(["virsh", "net-start", NETWORK_NAME])
    run(["virsh", "net-autostart", NETWORK_NAME])
    print(f"[ok] Network '{NETWORK_NAME}' created and started")
    return

  # Parse virsh output with flexible whitespace.
  lines = {
    l.split(":")[0].strip().lower(): l.split(":", 1)[1].strip()
    for l in result.stdout.splitlines() if ":" in l
  }
  if lines.get("active") == "yes":
    print(f"[ok] Network '{NETWORK_NAME}' already active")
  else:
    run(["virsh", "net-start", NETWORK_NAME])
    print(f"[ok] Network '{NETWORK_NAME}' started")

  if lines.get("autostart") != "yes":
    run(["virsh", "net-autostart", NETWORK_NAME])


def enable_ip_forwarding():
  """Enable IPv4 forwarding persistently."""
  sysctl_conf = Path("/etc/sysctl.d/90-ip-forward.conf")
  current = run(
    ["sysctl", "-n", "net.ipv4.ip_forward"],
    check=False, capture=True,
  )
  if current.stdout.strip() == "1":
    print("[ok] IP forwarding already enabled")
  else:
    run(["sysctl", "-w", "net.ipv4.ip_forward=1"])
    print("[ok] IP forwarding enabled")

  if sysctl_conf.exists():
    content = sysctl_conf.read_text()
    if "net.ipv4.ip_forward=1" in content:
      print("[ok] IP forwarding persisted in sysctl")
      return

  sysctl_conf.write_text("net.ipv4.ip_forward=1\n")
  print(f"[ok] Wrote {sysctl_conf}")


def generate_ssh_key():
  """Generate an ed25519 SSH keypair for VM access."""
  if SSH_KEY_PATH.exists():
    print(f"[ok] SSH key already exists: {SSH_KEY_PATH}")
    return

  SSH_KEY_PATH.parent.mkdir(mode=0o700, exist_ok=True)
  run([
    "ssh-keygen", "-t", "ed25519",
    "-f", str(SSH_KEY_PATH),
    "-N", "",
    "-C", "agent-orchestration-targets",
  ])

  # Fix ownership if running under sudo.
  user = os.environ.get("SUDO_USER")
  if user:
    run(["chown", f"{user}:{user}", str(SSH_KEY_PATH)])
    run(["chown", f"{user}:{user}", str(SSH_KEY_PATH) + ".pub"])

  print(f"[ok] SSH key generated: {SSH_KEY_PATH}")


def vm_exists(name):
  """Check if a libvirt VM exists (running or stopped)."""
  result = run(
    ["virsh", "dominfo", name],
    check=False, capture=True,
  )
  return result.returncode == 0


def write_cloud_init_configs(tmpdir):
  """Write cloud-init user-data and network-config files.

  Args:
    tmpdir: Path to a temporary directory for config files.

  Returns:
    Tuple of (user_data_path, network_config_path,
    meta_data_path).
  """
  pub_key = SSH_KEY_PATH.with_suffix(".pub").read_text().strip()

  user_data = textwrap.dedent(f"""\
    #cloud-config
    hostname: {VM_NAME}
    users:
      - name: {VM_USER}
        sudo: ALL=(ALL) NOPASSWD:ALL
        shell: /bin/bash
        ssh_authorized_keys:
          - {pub_key}
    packages:
      - build-essential
      - cmake
      - git
      - python3
      - python3-pip
    package_update: true
    package_upgrade: false
  """)

  network_config = textwrap.dedent(f"""\
    version: 2
    ethernets:
      enp1s0:
        addresses:
          - {VM_IP}/{NETWORK_PREFIX}
        gateway4: {NETWORK_GATEWAY}
        nameservers:
          addresses:
            - {NETWORK_GATEWAY}
            - 8.8.8.8
  """)

  meta_data = textwrap.dedent(f"""\
    instance-id: {VM_NAME}
    local-hostname: {VM_NAME}
  """)

  user_data_path = Path(tmpdir) / "user-data"
  network_config_path = Path(tmpdir) / "network-config"
  meta_data_path = Path(tmpdir) / "meta-data"

  user_data_path.write_text(user_data)
  network_config_path.write_text(network_config)
  meta_data_path.write_text(meta_data)

  return user_data_path, network_config_path, meta_data_path


def download_cloud_image():
  """Download the Debian cloud image if not present."""
  image_path = IMAGES_DIR / "debian-12-generic-amd64.qcow2"
  if image_path.exists():
    print(f"[ok] Cloud image already exists: {image_path}")
    return image_path

  IMAGES_DIR.mkdir(parents=True, exist_ok=True)
  print("[..] Downloading Debian 12 cloud image...")
  run(["wget", "-q", "--show-progress", "-O", str(image_path),
       CLOUD_IMAGE_URL])
  print(f"[ok] Cloud image downloaded: {image_path}")
  return image_path


def create_vm_disk(base_image):
  """Create a VM disk backed by the cloud image.

  Args:
    base_image: Path to the base cloud image.

  Returns:
    Path to the new VM disk.
  """
  disk_path = IMAGES_DIR / f"{VM_NAME}.qcow2"
  if disk_path.exists():
    print(f"[ok] VM disk already exists: {disk_path}")
    return disk_path

  # Create a copy (not backing file) so we can resize.
  run(["cp", str(base_image), str(disk_path)])
  run(["qemu-img", "resize", str(disk_path), f"{VM_DISK_GB}G"])
  print(f"[ok] VM disk created: {disk_path} ({VM_DISK_GB}GB)")
  return disk_path


def create_cloud_init_iso(tmpdir):
  """Create a cloud-init NoCloud ISO from config files.

  Args:
    tmpdir: Directory containing user-data, meta-data, and
      network-config files.

  Returns:
    Path to the generated ISO.
  """
  iso_path = IMAGES_DIR / f"{VM_NAME}-cidata.iso"
  if iso_path.exists():
    iso_path.unlink()

  run([
    "genisoimage", "-output", str(iso_path),
    "-volid", "cidata", "-joliet", "-rock",
    str(Path(tmpdir) / "user-data"),
    str(Path(tmpdir) / "meta-data"),
    str(Path(tmpdir) / "network-config"),
  ])
  print(f"[ok] Cloud-init ISO created: {iso_path}")
  return iso_path


def create_vm():
  """Provision the Debian VM using virt-install."""
  if vm_exists(VM_NAME):
    print(f"[ok] VM '{VM_NAME}' already exists")
    return

  base_image = download_cloud_image()
  disk_path = create_vm_disk(base_image)

  with tempfile.TemporaryDirectory() as tmpdir:
    write_cloud_init_configs(tmpdir)
    cidata_iso = create_cloud_init_iso(tmpdir)

  print(f"[..] Creating VM '{VM_NAME}'...")
  run([
    "virt-install",
    "--name", VM_NAME,
    "--virt-type", "kvm",
    "--cpu", "host",
    "--vcpus", str(VM_VCPUS),
    "--memory", str(VM_RAM_MB),
    "--disk", f"path={disk_path},format=qcow2",
    "--disk", f"path={cidata_iso},device=cdrom",
    "--os-variant", "debian12",
    "--network", f"network={NETWORK_NAME}",
    "--graphics", "none",
    "--console", "pty,target_type=serial",
    "--noautoconsole",
    "--import",
  ])
  print(f"[ok] VM '{VM_NAME}' created")


def wait_for_ssh(host, user, timeout=180):
  """Wait for SSH to become reachable on the VM.

  Args:
    host: IP address to connect to.
    user: SSH user.
    timeout: Maximum seconds to wait.
  """
  print(f"[..] Waiting for SSH on {host} (up to {timeout}s)...")
  deadline = time.time() + timeout
  while time.time() < deadline:
    result = subprocess.run(
      ["ssh",
       "-o", "ConnectTimeout=5",
       "-o", "StrictHostKeyChecking=accept-new",
       "-o", "BatchMode=yes",
       "-i", str(SSH_KEY_PATH),
       f"{user}@{host}", "echo ok"],
      capture_output=True, text=True,
    )
    if result.returncode == 0:
      print(f"[ok] SSH reachable on {host}")
      return True
    time.sleep(5)

  print(f"[warn] SSH not reachable after {timeout}s")
  print("  The VM may still be booting. Try again with:")
  print(f"  ssh -i {SSH_KEY_PATH} {user}@{host}")
  return False


def configure_ssh_config():
  """Add an SSH config entry for the VM."""
  ssh_config = _REAL_HOME / ".ssh" / "config"
  marker = f"# agent-orchestration: {VM_NAME}"

  if ssh_config.exists():
    content = ssh_config.read_text()
    if marker in content:
      print(
        f"[ok] SSH config entry for {VM_NAME} already exists"
      )
      return
  else:
    content = ""

  entry = textwrap.dedent(f"""\

    {marker}
    Host {VM_NAME}
      HostName {VM_IP}
      User {VM_USER}
      IdentityFile {SSH_KEY_PATH}
      StrictHostKeyChecking accept-new
  """)

  with open(ssh_config, "a") as f:
    f.write(entry)
  ssh_config.chmod(0o600)

  # Fix ownership if running under sudo.
  user = os.environ.get("SUDO_USER")
  if user:
    run(["chown", f"{user}:{user}", str(ssh_config)])

  print(f"[ok] SSH config entry added for {VM_NAME}")


def print_summary(ssh_ok):
  """Print a summary of the setup."""
  print()
  print("=" * 60)
  print("  QEMU/KVM Setup Complete")
  print("=" * 60)
  print()
  print(f"  Network:    {NETWORK_SUBNET}/{NETWORK_PREFIX}")
  print(f"  Gateway:    {NETWORK_GATEWAY} (this host)")
  print(f"  Bridge:     {NETWORK_BRIDGE}")
  print()
  print(f"  VM name:    {VM_NAME}")
  print(f"  IP address: {VM_IP}")
  print(f"  User:       {VM_USER}")
  print(f"  vCPUs:      {VM_VCPUS}")
  print(f"  RAM:        {VM_RAM_MB}MB")
  print(f"  Disk:       {VM_DISK_GB}GB")
  print(f"  SSH key:    {SSH_KEY_PATH}")
  print()
  if ssh_ok:
    print("  SSH:        connected")
  else:
    print(
      "  SSH:        not yet reachable "
      "(VM may still be booting)"
    )
  print()
  print("  Verify with:")
  print(f"    bin/target.py status {VM_NAME}")
  print()
  print("  To add a Windows VM later:")
  print("    1. Download a Windows ISO")
  print("    2. Use virt-install with --cdrom and OVMF UEFI:")
  print("       virt-install --name win-01 \\")
  print("         --virt-type kvm --cpu host \\")
  print("         --vcpus 4 --memory 8192 \\")
  print("         --disk size=80 \\")
  print("         --cdrom /path/to/windows.iso \\")
  print("         --os-variant win11 \\")
  print("         --boot uefi \\")
  print(f"         --network network={NETWORK_NAME}")
  print()


def main():
  """Run the full setup."""
  if not is_root():
    print("Error: this script must be run as root (use sudo).")
    sys.exit(1)

  print("Setting up QEMU/KVM with libvirt...")
  print(f"  Network: {NETWORK_SUBNET}/{NETWORK_PREFIX}")
  print(f"  Gateway: {NETWORK_GATEWAY} (this host)")
  print()

  check_kvm()
  install_packages()
  add_user_to_groups()
  start_libvirtd()
  setup_network()
  enable_ip_forwarding()
  generate_ssh_key()
  configure_ssh_config()
  create_vm()
  ssh_ok = wait_for_ssh(VM_IP, VM_USER)
  print_summary(ssh_ok)


if __name__ == "__main__":
  main()
