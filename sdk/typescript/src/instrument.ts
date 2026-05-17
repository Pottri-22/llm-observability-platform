// Instrumentation — monkey-patches an OpenAI-compatible client so every call
// emits a TraceEvent to a sink, with zero changes to the caller's call sites.
//
// TypeScript port of the Python SDK's instrument.py. The reliability flags
// (auto_usage, tool_calls handling, model normalization, deinstrument) match
// the Python equivalent; the sync-vs-async wrapper split is gone because in
// JS/TS `create` is always async — one wrapper covers everything.

import type { ChatMessage, TraceMetadata } from "./trace.js";
import { TraceEvent } from "./trace.js";

/** What the customer passes us — anything that quacks like an OpenAI client.
 *  Structurally typed so users don't need a runtime instanceof check, and so
 *  fakes work in tests without importing the real `openai` package. */
export interface OpenAILike {
  baseURL?: string | URL;
  chat: {
    completions: {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      create: (params: any) => Promise<any>;
    };
  };
}

export type Sink = (event: TraceEvent) => void;

export interface InstrumentOpts {
  sink: Sink;
  /** Override provider inference from baseURL. Drives model-id normalization
   *  for correct server-side cost lookup. */
  provider?: string;
  /** Inject `stream_options.include_usage = true` on streaming calls so the
   *  trace gets token counts. Default true. */
  autoUsage?: boolean;
}

// Symbols on the wrapped function so a second instrument() call is a no-op,
// and deinstrument() can recover the pre-patch method.
const SENTINEL = Symbol.for("aegis.instrumented");
const ORIGINAL = Symbol.for("aegis.original");

interface InstrumentedFn {
  [SENTINEL]?: true;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  [ORIGINAL]?: (params: any) => Promise<any>;
}

// Providers whose models the backend cost catalog keys with a `provider/`
// prefix. OpenAI and Anthropic models are keyed bare; only Groq triggers
// prefixing today (matches cost.py).
const PREFIXED_PROVIDERS = new Set(["groq"]);


// ---------------------------------------------------------------------------
// Provider + model helpers — pure, unit-tested
// ---------------------------------------------------------------------------

function baseUrlOf(client: OpenAILike): string | null {
  try {
    const value = client.baseURL;
    return value ? String(value) : null;
  } catch {
    return null;
  }
}

export function resolveProvider(
  provider: string | undefined,
  baseUrl: string | null,
): string | null {
  if (provider) return provider;
  if (!baseUrl) return null;
  if (baseUrl.includes("groq.com")) return "groq";
  if (baseUrl.includes("anthropic.com")) return "anthropic";
  if (baseUrl.includes("openai.com")) return "openai";
  return null;
}

export function normalizeModel(model: string, provider: string | null): string {
  if (!model || model.includes("/")) return model;
  if (provider && PREFIXED_PROVIDERS.has(provider)) return `${provider}/${model}`;
  return model;
}


// ---------------------------------------------------------------------------
// Response extraction — pulls the right "completion" string out of an OpenAI
// chat completion. Handles the tool-call case: when `message.content` is null
// (the model called a tool instead of writing text), serialize the tool calls
// into the completion field so the trace has something to display.
// ---------------------------------------------------------------------------

interface ToolCallLike {
  id?: string;
  type?: string;
  function?: { name?: string; arguments?: string };
}

interface ChoiceLike {
  message?: { content?: string | null; tool_calls?: ToolCallLike[] };
}

interface ResponseLike {
  choices?: ChoiceLike[];
  // `usage` is sometimes `null` (partial streaming responses without
  // include_usage), sometimes a partial object, sometimes absent entirely.
  usage?: { prompt_tokens?: number; completion_tokens?: number } | null;
}

export function extractCompletion(response: ResponseLike): string {
  try {
    const message = response.choices?.[0]?.message;
    if (!message) return "";
    if (message.content) return message.content;
    const toolCalls = message.tool_calls;
    if (Array.isArray(toolCalls) && toolCalls.length > 0) {
      return JSON.stringify(
        toolCalls.map((tc) => ({
          id: tc.id ?? null,
          type: tc.type ?? "function",
          name: tc.function?.name ?? null,
          arguments: tc.function?.arguments ?? null,
        })),
      );
    }
  } catch {
    /* fall through */
  }
  return "";
}


// ---------------------------------------------------------------------------
// Stream pass-through wrapper. Async generator that yields every chunk on
// through to the caller while accumulating trace state on the side. The
// `finally` fires `onDone` even on early break or mid-stream error.
// ---------------------------------------------------------------------------

interface StreamChunkLike {
  choices?: Array<{ delta?: { content?: string | null } }>;
  usage?: { prompt_tokens?: number; completion_tokens?: number };
}

type OnDone = (
  text: string,
  tokensIn: number | null,
  tokensOut: number | null,
  error: string | null,
) => void;

