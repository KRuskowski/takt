"""SSH operations via subprocess."""

import subprocess


class SSHError(Exception):
  """Raised when an SSH command fails."""

  def __init__(self, host, returncode, stderr):
    self.host = host
    self.returncode = returncode
    self.stderr = stderr
    super().__init__(
      f"SSH to {host} failed (rc={returncode}): {stderr}"
    )


def run_ssh(
  host, command, user=None, port=None, key=None, timeout=30,
):
  """Run a command on a remote host via SSH.

  Args:
    host: Hostname or IP address.
    command: Command string to execute remotely.
    user: SSH user (optional).
    port: SSH port (optional).
    key: Path to SSH private key (optional).
    timeout: Connection timeout in seconds.

  Returns:
    stdout as a stripped string.
  """
  ssh_args = [
    "ssh",
    "-o", "ConnectTimeout=" + str(timeout),
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "BatchMode=yes",
  ]
  if port:
    ssh_args += ["-p", str(port)]
  if key:
    ssh_args += ["-i", str(key)]
  target = f"{user}@{host}" if user else host
  ssh_args.append(target)
  ssh_args.append(command)

  result = subprocess.run(
    ssh_args, capture_output=True, text=True,
  )
  if result.returncode != 0:
    raise SSHError(host, result.returncode, result.stderr.strip())
  return result.stdout.strip()


def check_connectivity(
  host, user=None, port=None, key=None, timeout=5,
):
  """Check if a host is reachable via SSH.

  Args:
    host: Hostname or IP address.
    user: SSH user (optional).
    port: SSH port (optional).
    key: Path to SSH private key (optional).
    timeout: Connection timeout in seconds.

  Returns:
    True if reachable, False otherwise.
  """
  try:
    run_ssh(
      host, "echo ok",
      user=user, port=port, key=key, timeout=timeout,
    )
    return True
  except SSHError:
    return False
