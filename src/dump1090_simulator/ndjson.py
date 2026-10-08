"""The NDJSON recording format shared with dump1090-replay and friends.

Every line is one JSON object::

    {"t": <epoch seconds>, "aircraft": [ ... ]}

``t`` is the decoder's ``now`` at capture time (the simulation clock here,
so a seed replays exactly), and ``aircraft`` holds the raw ``aircraft``
array from ``aircraft.json`` — all fields preserved verbatim, including
``seen`` / ``seen_pos`` (seconds relative to ``t``) and ``alt_baro`` which
may be the string ``"ground"``.

Writers only ever emit ``t`` and ``aircraft``; that is the convention the
ecosystem's readers consume.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

__all__ = ["NdjsonRecorder", "format_line"]


def format_line(t: float, aircraft: Sequence[dict[str, Any]]) -> str:
    """Serialise one snapshot as a single NDJSON line (without newline)."""
    payload: dict[str, Any] = {"t": t, "aircraft": list(aircraft)}
    return json.dumps(payload, ensure_ascii=False, separators=(", ", ": "))


class NdjsonRecorder:
    """Appends one snapshot line per simulation step to a file.

    The file is truncated on open (a new recording, not a log to extend)
    and flushed after every line so a recording survives a crash. Use as a
    context manager, or call :meth:`close`.

    Args:
        path: Destination file; parent directories must already exist.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._handle: TextIO = self._path.open("w", encoding="utf-8", newline="\n")
        self._closed = False

    @property
    def path(self) -> Path:
        """The file being written."""
        return self._path

    def record(self, t: float, aircraft: Sequence[dict[str, Any]]) -> None:
        """Append one snapshot and flush it to disk.

        Raises:
            ValueError: The recorder is already closed.
        """
        if self._closed:
            raise ValueError(f"recorder for {self._path} is closed")
        self._handle.write(format_line(t, aircraft) + "\n")
        self._handle.flush()

    def close(self) -> None:
        """Close the file (idempotent)."""
        if not self._closed:
            self._closed = True
            self._handle.close()

    def __enter__(self) -> NdjsonRecorder:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
