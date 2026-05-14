"""aegis-sdk — drop-in tracing for LLM apps.

Public surface is filled in by SDK-C (the `Aegis` facade + `instrument`). For now this
exposes the reliability-spine primitives so SDK-B and the test suite can import them.
"""

from __future__ import annotations

from aegis_sdk._version import __version__
from aegis_sdk.buffer import RingBuffer
from aegis_sdk.circuit import CircuitBreaker
from aegis_sdk.instrument import deinstrument, instrument
from aegis_sdk.trace import TraceEvent
from aegis_sdk.transport import Transport

__all__ = [
    "CircuitBreaker",
    "RingBuffer",
    "TraceEvent",
    "Transport",
    "__version__",
    "deinstrument",
    "instrument",
]
