"""Desktop notification wrapper using notify-send.

Fails silently if notify-send is not installed or if the
D-Bus call times out.
"""

import shutil
import subprocess


def notify(title, body, urgency="normal"):
  """Send a desktop notification via notify-send.

  Args:
    title: Notification title.
    body: Notification body text.
    urgency: One of 'low', 'normal', 'critical'.

  Returns:
    True if notification was sent, False otherwise.
  """
  if not shutil.which("notify-send"):
    return False
  try:
    subprocess.run(
      [
        "notify-send",
        "--urgency", urgency,
        title,
        body,
      ],
      timeout=5,
      capture_output=True,
    )
    return True
  except (subprocess.TimeoutExpired, OSError):
    return False
