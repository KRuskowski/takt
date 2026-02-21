#!/usr/bin/env python3
"""Automated Windows 11 VM setup for C/C++ build targets.

Creates a UEFI+TPM2 Windows 11 Pro VM with VirtIO drivers,
unattended install, and SSH access. Requires sudo. Idempotent
— skips steps that are already done.

Storage: disk lives on /home (large partition) rather than
/var/lib/libvirt/images (small partition).

Usage:
  sudo python3 bin/setup_win_vm.py
"""

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

# VM config.
VM_NAME = "win-01"
VM_IP = "10.101.0.21"
VM_VCPUS = 8
VM_RAM_MB = 8192
VM_DISK_GB = 120
VM_USER = "worker"
VM_PASSWORD = "worker"

# Network (matches setup_libvirt.py).
NETWORK_NAME = "targets"
NETWORK_GATEWAY = "10.101.0.1"
NETWORK_PREFIX = 20

# Paths.
IMAGES_DIR = Path("/home/karl/libvirt/images")
WIN_ISO = Path("/media/karl/Ventoy/Win23H2_engl.iso")
VIRTIO_ISO = IMAGES_DIR / "virtio-win.iso"
VIRTIO_URL = (
  "https://fedorapeople.org/groups/virt/virtio-win/direct-downloads"
  "/stable-virtio/virtio-win.iso"
)
DISK_PATH = IMAGES_DIR / f"{VM_NAME}.qcow2"
AUTOUNATTEND_ISO = IMAGES_DIR / f"{VM_NAME}-autounattend.iso"

APPARMOR_LOCAL = Path(
  "/etc/apparmor.d/local/abstractions/libvirt-qemu"
)

# Resolve the real user's home when run via sudo.
_REAL_USER = os.environ.get("SUDO_USER", os.environ.get("USER"))
_REAL_HOME = Path(
  os.path.expanduser(f"~{_REAL_USER}")
  if _REAL_USER else os.path.expanduser("~")
)
SSH_KEY_PATH = _REAL_HOME / ".ssh" / "id_ed25519_targets"

# Add project root to path.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))


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


def vm_exists(name):
  """Check if a libvirt VM exists (running or stopped)."""
  result = run(
    ["virsh", "dominfo", name],
    check=False, capture=True,
  )
  return result.returncode == 0


def ensure_packages():
  """Install samba and genisoimage if missing."""
  packages = ["samba", "genisoimage"]
  result = run(
    ["dpkg", "-s"] + packages,
    check=False, capture=True,
  )
  if result.returncode == 0:
    print("[ok] Packages already installed")
    return

  print("[..] Installing packages...")
  run(["apt-get", "update", "-qq"])
  run(["apt-get", "install", "-y", "-qq"] + packages)
  print("[ok] Packages installed")


def create_storage_dir():
  """Create IMAGES_DIR with correct ownership and AppArmor."""
  if IMAGES_DIR.exists():
    print(f"[ok] Storage dir exists: {IMAGES_DIR}")
  else:
    IMAGES_DIR.mkdir(parents=True)
    run(["chown", "libvirt-qemu:kvm", str(IMAGES_DIR)])
    print(f"[ok] Created {IMAGES_DIR}")

  # AppArmor: allow libvirt to access the custom path.
  rule = f'  "{IMAGES_DIR}/**" rwk,'
  if APPARMOR_LOCAL.exists():
    content = APPARMOR_LOCAL.read_text()
    if str(IMAGES_DIR) in content:
      print("[ok] AppArmor rule already present")
      return
  else:
    content = ""

  APPARMOR_LOCAL.parent.mkdir(parents=True, exist_ok=True)
  with open(APPARMOR_LOCAL, "a") as f:
    f.write("\n# agent-orchestration: custom image store\n")
    f.write(f"{rule}\n")

  # Reload apparmor profile.
  run(
    ["apparmor_parser", "-r",
     "/etc/apparmor.d/usr.lib.libvirt.virt-aa-helper"],
    check=False, capture=True,
  )
  # Also reload the libvirt-qemu abstraction consumer.
  run(
    ["apparmor_parser", "-r",
     "/etc/apparmor.d/libvirt/TEMPLATE.qemu"],
    check=False, capture=True,
  )
  print("[ok] AppArmor rule added")


