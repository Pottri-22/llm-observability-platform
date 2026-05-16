// Trace detail — Server Component. Fetches one fully-expanded trace, parses the
// SDK's JSON-serialized `messages` out of the `prompt` field, and renders the
// full conversation alongside the response, cost/latency summary, and the raw
// metadata blob.
//
// The route param is a Promise in Next 16 (`await params`). A trace_id that
// doesn't belong to the calling project is returned by the backend as 404 —
// `getTrace` translates that to `null`, and we call `notFound()`.

import Link from "next/link";
import { notFound } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { getTrace } from "@/lib/backend";
import {
  formatCost,
  formatLatency,
  formatRelativeTime,
  formatTimestamp,
} from "@/lib/format";
import { requireApiKey } from "@/lib/session";
import type { EvaluationRecord } from "@/lib/types";

type Message = { role: string; content: string };

/** The SDK serializes the messages list into `prompt`. Non-SDK callers (curl,
 *  k6 load tests) may send plain text instead — try JSON first, fall back to
 *  rendering the raw string. */
function parseMessages(prompt: string): Message[] | null {
  if (!prompt || !prompt.trimStart().startsWith("[")) return null;
  try {
    const parsed: unknown = JSON.parse(prompt);
    if (
      Array.isArray(parsed) &&
      parsed.every(
        (m): m is Message =>
          typeof m === "object" &&
          m !== null &&
          typeof (m as Message).role === "string",
      )
    ) {
      return parsed as Message[];
    }
  } catch {
    /* fall through */
  }
  return null;
}

