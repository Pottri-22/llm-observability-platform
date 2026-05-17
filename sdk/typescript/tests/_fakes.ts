// Minimal fake OpenAI-compatible clients for the TS SDK suite.
// Mirrors sdk/python/tests/_fakes.py — same shape, same use, TS idioms.

import type { OpenAILike } from "../src/instrument.js";

export interface FakeUsage {
  prompt_tokens: number;
  completion_tokens: number;
}

export interface FakeToolCall {
  id: string;
  type: "function";
  function: { name: string; arguments: string };
}

export interface FakeResponse {
  choices: Array<{
    message: { content: string | null; tool_calls?: FakeToolCall[] };
  }>;
  usage: FakeUsage | null;
}

export interface FakeStreamChunk {
  choices: Array<{ delta: { content?: string | null } }>;
  usage: FakeUsage | null;
}

export function makeResponse(
  content: string | null,
  usage: FakeUsage | null = null,
  toolCalls?: FakeToolCall[],
): FakeResponse {
  return {
    choices: [{ message: { content, tool_calls: toolCalls } }],
    usage,
  };
}

export function makeChunk(
  content: string | null = null,
  usage: FakeUsage | null = null,
  hasChoice = true,
): FakeStreamChunk {
  return {
    choices: hasChoice ? [{ delta: { content } }] : [],
    usage,
  };
}

/** A fake OpenAI client. `result` is whatever `.chat.completions.create`
 *  should produce: a FakeResponse, an array of FakeStreamChunk, or an Error
 *  to throw. `receivedParams` records what the wrapper forwarded. */
export class FakeClient implements OpenAILike {
  readonly baseURL: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  readonly receivedParams: any[] = [];
  readonly chat: OpenAILike["chat"];

  constructor(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    private readonly result: FakeResponse | FakeStreamChunk[] | Error | any,
    baseURL: string = "https://api.openai.com/v1",
  ) {
    this.baseURL = baseURL;
    this.chat = {
      completions: {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        create: async (params: any) => {
          this.receivedParams.push(params);
          if (this.result instanceof Error) {
            throw this.result;
          }
          if (Array.isArray(this.result)) {
            // Streaming: return an async iterable of the chunks.
            const chunks = this.result;
            return (async function* () {
              for (const chunk of chunks) yield chunk;
            })();
          }
          return this.result;
        },
      },
    };
  }
}