def download_virtio_iso():
  """Download VirtIO drivers ISO from Fedora."""
  if VIRTIO_ISO.exists():
    print(f"[ok] VirtIO ISO exists: {VIRTIO_ISO}")
    return

  print("[..] Downloading VirtIO drivers ISO (~600MB)...")
  run([
    "wget", "-q", "--show-progress",
    "-O", str(VIRTIO_ISO), VIRTIO_URL,
  ])
  print(f"[ok] VirtIO ISO downloaded: {VIRTIO_ISO}")


def create_vm_disk():
  """Create a qcow2 disk image for the VM."""
  if DISK_PATH.exists():
    print(f"[ok] VM disk exists: {DISK_PATH}")
    return

  print(f"[..] Creating {VM_DISK_GB}GB disk...")
  run([
    "qemu-img", "create", "-f", "qcow2",
    str(DISK_PATH), f"{VM_DISK_GB}G",
  ])
  print(f"[ok] VM disk created: {DISK_PATH}")


def _generate_autounattend_xml():
  """Generate autounattend.xml content for unattended install.

  Returns:
    XML string for Windows unattended setup.
  """
  pub_key = SSH_KEY_PATH.with_suffix(".pub").read_text().strip()
  product_key = "8WRPJ-JNGPC-68MHF-T87DR-JHV3B"

  # VirtIO driver paths — try multiple CD letters since
  # Windows assigns them variably.
  driver_paths = ""
  for letter in ("D", "E", "F"):
    for subdir in ("amd64/w11", "amd64/w10", "w11/amd64"):
      driver_paths += (
        f'            <PathAndCredentials '
        f'wcm:action="add" wcm:keyValue="{letter}_{subdir}">'
        f'\n              <Path>{letter}:\\{subdir}</Path>'
        f'\n            </PathAndCredentials>\n'
      )

  return textwrap.dedent(f"""\
    <?xml version="1.0" encoding="utf-8"?>
    <unattend xmlns="urn:schemas-microsoft-com:unattend"
              xmlns:wcm="http://schemas.microsoft.com/WMIConfig\
/2002/State">

      <!-- windowsPE: partition disk, load VirtIO drivers -->
      <settings pass="windowsPE">
        <component name="Microsoft-Windows-International-Core-WinPE"
                   processorArchitecture="amd64"
                   publicKeyToken="31bf3856ad364e35"
                   language="neutral" versionScope="nonSxS">
          <SetupUILanguage>
            <UILanguage>en-US</UILanguage>
          </SetupUILanguage>
          <InputLocale>en-US</InputLocale>
          <SystemLocale>en-US</SystemLocale>
          <UILanguage>en-US</UILanguage>
          <UserLocale>en-US</UserLocale>
        </component>

        <component name="Microsoft-Windows-PnpCustomizationsWinPE"
                   processorArchitecture="amd64"
                   publicKeyToken="31bf3856ad364e35"
                   language="neutral" versionScope="nonSxS">
          <DriverPaths>
{driver_paths}\
          </DriverPaths>
        </component>

        <component name="Microsoft-Windows-Setup"
                   processorArchitecture="amd64"
                   publicKeyToken="31bf3856ad364e35"
                   language="neutral" versionScope="nonSxS">
          <DiskConfiguration>
            <Disk wcm:action="add">
              <DiskID>0</DiskID>
              <WillWipeDisk>true</WillWipeDisk>
              <CreatePartitions>
                <CreatePartition wcm:action="add">
                  <Order>1</Order>
                  <Size>100</Size>
                  <Type>EFI</Type>
                </CreatePartition>
                <CreatePartition wcm:action="add">
                  <Order>2</Order>
                  <Size>16</Size>
                  <Type>MSR</Type>
                </CreatePartition>
                <CreatePartition wcm:action="add">
                  <Order>3</Order>
                  <Extend>true</Extend>
                  <Type>Primary</Type>
                </CreatePartition>
              </CreatePartitions>
              <ModifyPartitions>
                <ModifyPartition wcm:action="add">
                  <Order>1</Order>
                  <PartitionID>1</PartitionID>
                  <Format>FAT32</Format>
                  <Label>EFI</Label>
                </ModifyPartition>
                <ModifyPartition wcm:action="add">
                  <Order>2</Order>
                  <PartitionID>3</PartitionID>
                  <Format>NTFS</Format>
                  <Label>Windows</Label>
                </ModifyPartition>
              </ModifyPartitions>
            </Disk>
          </DiskConfiguration>
          <ImageInstall>
            <OSImage>
              <InstallTo>
                <DiskID>0</DiskID>
                <PartitionID>3</PartitionID>
              </InstallTo>
              <InstallFrom>
                <MetaData wcm:action="add">
                  <Key>/IMAGE/NAME</Key>
                  <Value>Windows 11 Pro</Value>
                </MetaData>
              </InstallFrom>
            </OSImage>
          </ImageInstall>
          <UserData>
            <ProductKey>
              <Key>{product_key}</Key>
            </ProductKey>
            <AcceptEula>true</AcceptEula>
          </UserData>
        </component>
      </settings>

      <!-- specialize: hostname -->
      <settings pass="specialize">
        <component name="Microsoft-Windows-Shell-Setup"
                   processorArchitecture="amd64"
                   publicKeyToken="31bf3856ad364e35"
                   language="neutral" versionScope="nonSxS">
          <ComputerName>{VM_NAME}</ComputerName>
        </component>
      </settings>

      <!-- oobeSystem: user, network, SSH, tweaks -->
      <settings pass="oobeSystem">
        <component name="Microsoft-Windows-International-Core"
                   processorArchitecture="amd64"
                   publicKeyToken="31bf3856ad364e35"
                   language="neutral" versionScope="nonSxS">
          <InputLocale>en-US</InputLocale>
          <SystemLocale>en-US</SystemLocale>
          <UILanguage>en-US</UILanguage>
          <UserLocale>en-US</UserLocale>
        </component>

        <component name="Microsoft-Windows-Shell-Setup"
                   processorArchitecture="amd64"
                   publicKeyToken="31bf3856ad364e35"
                   language="neutral" versionScope="nonSxS">
          <OOBE>
            <HideEULAPage>true</HideEULAPage>
            <HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE>
            <NetworkLocation>Work</NetworkLocation>
            <ProtectYourPC>3</ProtectYourPC>
            <SkipMachineOOBE>true</SkipMachineOOBE>
            <SkipUserOOBE>true</SkipUserOOBE>
          </OOBE>

          <UserAccounts>
            <LocalAccounts>
              <LocalAccount wcm:action="add">
                <Name>{VM_USER}</Name>
                <Group>Administrators</Group>
                <Password>
                  <Value>{VM_PASSWORD}</Value>
                  <PlainText>true</PlainText>
                </Password>
              </LocalAccount>
            </LocalAccounts>
          </UserAccounts>

          <AutoLogon>
            <Enabled>true</Enabled>
            <Username>{VM_USER}</Username>
            <Password>
              <Value>{VM_PASSWORD}</Value>
              <PlainText>true</PlainText>
            </Password>
            <LogonCount>1</LogonCount>
          </AutoLogon>

          <FirstLogonCommands>
            <!-- 1. Set static IP -->
            <SynchronousCommand wcm:action="add">
              <Order>1</Order>
              <CommandLine>powershell -NoProfile -Command "\
$idx = (Get-NetAdapter | Select -First 1).ifIndex; \
New-NetIPAddress -InterfaceIndex $idx \
-IPAddress {VM_IP} -PrefixLength {NETWORK_PREFIX} \
-DefaultGateway {NETWORK_GATEWAY}; \
Set-DnsClientServerAddress -InterfaceIndex $idx \
-ServerAddresses ('{NETWORK_GATEWAY}','8.8.8.8')"</CommandLine>
              <Description>Set static IP</Description>
            </SynchronousCommand>

            <!-- 2. Install and start OpenSSH Server -->
            <SynchronousCommand wcm:action="add">
              <Order>2</Order>
              <CommandLine>powershell -NoProfile -Command "\
Add-WindowsCapability -Online \
-Name OpenSSH.Server~~~~0.0.1.0; \
Start-Service sshd; \
Set-Service -Name sshd -StartupType Automatic"</CommandLine>
              <Description>Install OpenSSH Server</Description>
            </SynchronousCommand>

            <!-- 3. Write SSH pubkey for admin user -->
            <SynchronousCommand wcm:action="add">
              <Order>3</Order>
              <CommandLine>powershell -NoProfile -Command "\
$akf = 'C:\\ProgramData\\ssh\\administrators_authorized_keys'; \
Set-Content -Path $akf -Value '{pub_key}'; \
icacls $akf /inheritance:r /grant 'SYSTEM:(R)' \
/grant 'BUILTIN\\Administrators:(R)'"</CommandLine>
              <Description>Configure SSH pubkey</Description>
            </SynchronousCommand>

            <!-- 4. Set PowerShell as default SSH shell -->
            <SynchronousCommand wcm:action="add">
              <Order>4</Order>
              <CommandLine>powershell -NoProfile -Command "\
New-ItemProperty -Path \
'HKLM:\\SOFTWARE\\OpenSSH' \
-Name DefaultShell \
-Value 'C:\\Windows\\System32\\WindowsPowerShell\\\
v1.0\\powershell.exe' -PropertyType String -Force"</CommandLine>
              <Description>Set default SSH shell</Description>
            </SynchronousCommand>

            <!-- 5. Disable Defender realtime scan -->
            <SynchronousCommand wcm:action="add">
              <Order>5</Order>
              <CommandLine>powershell -NoProfile -Command "\
Set-MpPreference \
-DisableRealtimeMonitoring $true"</CommandLine>
              <Description>Disable Defender realtime</Description>
            </SynchronousCommand>

            <!-- 6. Disable sleep/hibernate -->
            <SynchronousCommand wcm:action="add">
              <Order>6</Order>
              <CommandLine>powershell -NoProfile -Command "\
powercfg /change standby-timeout-ac 0; \
powercfg /change hibernate-timeout-ac 0; \
powercfg /hibernate off"</CommandLine>
              <Description>Disable sleep</Description>
            </SynchronousCommand>

            <!-- 7. Write completion marker -->
            <SynchronousCommand wcm:action="add">
              <Order>7</Order>
              <CommandLine>powershell -NoProfile -Command "\
New-Item -Path 'C:\\setup-complete.marker' \
-ItemType File -Force"</CommandLine>
              <Description>Write completion marker</Description>
            </SynchronousCommand>
          </FirstLogonCommands>
        </component>
      </settings>
    </unattend>
  """)


