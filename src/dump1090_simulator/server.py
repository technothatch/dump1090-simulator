"""Threaded HTTP server exposing the three JSON documents.

Mirrors the endpoints a dump1090 / readsb web interface expects:

- ``GET /data/aircraft.json`` — live fleet snapshot;
- ``GET /data/receiver.json`` — receiver identity (static);
- ``GET /data/stats.json`` — five-bucket statistics.

Anything else is a 404. The server runs on a daemon thread; the control
thread keeps stepping the simulation, so documents are always produced
under the simulation lock and served as read-only snapshots.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

from dump1090_simulator import __version__
from dump1090_simulator.simulation import Simulation

__all__ = ["HttpServer"]

_JSON_HEADERS = {"Content-Type": "application/json"}


def _dumps(doc: dict[str, Any]) -> bytes:
    """Serialize a document the same way recordings do (stable spacing)."""
    return json.dumps(doc, ensure_ascii=False, separators=(", ", ": ")).encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    """Routes the three data paths to the simulation's documents."""

    server_version = f"dump1090-simulator/{__version__}"
    server: _HttpServer

    def do_GET(self) -> None:
        sim = self.server.sim
        if self.path == "/data/aircraft.json":
            payload = _dumps(sim.aircraft_doc())
        elif self.path == "/data/receiver.json":
            payload = _dumps(sim.receiver_doc())
        elif self.path == "/data/stats.json":
            payload = _dumps(sim.stats_doc())
        else:
            self.send_error(404, "Not Found")
            return
        self.send_response(200)
        for header, value in _JSON_HEADERS.items():
            self.send_header(header, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:
        """Stay quiet: request logging would pollute test output."""

    def log_error(self, *args: object) -> None:
        """Also silence error logs (broken pipes during teardown, etc.)."""


class _HttpServer(ThreadingHTTPServer):
    """HTTP server bound to an address, carrying the simulation."""

    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], sim: Simulation) -> None:
        self.sim = sim
        super().__init__(server_address, _Handler)


class HttpServer:
    """Serves one simulation's documents on a background daemon thread.

    Args:
        sim: The simulation whose documents are served.
        host: Bind address (``127.0.0.1`` by default; ``0.0.0.0`` exposes
            the feed to the network).
        port: TCP port; ``0`` picks a free ephemeral port (tests rely on
            this via :attr:`port`).
    """

    def __init__(self, sim: Simulation, *, host: str = "127.0.0.1", port: int = 8080) -> None:
        self._httpd = _HttpServer((host, port), sim)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="dump1090-simulator-http",
            daemon=True,
        )

    @property
    def port(self) -> int:
        """The actual bound TCP port (resolved even when constructed with 0)."""
        return int(self._httpd.server_address[1])

    @property
    def url(self) -> str:
        """Base URL clients can poll."""
        host = cast(str, self._httpd.server_address[0])
        port = int(self._httpd.server_address[1])
        return f"http://{host}:{port}"

    def start(self) -> None:
        """Begin accepting connections in the background."""
        self._thread.start()

    def close(self) -> None:
        """Stop accepting connections and release the port."""
        if self._thread.is_alive():
            self._httpd.shutdown()
            self._thread.join()
        self._httpd.server_close()
