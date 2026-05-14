// Server-side client for the Aegis backend API.
//
// Every function here runs on the Next.js server (server components / server
// actions), never in the browser — so the API key stays server-side and there
// is no CORS surface (the backend never sees a browser origin). The browser
// only ever talks to *this* Next app.

import type { TraceDetail, TraceListResponse } from "@/lib/types";

const BASE_URL = process.env.AEGIS_API_URL ?? "http://localhost:8000";

/** A non-2xx response from the backend, carrying its typed error code. */
export class BackendError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
    this.name = "BackendError";
  }
}

async function backendFetch(
  path: string,
  apiKey: string,
  init?: RequestInit,
): Promise<Response> {
  return fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { ...init?.headers, Authorization: `Bearer ${apiKey}` },
    // Traces are live data — never serve a cached page from a stale fetch.
    cache: "no-store",
  });
}

/** Parse `{ error: { code, message } }`; fall back if the body isn't JSON. */
async function toBackendError(res: Response): Promise<BackendError> {
  try {
    const body = (await res.json()) as { error?: { code?: string; message?: string } };
    return new BackendError(
      res.status,
      body.error?.code ?? "unknown",
      body.error?.message ?? res.statusText,
    );
  } catch {
    return new BackendError(res.status, "unknown", res.statusText);
  }
}

/** True if the key authenticates against the backend. Used by the login flow. */
export async function validateKey(apiKey: string): Promise<boolean> {
  try {
    const res = await backendFetch("/v1/traces?limit=1", apiKey);
    return res.ok;
  } catch {
    // Network error / backend down — treated as "can't validate", not "valid".
    return false;
  }
}

export type ListTracesParams = {
  limit?: number;
  offset?: number;
  model?: string;
};

/** `GET /v1/traces` — one page of the calling project's traces, newest first. */
export async function listTraces(
  apiKey: string,
  params: ListTracesParams = {},
): Promise<TraceListResponse> {
  const qs = new URLSearchParams();
  if (params.limit !== undefined) qs.set("limit", String(params.limit));
  if (params.offset !== undefined) qs.set("offset", String(params.offset));
  if (params.model) qs.set("model", params.model);

  const res = await backendFetch(`/v1/traces?${qs.toString()}`, apiKey);
  if (!res.ok) throw await toBackendError(res);
  return (await res.json()) as TraceListResponse;
}

/** `GET /v1/traces/{id}` — one fully-expanded trace, or null on 404. */
export async function getTrace(
  apiKey: string,
  traceId: string,
): Promise<TraceDetail | null> {
  const res = await backendFetch(
    `/v1/traces/${encodeURIComponent(traceId)}`,
    apiKey,
  );
  if (res.status === 404) return null;
  if (!res.ok) throw await toBackendError(res);
  return (await res.json()) as TraceDetail;
}