export default async function TraceDetailPage({
  params,
}: {
  params: Promise<{ traceId: string }>;
}) {
  const apiKey = await requireApiKey();
  const { traceId } = await params;
  const trace = await getTrace(apiKey, traceId);
  if (!trace) notFound();

  const messages = parseMessages(trace.prompt);
  const aegisMeta = (trace.metadata.aegis ?? {}) as {
    status?: string;
    streamed?: boolean;
    provider?: string;
    error?: string;
  };
  const status = aegisMeta.status ?? "ok";

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-8 space-y-6">
      <div>
        <Link
          href="/traces"
          className={buttonVariants({ size: "sm", variant: "ghost" })}
        >
          ← Back to traces
        </Link>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight">
          Trace <span className="font-mono">{trace.trace_id.slice(0, 8)}</span>
        </h1>
        <p className="font-mono text-xs text-muted-foreground">{trace.trace_id}</p>
        <p className="text-sm text-muted-foreground">
          {formatTimestamp(trace.ts)}
        </p>
      </div>

      {/* Summary card — the at-a-glance facts every trace has. */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Summary</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
            <Field label="Model">
              <Badge variant="secondary" className="font-mono">{trace.model}</Badge>
            </Field>
            <Field label="Status">
              <Badge variant={status === "error" ? "destructive" : "secondary"}>
                {status}
              </Badge>
            </Field>
            <Field label="Tokens">
              <span className="tabular-nums">
                {trace.tokens_in} <span className="text-muted-foreground">in</span>{" "}
                · {trace.tokens_out}{" "}
                <span className="text-muted-foreground">out</span>
              </span>
            </Field>
            <Field label="Cost">
              <span className="tabular-nums">{formatCost(trace.cost_usd)}</span>
            </Field>
            <Field label="Latency">
              <span className="tabular-nums">{formatLatency(trace.latency_ms)}</span>
            </Field>
            <Field label="Streamed">{aegisMeta.streamed ? "yes" : "no"}</Field>
            {aegisMeta.provider ? (
              <Field label="Provider">{aegisMeta.provider}</Field>
            ) : null}
            {aegisMeta.error ? (
              <Field label="Error">
                <span className="font-mono text-xs text-red-600 dark:text-red-400">
                  {aegisMeta.error}
                </span>
              </Field>
            ) : null}
          </dl>
        </CardContent>
      </Card>

      {/* Evaluations — automated scores from the eval engine. Placed right
          after Summary because "is this response good?" is the next question
          after "what was sent and what came back?". */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Evaluations</CardTitle>
          <CardDescription>
            Automated scoring runs against this trace. The judge runs asynchronously —
            refresh in a few seconds if you just POSTed.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {trace.evaluations.length === 0 ? (
            <p className="text-sm text-muted-foreground italic">
              No evaluations yet for this trace.
            </p>
          ) : (
            trace.evaluations.map((evaluation) => (
              <EvaluationItem key={evaluation.eval_id} evaluation={evaluation} />
            ))
          )}
        </CardContent>
      </Card>

      {/* Messages: render parsed conversation when the SDK was the source;
          otherwise fall back to the raw prompt string. */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            {messages ? "Messages" : "Prompt"}
          </CardTitle>
          {messages ? (
            <CardDescription>
              {messages.length} message{messages.length === 1 ? "" : "s"} as sent to the model
            </CardDescription>
          ) : null}
        </CardHeader>
        <CardContent className="space-y-4">
          {messages ? (
            messages.map((m, i) => (
              <div key={i} className="space-y-1">
                <Badge variant="outline" className="font-mono text-xs">
                  {m.role}
                </Badge>
                <pre className="whitespace-pre-wrap break-words rounded-md bg-muted/50 p-3 text-sm">
                  {m.content}
                </pre>
              </div>
            ))
          ) : (
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/50 p-3 text-sm">
              {trace.prompt || (
                <span className="italic text-muted-foreground">(empty)</span>
              )}
            </pre>
          )}
        </CardContent>
      </Card>

      {/* Completion: the model's response text. */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Completion</CardTitle>
        </CardHeader>
        <CardContent>
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/50 p-3 text-sm">
            {trace.completion || (
              <span className="italic text-muted-foreground">(empty)</span>
            )}
          </pre>
        </CardContent>
      </Card>

      {/* Raw metadata — JSON, with the SDK's `aegis` namespaced block included. */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Metadata</CardTitle>
          <CardDescription>
            Anything under <span className="font-mono">aegis.*</span> is set by
            the SDK; everything else came from{" "}
            <span className="font-mono">aegis_metadata</span> at the call site.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <pre className="max-h-96 overflow-auto rounded-md bg-muted/50 p-3 text-xs">
            {JSON.stringify(trace.metadata, null, 2)}
          </pre>
        </CardContent>
      </Card>
    </main>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="mt-1">{children}</dd>
    </div>
  );
}

/** One eval row from the backend, rendered as scores + reasoning + footer. */
function EvaluationItem({ evaluation }: { evaluation: EvaluationRecord }) {
  const isError = evaluation.status === "error";
  return (
    <div className="space-y-3 border-l-2 border-muted pl-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge variant="outline" className="font-mono">
          {evaluation.evaluator}
        </Badge>
        <Badge variant={isError ? "destructive" : "secondary"}>
          {evaluation.status}
        </Badge>
        <span className="text-xs text-muted-foreground">
          {formatRelativeTime(evaluation.created_at)}
        </span>
      </div>

      {isError ? (
        <p className="text-sm text-red-600 dark:text-red-400">
          {evaluation.error || "Evaluator failed without a message."}
        </p>
      ) : (
        <>
          {/* Scores as labeled mini-bars. Keys aren't known at compile time —
              Judge emits accuracy/completeness/safety; PII will emit pii_score;
              the loop renders whatever's there. */}
          <div className="grid gap-2 sm:grid-cols-3">
            {Object.entries(evaluation.scores).map(([key, value]) => (
              <ScoreBar key={key} label={key} value={value} />
            ))}
          </div>
          {evaluation.reasoning ? (
            <p className="text-sm text-muted-foreground italic">
              “{evaluation.reasoning}”
            </p>
          ) : null}
        </>
      )}

      <p className="text-xs text-muted-foreground">
        {evaluation.judge_model ? `${evaluation.judge_model} · ` : ""}
        {formatLatency(evaluation.latency_ms)} · {formatCost(evaluation.cost_usd)}
      </p>
    </div>
  );
}

/** Color-coded 0-1 score with a horizontal bar. ≥0.8 green, ≥0.5 amber, else red.
 *  Thresholds match common eval-tooling conventions (LangSmith, Langfuse). */
function ScoreBar({ label, value }: { label: string; value: number }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const color =
    value >= 0.8 ? "bg-emerald-500" : value >= 0.5 ? "bg-amber-500" : "bg-red-500";
  return (
    <div>
      <div className="mb-1 flex justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-mono tabular-nums">{value.toFixed(2)}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-muted">
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