def generate_autounattend_iso():
  """Create an ISO containing autounattend.xml."""
  if AUTOUNATTEND_ISO.exists():
    print(f"[ok] Autounattend ISO exists: {AUTOUNATTEND_ISO}")
    return

  print("[..] Generating autounattend ISO...")
  xml_content = _generate_autounattend_xml()

  # Write XML to a temp dir and create ISO.
  import tempfile
  with tempfile.TemporaryDirectory() as tmpdir:
    xml_path = Path(tmpdir) / "autounattend.xml"
    xml_path.write_text(xml_content)
    run([
      "genisoimage",
      "-output", str(AUTOUNATTEND_ISO),
      "-volid", "OEMDRV",
      "-joliet", "-rock",
      str(xml_path),
    ])
  print(f"[ok] Autounattend ISO created: {AUTOUNATTEND_ISO}")


def create_vm():
  """Create the Windows 11 VM using virt-install."""
  if vm_exists(VM_NAME):
    print(f"[ok] VM '{VM_NAME}' already exists")
    return

  if not WIN_ISO.exists():
    print(f"Error: Windows ISO not found: {WIN_ISO}")
    sys.exit(1)

  print(f"[..] Creating VM '{VM_NAME}'...")

  run([
    "virt-install",
    "--name", VM_NAME,
    "--virt-type", "kvm",
    "--cpu", "host",
    "--vcpus", str(VM_VCPUS),
    "--memory", str(VM_RAM_MB),
    # Main disk: VirtIO for performance.
    "--disk",
    f"path={DISK_PATH},format=qcow2,bus=virtio,"
    f"cache=writeback",
    # CD-ROMs: Windows ISO, autounattend, VirtIO drivers.
    "--disk",
    f"path={WIN_ISO},device=cdrom,bus=sata",
    "--disk",
    f"path={AUTOUNATTEND_ISO},device=cdrom,bus=sata",
    "--disk",
    f"path={VIRTIO_ISO},device=cdrom,bus=sata",
    # UEFI with Secure Boot support + per-VM NVRAM.
    "--boot", "uefi",
    "--boot",
    "loader=/usr/share/OVMF/OVMF_CODE_4M.ms.fd,"
    "loader.readonly=yes,loader.type=pflash,"
    "nvram.template=/usr/share/OVMF/OVMF_VARS_4M.ms.fd,"
    "loader.secure=yes",
    # TPM 2.0 (swtpm emulator).
    "--tpm",
    "backend.type=emulator,"
    "backend.version=2.0,"
    "model=tpm-crb",
    # Network: VirtIO on targets network.
    "--network", f"network={NETWORK_NAME},model=virtio",
    # USB 3.0 controller for passthrough.
    "--controller", "usb,model=qemu-xhci",
    # SPICE for occasional GUI access via virt-viewer.
    "--graphics", "spice,listen=none",
    "--video", "qxl",
    "--os-variant", "win11",
    "--noautoconsole",
  ])
  print(f"[ok] VM '{VM_NAME}' created and booting")


