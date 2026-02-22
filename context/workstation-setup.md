# Workstation Setup

End-to-end guide for bootstrapping takt on a
fresh Debian workstation. Every step is scripted — an agent
can run this top-to-bottom.

## Prerequisites

- Debian 12+ (or derivative) with sudo access
- Hardware virtualization enabled in BIOS (VT-x / AMD-V)
- Python 3.11+
- Git
- Internet access (downloads cloud images, packages)

Verify KVM support:
```bash
ls /dev/kvm  # must exist
```

## 1. Clone the orchestration repo

```bash
mkdir -p ~/dev
cd ~/dev
git clone <github-url> takt
```

## 2. Install Python dependencies

```bash
pip install -r ~/dev/takt/requirements.txt
```

Dependencies: `PyYAML>=6.0`, `textual>=1.0.0`,
`pyzmq>=26.0`, `claude-code-sdk>=0.1.0`.

For linting and testing (optional but recommended):
```bash
pip install flake8 cpplint pytest
```

## 3. Set up libvirt, networking, and Debian VM

The `setup_libvirt.py` script handles everything:

- Installs QEMU, libvirt, virtinst, OVMF, genisoimage
- Adds user to `libvirt` and `kvm` groups
- Starts `libvirtd`
- Creates `targets` NAT network (10.101.0.0/20,
  gateway 10.101.0.1)
- Enables IP forwarding
- Generates SSH key (`~/.ssh/id_ed25519_targets`)
- Downloads Debian 12 cloud image
- Creates the `deb-01` template VM (4 vCPU, 4GB RAM,
  40GB disk, static IP 10.101.0.20)
- Adds SSH config entry for `deb-01`

```bash
sudo python3 ~/dev/takt/bin/setup_libvirt.py
```

After running, log out and back in so `libvirt`/`kvm` group
membership takes effect.

## 4. Provision the Debian VM

Installs build tooling, editor configs, and sets default
shell to zsh.

```bash
python3 ~/dev/takt/bin/provision_vm.py deb-01
```

What it installs on the VM:
- APT: zsh, neovim, build-essential, cmake, clang,
  clang-format, clang-tidy, python3, python3-pip,
  python3-venv, rsync, curl, wget, locales
- PIP: pytest, cpplint, flake8
- Copies operator's zsh + oh-my-zsh config
- Copies nvim config + packer plugins
- Sets default shell to zsh

## 5. Set up the Windows VM (optional)

Only needed if you build Windows targets.

```bash
# 1. Create and install Windows 11 (unattended)
sudo python3 ~/dev/takt/bin/setup_win_vm.py

# 2. Provision (VS2022 Build Tools, Git, OpenSSH)
python3 ~/dev/takt/bin/provision_win_vm.py win-01
```

Details: `context/windows-vm.md`

## 6. Set up Samba share

VMs access source code via a Samba share of `~/dev`. This
avoids file transfer — VMs build directly from the share.

```bash
# Install Samba
sudo apt-get install -y samba

# Add share config
sudo tee -a /etc/samba/smb.conf > /dev/null <<'EOF'

[dev]
  path = /home/%U/dev
  browseable = yes
  read only = no
  valid users = %U
  create mask = 0644
  directory mask = 0755
EOF

# Set Samba password (use your login password or a
# dedicated one — VMs will use this to mount)
sudo smbpasswd -a $(whoami)

# Restart Samba
sudo systemctl restart smbd
```

### Mount on Debian VMs

SSH into the VM and mount:
```bash
sudo apt-get install -y cifs-utils
sudo mkdir -p /mnt/dev

# Test mount
sudo mount -t cifs //10.101.0.1/dev /mnt/dev \
  -o user=karl,uid=$(id -u),gid=$(id -g)

# Persistent mount (add to /etc/fstab)
echo '//10.101.0.1/dev /mnt/dev cifs' \
  'user=karl,pass=<password>,uid=1000,gid=1000 0 0' \
  | sudo tee -a /etc/fstab
```

### Mount on Windows VMs

The Windows provisioning script maps `W:` drive
automatically. To do it manually:
```powershell
cmdkey /add:10.101.0.1 /user:karl /pass:<password>
net use W: \\10.101.0.1\dev /persistent:yes
```

## 7. Clone root repos from GitHub

