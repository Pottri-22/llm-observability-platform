// Trace list — DASH-A stub. `requireApiKey()` enforces the session (bounces to
// /login if absent), which is what this stub exists to verify end-to-end.
// The real list table + pagination + filtering lands in DASH-B.

import { requireApiKey } from "@/lib/session";

export default async function TracesPage() {
  await requireApiKey();
  return (
    <main className="mx-auto max-w-5xl flex-1 p-6">
      <p className="text-sm text-muted-foreground">
        Authenticated. Trace list table arrives in DASH-B.
      </p>
    </main>
  );
}