def wait_for_ssh(timeout=900):
  """Wait for SSH to become reachable on the VM.

  Args:
    timeout: Maximum seconds to wait (default 900 = 15 min).

  Returns:
    True if SSH is reachable, False on timeout.
  """
  print(
    f"[..] Waiting for SSH on {VM_IP}"
    f" (up to {timeout}s, Windows install takes ~15 min)..."
  )
  deadline = time.time() + timeout
  while time.time() < deadline:
    result = subprocess.run(
      ["ssh",
       "-o", "ConnectTimeout=5",
       "-o", "StrictHostKeyChecking=accept-new",
       "-o", "BatchMode=yes",
       "-i", str(SSH_KEY_PATH),
       f"{VM_USER}@{VM_IP}",
       "powershell -NoProfile -Command echo ok"],
      capture_output=True, text=True,
    )
    if result.returncode == 0:
      print(f"[ok] SSH reachable on {VM_IP}")
      return True
    time.sleep(10)

  print(f"[warn] SSH not reachable after {timeout}s")
  print("  The VM may still be installing. Try manually:")
  print(f"  ssh -i {SSH_KEY_PATH} {VM_USER}@{VM_IP}")
  return False


def configure_ssh_config():
  """Add an SSH config entry for win-01."""
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


def update_targets_config():
  """Add win-01 to config/targets.yaml."""
  from lib.config import load_targets_config

  config_path = PROJECT_DIR / "config" / "targets.yaml"
  config = load_targets_config()
  targets = config.get("targets", {})

  if VM_NAME in targets:
    print(f"[ok] {VM_NAME} already in targets.yaml")
    return

  import yaml
  targets[VM_NAME] = {
    "type": "vm",
    "host": VM_IP,
    "user": VM_USER,
    "ssh_key": "~/.ssh/id_ed25519_targets",
    "os": "windows",
    "description": "Windows 11 Pro build VM (QEMU/KVM)",
  }
  config["targets"] = targets

  with open(config_path, "w") as f:
    yaml.dump(config, f, default_flow_style=False, sort_keys=False)

  # Fix ownership if running under sudo.
  user = os.environ.get("SUDO_USER")
  if user:
    run(["chown", f"{user}:{user}", str(config_path)])

  print(f"[ok] {VM_NAME} added to targets.yaml")