Root repos are local mirrors that agents clone from. They
live at `~/dev/root/<repo>`.

```bash
mkdir -p ~/dev/root
cd ~/dev/root

# Clone each repo listed in config/repos.yaml.
# Use the `path` field as the directory name.
git clone git@github.com:<org>/config.git config
git clone git@github.com:<org>/Combatant.git Combatant
git clone git@github.com:<org>/Conveyor.git Conveyor
# ... etc for each repo in repos.yaml
```

The `path` field in `config/repos.yaml` determines the
directory name under `~/dev/root/`. Some repos use a `.git`
suffix in the path (e.g. `mpl-wgpu.git`) — clone to match:
```bash
git clone git@github.com:<org>/mpl-wgpu.git mpl-wgpu.git
```

## 8. Create VM clones for work

Template VMs are read-only base images. Create clones for
actual use:

```bash
# Debian clone
sudo python3 ~/dev/takt/bin/clone_vm.py \
  create deb-01 deb-02 --ip 10.101.0.100

# Windows clone (if win-01 exists)
sudo python3 ~/dev/takt/bin/clone_vm.py \
  create win-01 win-02 --ip 10.101.0.101
```

Clone IPs start at `10.101.0.100+`. Details:
`context/vm-templates.md`

## 9. Install takt-service

The background service handles pipeline watching and agent
execution. Output persists across TUI disconnects.

```bash
mkdir -p ~/.config/systemd/user
cp ~/dev/takt/config/takt-service.service \
  ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable takt-service
systemctl --user start takt-service
```

Verify:
```bash
systemctl --user status takt-service
journalctl --user -u takt-service -f
```

## 10. Verify the setup

```bash
# Check targets
~/dev/takt/bin/target.py list
~/dev/takt/bin/target.py status deb-02

# Create a test workspace
~/dev/takt/bin/workspace.py create test-setup \
  config

# Verify workspace
~/dev/takt/bin/workspace.py status test-setup

# Clean up
~/dev/takt/bin/workspace.py delete test-setup -f

# Run tests
python3 -m pytest ~/dev/takt/tests -v

# Launch TUI (auto-connects to takt-service)
~/dev/takt/bin/takt.py
```

## Directory structure (after setup)

```
~/dev/
  takt/    This repo (tools, config, templates)
  root/                   Local mirrors of GitHub repos
    config/
    Combatant/
    Conveyor/
    ...
  workspaces/             Created by workspace.py
  stages/                 Created by workspace.py stage-add
```

## Network layout

| IP | Host |
|----|------|
| 10.101.0.1 | This workstation (gateway) |
| 10.101.0.20 | deb-01 (Debian template) |
| 10.101.0.21 | win-01 (Windows template) |
| 10.101.0.100+ | VM clones |

Network: `targets` (NAT, 10.101.0.0/20).
DHCP range: 10.101.8.1 — 10.101.15.254 (not used by
targets; they use static IPs).

## Disk layout

| Path | Contents |
|------|----------|
| `/var/lib/libvirt/images/` | deb-01 template disk + cloud-init ISO |
| `~/libvirt/images/` | win-01 template disk, clone disks, ISOs |

The home directory needs `o+x` permission so `libvirt-qemu`
can traverse into `~/libvirt/images/`:
```bash
chmod o+x ~/
```

## Adapting for a different machine

Things to change if the username or paths differ:

- `config/targets.yaml` — `ssh_key` paths, `disk` paths
- `config/repos.yaml` — repo paths if using different names
- Samba share config — username, `path` directive
- Windows VM Samba credentials (`cmdkey` commands)
- `~/libvirt/images/` — create directory, set permissions:
  ```bash
  sudo mkdir -p ~/libvirt/images
  sudo chown libvirt-qemu:kvm ~/libvirt/images
  chmod o+x ~/
  ```

## Related files

- `bin/setup_libvirt.py` — libvirt + Debian VM setup
- `bin/provision_vm.py` — Debian VM provisioning
- `bin/setup_win_vm.py` — Windows VM setup
- `bin/provision_win_vm.py` — Windows VM provisioning
- `bin/clone_vm.py` — VM cloning from templates
- `config/targets.yaml` — target inventory
- `config/repos.yaml` — repo registry
- `context/vm-templates.md` — VM template/clone details
- `context/windows-vm.md` — Windows VM details
