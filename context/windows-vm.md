# Windows 11 Build VM (win-01)

## Specs

| Property | Value |
|----------|-------|
| Name | win-01 |
| OS | Windows 11 Pro 23H2 |
| IP | 10.101.0.21 (static, `targets` network) |
| vCPUs | 8 |
| RAM | 8192 MB |
| Disk | 120 GB qcow2, VirtIO bus |
| Disk location | `/home/karl/libvirt/images/win-01.qcow2` |
| User | worker |
| SSH key | `~/.ssh/id_ed25519_targets` |
| UEFI | OVMF_CODE_4M.ms.fd (Secure Boot) |
| TPM | swtpm 2.0 (emulated, tpm-crb) |
| Network | VirtIO on `targets` bridge |
| USB | xHCI (USB 3.0) controller |
| Graphics | SPICE (listen=none), QXL video |

## Setup & Provisioning

```bash
# 1. Create VM and run unattended Windows install
sudo python3 bin/setup_win_vm.py

# 2. Provision (VS2022, Git, Samba)
python3 bin/provision_win_vm.py win-01
```

Setup script: `bin/setup_win_vm.py`
Provisioning script: `bin/provision_win_vm.py`

## Autounattend Reference

The autounattend.xml is generated as a Python f-string in
`bin/setup_win_vm.py:_generate_autounattend_xml`. It runs
three passes:

- **windowsPE**: GPT partitioning (EFI + MSR + Windows),
  loads VirtIO drivers from CD-ROM, selects Pro edition
- **specialize**: Sets hostname to `win-01`
- **oobeSystem**: Creates `worker` admin account, disables
  Windows Firewall, sets static IP, installs OpenSSH Server,
  writes SSH pubkey, sets PowerShell as default shell,
  disables Defender realtime and sleep/hibernate

The autounattend ISO uses volume label `OEMDRV` which
Windows auto-searches during setup.

## Installed Software

After provisioning (`bin/provision_win_vm.py`):

- **VS 2022 Build Tools** with VCTools workload + CMake
  component (`cl.exe`, `cmake`, `msbuild`)
- **Git for Windows** (`C:\Program Files\Git\cmd\git.exe`)
- **OpenSSH Server** (PowerShell as default shell)

VS environment (cl.exe, link.exe, etc.) is auto-loaded on
SSH login via PowerShell profile that sources `vcvars64.bat`.

## Samba Share

Host exports `/home/karl/dev` as `[dev]` share via Samba.
Guest maps it as `W:` drive (`\\10.101.0.1\dev`).

To update the Samba password:
```bash
# On host
sudo smbpasswd -a karl

# On guest (via SSH)
cmdkey /add:10.101.0.1 /user:karl /pass:newpassword
net use W: /delete
net use W: \\10.101.0.1\dev /persistent:yes
```

## USB Passthrough

The VM has an xHCI (USB 3.0) controller. To pass through
a specific USB device at runtime:

```bash
# Find the device
lsusb  # note vendor:product ID (e.g. 1234:5678)

# Attach to running VM
virsh attach-device win-01 --live <(cat <<EOF
<hostdev mode='subsystem' type='usb'>
  <source>
    <vendor id='0x1234'/>
    <product id='0x5678'/>
  </source>
</hostdev>
EOF
)

# Detach from VM
virsh detach-device win-01 --live <(cat <<EOF
<hostdev mode='subsystem' type='usb'>
  <source>
    <vendor id='0x1234'/>
    <product id='0x5678'/>
  </source>
</hostdev>
EOF
)
```

For persistent passthrough (survives reboot), add a
`<hostdev>` element to the VM's libvirt XML:
```bash
virsh edit win-01
```

## UEFI Boot Notes

The setup script handles these UEFI-specific quirks:

- **Boot order**: Uses `boot.order=1` on the CD-ROM and
  `boot.order=2` on the disk (UEFI ignores `<boot dev>`
  in `<os>`)
- **CD boot keypress**: UEFI shows "Press any key to boot
  from CD" — the script sends `virsh send-key KEY_ENTER`
  after a 3s delay
- **VirtIO drivers**: Only `viostor\w11\amd64` and
  `NetKVM\w11\amd64` paths (the `amd64\w11` top-level
  path contains duplicate viostor that causes 0x80070103)
- **AutoLogon count**: Set to 3 (profile setup consumes
  initial auto-logons before FirstLogonCommands run)
- **Parent dir permissions**: `libvirt-qemu` needs `o+x`
  on parent dirs to access images in `/home/karl/`

## Recovery

### VM won't boot
```bash
virsh start win-01
# Check console via SPICE
virt-viewer win-01
```

### SSH not working
```bash
# Check if VM is running
virsh dominfo win-01

# Check IP reachability
ping -c 1 10.101.0.21

# Check SSH with verbose output
ssh -v -i ~/.ssh/id_ed25519_targets worker@10.101.0.21

# If sshd stopped inside Windows, use SPICE console
virt-viewer win-01
# Then in Windows: Start-Service sshd
```

### Rebuild from scratch
```bash
virsh destroy win-01
virsh undefine win-01 --nvram --tpm
rm /home/karl/libvirt/images/win-01.qcow2
rm /home/karl/libvirt/images/win-01-autounattend.iso
sudo python3 bin/setup_win_vm.py
python3 bin/provision_win_vm.py win-01
```

### Snapshot / clone
```bash
# Shut down first
virsh shutdown win-01

# Create snapshot
virsh snapshot-create-as win-01 --name "clean-base" \
  --description "Post-provision base image"

# Revert to snapshot
virsh snapshot-revert win-01 clean-base
```