def print_summary(ssh_ok):
  """Print a summary of the setup."""
  print()
  print("=" * 60)
  print("  Windows 11 VM Setup Complete")
  print("=" * 60)
  print()
  print(f"  VM name:    {VM_NAME}")
  print(f"  IP address: {VM_IP}")
  print(f"  User:       {VM_USER}")
  print(f"  vCPUs:      {VM_VCPUS}")
  print(f"  RAM:        {VM_RAM_MB}MB")
  print(f"  Disk:       {VM_DISK_GB}GB ({DISK_PATH})")
  print(f"  SSH key:    {SSH_KEY_PATH}")
  print()
  if ssh_ok:
    print("  SSH:        connected")
  else:
    print(
      "  SSH:        not yet reachable"
      " (VM may still be installing)"
    )
  print()
  print("  Next step — provision the VM:")
  print(f"    python3 bin/provision_win_vm.py {VM_NAME}")
  print()


def main():
  """Run the full Windows VM setup."""
  if not is_root():
    print("Error: this script must be run as root (use sudo).")
    sys.exit(1)

  print(f"Setting up Windows 11 VM: {VM_NAME}")
  print(f"  IP:   {VM_IP}")
  print(f"  Disk: {IMAGES_DIR}")
  print()

  ensure_packages()
  create_storage_dir()
  download_virtio_iso()
  create_vm_disk()
  generate_autounattend_iso()
  create_vm()
  ssh_ok = wait_for_ssh()
  configure_ssh_config()
  update_targets_config()
  print_summary(ssh_ok)


if __name__ == "__main__":
  main()
