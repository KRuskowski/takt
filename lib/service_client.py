"""ZMQ client for connecting the TUI to takt-service.

Uses DEALER for request/reply commands and SUB for
receiving broadcast events (agent updates, output,
pipeline events).
"""

import asyncio
import json
import logging

import zmq
import zmq.asyncio

from lib.service import DEFAULT_CMD_ADDR, DEFAULT_PUB_ADDR

log = logging.getLogger("takt.client")


class ServiceClient:
  """ZMQ client for takt-service IPC.

  Attributes:
    cmd_addr: DEALER connect address.
    pub_addr: SUB connect address.
    connected: Whether the client is connected.
  """

  def __init__(self, cmd_addr=None, pub_addr=None,
               zmq_ctx=None):
    """Initialize the client.

    Args:
      cmd_addr: DEALER connect address.
      pub_addr: SUB connect address.
      zmq_ctx: Optional ZMQ context (for testing).
    """
    self.cmd_addr = cmd_addr or DEFAULT_CMD_ADDR
    self.pub_addr = pub_addr or DEFAULT_PUB_ADDR
    self._ctx = zmq_ctx or zmq.asyncio.Context()
    self._own_ctx = zmq_ctx is None
    self._dealer = None
    self._sub = None
    self._handlers = {}  # topic -> [callback]
    self._poller_task = None
    self.connected = False

  async def connect(self):
    """Connect DEALER and SUB sockets to the service."""
    self._dealer = self._ctx.socket(zmq.DEALER)
    self._dealer.connect(self.cmd_addr)
    self._sub = self._ctx.socket(zmq.SUB)
    self._sub.connect(self.pub_addr)
    self.connected = True
    self._poller_task = asyncio.create_task(
      self._poll_sub()
    )
    log.info("Connected to takt-service")

  async def disconnect(self):
    """Close sockets and clean up."""
    self.connected = False
    if self._poller_task:
      self._poller_task.cancel()
      try:
        await self._poller_task
      except asyncio.CancelledError:
        pass
    if self._dealer:
      self._dealer.close(linger=0)
    if self._sub:
      self._sub.close(linger=0)
    if self._own_ctx:
      self._ctx.term()
    log.info("Disconnected from takt-service")

  async def send_cmd(self, cmd, **kwargs):
    """Send a command and wait for the reply.

    Args:
      cmd: Command name string.
      **kwargs: Additional command parameters.

    Returns:
      Reply dict with 'status' and 'data' or 'message'.

    Raises:
      ConnectionError: If not connected.
      TimeoutError: If no reply within 10 seconds.
    """
    if not self.connected or self._dealer is None:
      raise ConnectionError("Not connected to service")
    payload = {"cmd": cmd}
    payload.update(kwargs)
    await self._dealer.send_multipart([
      b"", json.dumps(payload).encode()
    ])
    try:
      frames = await asyncio.wait_for(
        self._dealer.recv_multipart(), timeout=10
      )
    except asyncio.TimeoutError:
      raise TimeoutError(
        f"No reply for command: {cmd}"
      )
    # DEALER receives [empty, payload].
    return json.loads(frames[1])

  def subscribe(self, topic):
    """Subscribe to a PUB topic.

    Args:
      topic: Topic string to subscribe to.
    """
    if self._sub:
      self._sub.subscribe(topic.encode())

  def unsubscribe(self, topic):
    """Unsubscribe from a PUB topic.

    Args:
      topic: Topic string to unsubscribe from.
    """
    if self._sub:
      self._sub.unsubscribe(topic.encode())

  def on(self, topic, handler):
    """Register a handler for a PUB topic.

    Handlers are called with (topic_str, data_dict) when
    a matching message arrives.

    Args:
      topic: Topic prefix string.
      handler: Callable(topic_str, data_dict).
    """
    self._handlers.setdefault(topic, []).append(handler)

  def off(self, topic, handler=None):
    """Unregister a handler for a PUB topic.

    Args:
      topic: Topic prefix string.
      handler: Specific handler to remove, or None to
        remove all handlers for the topic.
    """
    if handler is None:
      self._handlers.pop(topic, None)
    else:
      handlers = self._handlers.get(topic, [])
      if handler in handlers:
        handlers.remove(handler)

  async def _poll_sub(self):
    """Read SUB socket and dispatch to handlers."""
    while self.connected:
      try:
        frames = await self._sub.recv_multipart()
      except zmq.ZMQError:
        if not self.connected:
          break
        raise
      if len(frames) < 2:
        continue
      topic_str = frames[0].decode()
      try:
        data = json.loads(frames[1])
      except json.JSONDecodeError:
        continue
      # Match handlers by topic prefix.
      for prefix, handlers in self._handlers.items():
        if topic_str.startswith(prefix):
          for handler in handlers:
            try:
              handler(topic_str, data)
            except Exception:
              log.debug(
                "Handler for %s failed",
                topic_str,
                exc_info=True,
              )

  async def is_service_running(self):
    """Check if the service is responsive.

    Returns:
      True if the service replies to ping.
    """
    try:
      reply = await self.send_cmd("ping")
      return reply.get("status") == "ok"
    except (ConnectionError, TimeoutError):
      return False
