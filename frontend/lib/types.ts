// Wire types — mirror the backend's Pydantic response schemas
// (backend/app/schemas/trace.py + common.py). Kept in one file so a backend
// schema change has exactly one place to follow on the frontend.

/** One row of the trace list — backend `TraceListItem`. Full prompt/completion
 *  are omitted from the list payload; fetch them via the detail endpoint. */
export type TraceListItem = {
  trace_id: string;
  ts: string; // ISO-8601
  model: string;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  latency_ms: number;
  prompt_preview: string;
};

/** Pagination envelope — backend `PaginatedMeta`. */
export type PaginatedMeta = {
  total: number;
  limit: number;
  offset: number;
};

/** Response of `GET /v1/traces` — backend `TraceListResponse`. */
export type TraceListResponse = {
  traces: TraceListItem[];
  meta: PaginatedMeta;
};

/** Response of `GET /v1/traces/{id}` — backend `TraceDetail`. */
export type TraceDetail = {
  trace_id: string;
  org_id: string;
  project_id: string;
  ts: string;
  model: string;
  prompt: string; // JSON-serialized messages list (the SDK writes it this way)
  completion: string;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  latency_ms: number;
  metadata: Record<string, unknown>;
  inserted_at: string;
};

/** Error envelope — backend `ErrorResponse` (`{ error: { code, message } }`). */
export type ApiError = {
  error: { code: string; message: string };
};
