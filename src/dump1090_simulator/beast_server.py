"""TCP server that broadcasts Beast binary frames to connected consumers.

dump1090's ``--net`` Beast input (and tools like ``beast-relay``) connect to
a TCP port and read a byte stream of ``0x1a``-framed messages. This module
accepts any number of such consumers and fans each step's frames out to all
of them.

Backpressure policy: every client gets a bounded queue; if one consumer
falls behind, whole batches are dropped for that consumer only (never for
the others, never for the simulation) — the same behaviour a live radio
link shows when a decoder stalls.
"""

from __future__ import annotations

import queue
import socket
import threading
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["BeastBroadcaster"]

#: Per-client queue depth in whole publish batches (step outputs).
_QUEUE_DEPTH = 64

#: Accept-loop poll interval; bounds how long shutdown can take.
_ACCEPT_TIMEOUT_S = 0.5

#: Sentinel pushed to a client queue to tell its pump to exit.
_STOP: None = None


@dataclass(slots=True)
class _Client:
    """One connected consumer: its socket, queue and pump thread."""

    sock: socket.socket
    queue: queue.Queue[bytes | None]
    thread: threading.Thread


class BeastBroadcaster:
    """Serves Beast frames on a background accept thread.

    Args:
        host: Bind address (``0.0.0.0`` exposes the feed to the network).
        port: TCP port; ``0`` picks a free ephemeral port (tests rely on
            this via :attr:`port`). ``30005`` is the dump1090 convention.
    """

    def __init__(self, *, host: str = "127.0.0.1", port: int = 30005) -> None:
        self._lock = threading.Lock()
        self._closed = False
        self._clients: list[_Client] = []
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind((host, port))
        self._listener.listen()
        self._listener.settimeout(_ACCEPT_TIMEOUT_S)
        self._thread = threading.Thread(
            target=self._accept_loop,
            name="dump1090-simulator-beast",
            daemon=True,
        )

    @property
    def port(self) -> int:
        """The actual bound TCP port (resolved even when constructed with 0)."""
        return int(self._listener.getsockname()[1])

    @property
    def client_count(self) -> int:
        """Number of consumers currently connected."""
        with self._lock:
            return len(self._clients)

    def start(self) -> None:
        """Begin accepting Beast consumers in the background."""
        self._thread.start()

    def publish(self, frames: Sequence[bytes]) -> None:
        """Fan encoded frames out to every connected consumer.

        An empty batch wakes nobody; a slow consumer's full queue drops
        that batch for it without affecting the rest.
        """
        if not frames:
            return
        blob = b"".join(frames)
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.queue.put_nowait(blob)
            except queue.Full:
                continue

    def close(self) -> None:
        """Stop accepting, disconnect everyone and release the port."""
        with self._lock:
            self._closed = True
            clients = list(self._clients)
            self._clients.clear()
        self._listener.close()
        for client in clients:
            self._stop_client(client)
        if self._thread.is_alive():
            self._thread.join()
        for client in clients:
            client.thread.join()

    # -------------------------------------------------------------- internals

    @staticmethod
    def _stop_client(client: _Client) -> None:
        """Drain a client queue and send its pump the exit sentinel."""
        while True:
            try:
                client.queue.get_nowait()
            except queue.Empty:
                break
        client.queue.put_nowait(_STOP)

    def _accept_loop(self) -> None:
        """Accept consumers until the listener is closed."""
        while True:
            try:
                conn, _addr = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client = _Client(
                sock=conn,
                queue=queue.Queue(maxsize=_QUEUE_DEPTH),
                thread=threading.Thread(
                    target=self._pump,
                    args=(conn,),
                    daemon=True,
                    name="dump1090-simulator-beast-client",
                ),
            )
            with self._lock:
                if self._closed:
                    client.queue.put_nowait(_STOP)
                else:
                    self._clients.append(client)
            client.thread.start()

    def _pump(self, conn: socket.socket) -> None:
        """Write queued batches to one consumer until it goes away."""
        with self._lock:
            client = next((c for c in self._clients if c.sock is conn), None)
        try:
            if client is None:
                # Closed between accept and register: nothing to serve.
                return
            while True:
                batch = client.queue.get()
                if batch is _STOP:
                    return
                conn.sendall(batch)
        except OSError:
            return
        finally:
            self._unregister(conn)
            conn.close()

    def _unregister(self, conn: socket.socket) -> None:
        """Forget a consumer whose pump has finished."""
        with self._lock:
            self._clients[:] = [c for c in self._clients if c.sock is not conn]
