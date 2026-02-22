# VM Templates & Clones

## Overview

deb-01 and win-01 are fully provisioned base images marked
as templates (`template: true` in `config/targets.yaml`).
They are never used directly — agents work on clones.

Clones use qcow2 backing files: they're fast to create and
space-efficient since they only store diffs from the template.

## Template targets

| Name   | OS         | IP          | Disk location                          |
|--------|------------|-------------|----------------------------------------|
| deb-01 | Debian 12  | 10.101.0.20 | /var/lib/libvirt/images/deb-01.qcow2   |
| win-01 | Windows 11 | 10.101.0.21 | ~/libvirt/images/win-01.qcow2  |

Templates have `template: true` and `disk:` fields in
`config/targets.yaml`. Template guards in `bin/target.py`
refuse `claim`, `up`, and `run` on templates.

## Creating clones

```bash
sudo python3 bin/clone_vm.py create <template> <name> --ip <ip>
```

Steps performed:
1. Validate template exists and has `template: true`
2. Shut down template VM if running
3. Set template disk to read-only (chmod 0444)
4. Create clone disk with qcow2 backing file
5. Clone the libvirt domain (virt-clone --preserve-data)
6. OS-specific reconfiguration:
   - **Debian**: offline via virt-customize (hostname, IP,
     SSH host keys)
   - **Windows**: boot with template IP, SSH in, run
     PowerShell to set hostname + IP, then restart
7. Register clone in `config/targets.yaml`
8. Start clone and verify SSH connectivity

## Deleting clones

```bash
sudo python3 bin/clone_vm.py delete <name>
```

Steps: shut down VM, undefine domain (--nvram --tpm for
Windows), delete disk, remove from targets.yaml.

## Disk layout

All clone disks go in `~/libvirt/images/`.
The deb-01 template disk stays in `/var/lib/libvirt/images/`
(backing file reference uses absolute path).

## IP allocation

| Range           | Use            |
|-----------------|----------------|
| 10.101.0.20-21  | Templates      |
| 10.101.0.100+   | Clones         |

## Dependencies

- `qemu-img` — creates backing file disks
- `virt-clone` — from `virtinst` package
- `virt-customize` — from `libguestfs-tools` (Debian offline
  reconfiguration only)

## Related files

- `bin/clone_vm.py` — create/delete CLI
- `bin/target.py` — template guards
- `lib/target_ops.py:is_template()` — template check helper
- `lib/config.py:save_targets_config()` — write targets.yaml
- `config/targets.yaml` — target registry
- `context/windows-vm.md` — Windows VM provisioning details
