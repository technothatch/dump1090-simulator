"""Synthetic ADS-B traffic for the dump1090 / readsb ecosystem.

A kinematically plausible fleet of aircraft, served the way a real decoder
serves traffic: ``/data/aircraft.json``, ``/data/receiver.json``,
``/data/stats.json`` over HTTP, Beast binary frames (``0x1a``-escaped) over
TCP, and NDJSON snapshots on disk. Everything is standard library only and
fully deterministic for a given ``--seed``.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