async function* wrapStream<T extends StreamChunkLike>(
  stream: AsyncIterable<T>,
  onDone: OnDone,
): AsyncGenerator<T, void, undefined> {
  const parts: string[] = [];
  let tokensIn: number | null = null;
  let tokensOut: number | null = null;
  let error: string | null = null;
  try {
    for await (const chunk of stream) {
      const delta = chunk.choices?.[0]?.delta;
      if (delta?.content) {
        parts.push(delta.content);
      }
      const usage = chunk.usage;
      if (usage) {
        tokensIn = usage.prompt_tokens ?? null;
        tokensOut = usage.completion_tokens ?? null;
      }
      yield chunk;
    }
  } catch (e) {
    error = String(e);
    throw e;
  } finally {
    onDone(parts.join(""), tokensIn, tokensOut, error);
  }
}


// ---------------------------------------------------------------------------
// instrument / deinstrument
// ---------------------------------------------------------------------------

export function instrument<T extends OpenAILike>(client: T, opts: InstrumentOpts): T {
  const completions = client.chat.completions;
  const original = completions.create as InstrumentedFn & typeof completions.create;
  if (original[SENTINEL]) {
    return client; // already wrapped — idempotent
  }

  const sink = opts.sink;
  const autoUsage = opts.autoUsage ?? true;
  const resolvedProvider = resolveProvider(opts.provider, baseUrlOf(client));

  /** Send to the sink; never let a broken sink break the caller's call. */
  const safeSink = (event: TraceEvent): void => {
    try {
      sink(event);
    } catch {
      // The instrumentation must never propagate sink errors back to the
      // user's app. Logging happens at the facade layer (TS-SDK-C).
    }
  };

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const wrapped = async function (this: unknown, params: any): Promise<any> {
    // Pull our private metadata kwarg before the request leaves us. If a user
    // sets `aegis_metadata`, we want it on the trace but not on the wire to
    // the provider — OpenAI would 400 on the unknown field.
    const aegisMetadata: TraceMetadata | undefined = params?.aegis_metadata;
    const cleanParams = { ...(params ?? {}) };
    delete cleanParams.aegis_metadata;

    const messages: ChatMessage[] = Array.isArray(cleanParams.messages)
      ? cleanParams.messages
      : [];
    const rawModel: string = cleanParams.model ?? "";
    const normalizedModel = normalizeModel(rawModel, resolvedProvider);
    const streamed = Boolean(cleanParams.stream);

    if (streamed && autoUsage) {
      // setdefault-style merge: never clobber a user-supplied value.
      const existing = cleanParams.stream_options ?? {};
      cleanParams.stream_options = {
        ...existing,
        include_usage: existing.include_usage ?? true,
      };
    }

    const t0 = performance.now();
    let result;
    try {
      result = await original.call(this, cleanParams);
    } catch (e) {
      safeSink(
        new TraceEvent({
          model: normalizedModel,
          messages,
          completion: "",
          tokensIn: null,
          tokensOut: null,
          latencyMs: performance.now() - t0,
          status: "error",
          streamed,
          provider: resolvedProvider,
          error: String(e),
          userMetadata: aegisMetadata,
        }),
      );
      throw e;
    }

    if (streamed) {
      // Wrap the stream as a yields-through async generator. Caller iterates
      // it exactly as they would the raw OpenAI Stream — chunks pass on
      // unchanged; the trace fires when the iterator exhausts (or breaks).
      return wrapStream(result, (text, tokensIn, tokensOut, error) => {
        safeSink(
          new TraceEvent({
            model: normalizedModel,
            messages,
            completion: text,
            tokensIn,
            tokensOut,
            latencyMs: performance.now() - t0,
            status: error === null ? "ok" : "error",
            streamed: true,
            provider: resolvedProvider,
            error,
            userMetadata: aegisMetadata,
          }),
        );
      });
    }

    // Non-streaming.
    const usage = (result as ResponseLike).usage;
    safeSink(
      new TraceEvent({
        model: normalizedModel,
        messages,
        completion: extractCompletion(result as ResponseLike),
        tokensIn: usage?.prompt_tokens ?? null,
        tokensOut: usage?.completion_tokens ?? null,
        latencyMs: performance.now() - t0,
        status: "ok",
        streamed: false,
        provider: resolvedProvider,
        userMetadata: aegisMetadata,
      }),
    );
    return result;
  } as InstrumentedFn & typeof completions.create;

  wrapped[SENTINEL] = true;
  wrapped[ORIGINAL] = original;
  completions.create = wrapped;
  return client;
}

export function deinstrument(client: OpenAILike): boolean {
  const completions = client.chat?.completions;
  const current = completions?.create as InstrumentedFn | undefined;
  const original = current?.[ORIGINAL];
  if (!original) return false;
  completions.create = original;
  return true;
}
