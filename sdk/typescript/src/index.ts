// Public surface. The `Aegis` facade lands in TS-SDK-C; for now this exposes
// the reliability-spine primitives so TS-SDK-B and the test suite can import them.

export { VERSION } from "./version.js";
export { TraceEvent } from "./trace.js";
export type {
  ChatMessage,
  TraceEventInput,
  TraceMetadata,
  TraceWirePayload,
} from "./trace.js";
export { RingBuffer, DEFAULT_MAXLEN } from "./buffer.js";
export { CircuitBreaker } from "./circuit.js";
export type { CircuitBreakerOpts, CircuitState } from "./circuit.js";
export { Transport } from "./transport.js";
export type { FetchFn, SleepFn, TransportOpts } from "./transport.js";
export { instrument, deinstrument, normalizeModel, resolveProvider, extractCompletion } from "./instrument.js";
export type { OpenAILike, InstrumentOpts, Sink } from "./instrument.js";
export { Aegis } from "./client.js";
export type { AegisOpts, InstrumentArgs } from "./client.js";
