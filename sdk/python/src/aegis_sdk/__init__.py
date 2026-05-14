"""aegis-sdk — drop-in tracing for LLM apps.

    from aegis_sdk import Aegis
    aegis = Aegis(api_key="aegis_live_xxx", project="my-app")
    aegis.instrument(openai_client)

`Aegis` is the facade most apps use. The lower-level primitives (`instrument`,
`RingBuffer`, `Transport`, …) are exported too for advanced wiring and testing.
"""

from __future__ import annotations

from aegis_sdk._version import __version__
from aegis_sdk.buffer import RingBuffer
from aegis_sdk.circuit import CircuitBreaker
from aegis_sdk.client import Aegis
from aegis_sdk.instrument import deinstrument, instrument
from aegis_sdk.trace import TraceEvent
from aegis_sdk.transport import Transport

__all__ = [
    "Aegis",
    "CircuitBreaker",
    "RingBuffer",
    "TraceEvent",
    "Transport",
    "__version__",
    "deinstrument",
    "instrument",
]
