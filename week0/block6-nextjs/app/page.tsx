// Week 0 · Block 6 — App Router page wiring together the 3 shadcn components
// (Card, Table, Button) and one Server Action.
//
// This is a Server Component (the App Router default): it runs on the server,
// reads the trace store directly — no useEffect, no client-side fetch — and
// streams HTML. The only interactivity is the form, which POSTs to a Server
// Action.

import { addTraceAction } from "./actions";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { listTraces } from "@/lib/trace-store";

export default function Home() {
  const traces = listTraces();

  return (
    <main className="mx-auto max-w-3xl p-8">
      <Card>
        <CardHeader>
          <CardTitle>Aegis · Week 0 Block 6 refresher</CardTitle>
          <CardDescription>
            App Router Server Component + Server Action + 3 shadcn components
            (Card, Table, Button). The table is server-rendered from an
            in-memory store; the button POSTs to a Server Action that mutates
            the store and revalidates this route.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Table>
            <TableCaption>{traces.length} trace(s) — newest first</TableCaption>
            <TableHeader>
              <TableRow>
                <TableHead>Trace</TableHead>
                <TableHead>Model</TableHead>
                <TableHead className="text-right">Tokens in/out</TableHead>
                <TableHead className="text-right">Cost (USD)</TableHead>
                <TableHead className="text-right">Latency</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {traces.map((t) => (
                <TableRow key={t.id}>
                  <TableCell className="font-mono text-xs">{t.id}</TableCell>
                  <TableCell>{t.model}</TableCell>
                  <TableCell className="text-right">
                    {t.tokensIn} / {t.tokensOut}
                  </TableCell>
                  <TableCell className="text-right">
                    ${t.costUsd.toFixed(6)}
                  </TableCell>
                  <TableCell className="text-right">{t.latencyMs} ms</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          {/*
            The form's `action` is the Server Action itself — not a URL string.
            Next.js serializes the submit, runs addTraceAction on the server,
            then re-renders this Server Component with the updated store.
          */}
          <form action={addTraceAction}>
            <Button type="submit">+ Add synthetic trace</Button>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
