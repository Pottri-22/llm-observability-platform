// Trace list — a Server Component. It reads the session cookie, fetches one page
// of traces from the backend directly (no client fetch, no useEffect), and
// streams the table as HTML. Pagination and the model filter are plain URL
// query params, so Prev/Next/filter are just <Link>s and a GET <form> — every
// state of this page is a bookmarkable URL, and it all works without JS.

import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { BackendError, listTraces } from "@/lib/backend";
import { formatCost, formatLatency, formatRelativeTime } from "@/lib/format";
import { requireApiKey } from "@/lib/session";
import type { TraceListResponse } from "@/lib/types";

const PAGE_SIZE = 50;

/** Build a /traces URL preserving the model filter, omitting empty params. */
function tracesHref(offset: number, model?: string): string {
  const qs = new URLSearchParams();
  if (offset > 0) qs.set("offset", String(offset));
  if (model) qs.set("model", model);
  const s = qs.toString();
  return s ? `/traces?${s}` : "/traces";
}

export default async function TracesPage({
  searchParams,
}: {
  searchParams: Promise<{ offset?: string; model?: string }>;
}) {
  const apiKey = await requireApiKey();
  const sp = await searchParams;

  const offset = Math.max(0, Number.parseInt(sp.offset ?? "0", 10) || 0);
  const model = sp.model?.trim() || undefined;

  let data: TraceListResponse | null = null;
  let errorMsg: string | null = null;
  try {
    data = await listTraces(apiKey, { limit: PAGE_SIZE, offset, model });
  } catch (e) {
    errorMsg =
      e instanceof BackendError
        ? `Backend error (${e.code}): ${e.message}`
        : "Could not reach the Aegis API. Is the backend running?";
  }

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-8">
      <div className="mb-6 flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Traces</h1>
          {data ? (
            <p className="text-sm text-muted-foreground">
              {data.meta.total.toLocaleString()} total
              {model ? (
                <>
                  {" "}
                  · filtered by <span className="font-mono">{model}</span>
                </>
              ) : null}
            </p>
          ) : null}
        </div>
        {/* Plain GET form — submitting navigates to /traces?model=… (page 1). */}
        <form action="/traces" method="get" className="flex items-center gap-2">
          <Input
            name="model"
            defaultValue={model ?? ""}
            placeholder="filter by model…"
            className="h-9 w-56"
          />
          <Button type="submit" size="sm" variant="secondary">
            Filter
          </Button>
          {model ? (
            <Link
              href="/traces"
              className={buttonVariants({ size: "sm", variant: "ghost" })}
            >
              Clear
            </Link>
          ) : null}
        </form>
      </div>

      {errorMsg ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-red-600 dark:text-red-400">
            {errorMsg}
          </CardContent>
        </Card>
      ) : data && data.traces.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            {model
              ? "No traces match that model filter."
              : "No traces yet. Instrument an app with the Aegis SDK to see traces here."}
          </CardContent>
        </Card>
      ) : data ? (
        <>
          <Card>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-28">Time</TableHead>
                    <TableHead>Model</TableHead>
                    <TableHead>Prompt</TableHead>
                    <TableHead className="text-right">Tokens</TableHead>
                    <TableHead className="text-right">Cost</TableHead>
                    <TableHead className="text-right">Latency</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.traces.map((t) => (
                    <TableRow key={t.trace_id} className="hover:bg-muted/50">
                      <TableCell className="whitespace-nowrap">
                        {/* The clickable cell — navigates to the trace detail. */}
                        <Link
                          href={`/traces/${t.trace_id}`}
                          title={`${t.ts} · ${t.trace_id}`}
                          className="font-medium text-foreground underline-offset-4 hover:underline"
                        >
                          {formatRelativeTime(t.ts)}
                        </Link>
                      </TableCell>
                      <TableCell>
                        <Badge variant="secondary" className="font-mono text-xs">
                          {t.model}
                        </Badge>
                      </TableCell>
                      <TableCell className="max-w-md truncate text-muted-foreground">
                        {t.prompt_preview || <span className="italic">—</span>}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {t.tokens_in} / {t.tokens_out}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatCost(t.cost_usd)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatLatency(t.latency_ms)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          <div className="mt-4 flex items-center justify-between text-sm text-muted-foreground">
            <span>
              Showing {offset + 1}–{offset + data.traces.length} of{" "}
              {data.meta.total.toLocaleString()}
            </span>
            <div className="flex gap-2">
              {offset > 0 ? (
                <Link
                  href={tracesHref(Math.max(0, offset - PAGE_SIZE), model)}
                  className={buttonVariants({ size: "sm", variant: "outline" })}
                >
                  Previous
                </Link>
              ) : (
                <Button size="sm" variant="outline" disabled>
                  Previous
                </Button>
              )}
              {offset + PAGE_SIZE < data.meta.total ? (
                <Link
                  href={tracesHref(offset + PAGE_SIZE, model)}
                  className={buttonVariants({ size: "sm", variant: "outline" })}
                >
                  Next
                </Link>
              ) : (
                <Button size="sm" variant="outline" disabled>
                  Next
                </Button>
              )}
            </div>
          </div>
        </>
      ) : null}
    </main>
  );
}
