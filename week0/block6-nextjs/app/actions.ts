// Week 0 · Block 6 — a Server Action.
//
// The "use server" directive marks every export in this file as a Server
// Action: code that runs ONLY on the server and is never shipped in the
// client bundle. A <form action={addTraceAction}> POSTs to it directly —
// no API route, no fetch(), no onClick handler.

"use server";

import { revalidatePath } from "next/cache";

import { addSyntheticTrace } from "@/lib/trace-store";

export async function addTraceAction() {
  // Mutate server-side state. In v0.1 this would be a ClickHouse INSERT.
  addSyntheticTrace();

  // Tell Next.js the data behind "/" changed. The server component re-renders
  // with the fresh trace list and the new HTML streams back — the client did
  // not fetch anything itself.
  revalidatePath("/");
}
