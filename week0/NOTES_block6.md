# Week 0 · Block 6 — Next.js App Router + shadcn/ui refresher

**As of:** 2026-05-14 (Wed) · pre-Week 1 · Block 6 complete
**Pairs with:** [block6-nextjs/](block6-nextjs/) — a throwaway scaffold, NOT the
real `frontend/` (that's a v0.1 deliverable at the repo root)
**Reading goal:** so v0.1's `frontend/` build doesn't re-discover the Server
Action wiring, the Next 16 API shifts, or the shadcn-on-Tailwind-v4 setup.

---

## 1. What was built

A `create-next-app` scaffold in [block6-nextjs/](block6-nextjs/) with:

- **3 shadcn/ui components** — `Button` (added by `shadcn init` defaults),
  `Card`, `Table` (`shadcn add card table`).
- **One Server Action** — [app/actions.ts](block6-nextjs/app/actions.ts),
  file-level `"use server"`, mutates an in-memory store and calls
  `revalidatePath("/")`.
- **A Server Component page** — [app/page.tsx](block6-nextjs/app/page.tsx)
  reads the store directly (no `useEffect`, no client fetch), renders a
  `Card > Table` of synthetic traces, and a `<form action={addTraceAction}>`
  with the `Button` as submit.
- **A stand-in data layer** — [lib/trace-store.ts](block6-nextjs/lib/trace-store.ts),
  module-level array; in v0.1 this becomes a ClickHouse query module.

**Verified:**

| Check | Result |
|---|---|
| `npm run build` | ✓ compiled 4.5s, TypeScript clean, `/` prerendered static |
| `npm run start` + `GET /` | 200, page renders Card + Table + Button + both seed traces |
| Server Action, 2× POST | both 200; trace count 2 → 4, new rows render newest-first |

---

## 2. The version surprise — this is Next.js **16**, not 14

The README planned "Next.js 14." `create-next-app@latest` installed
**Next.js 16.2.6 / React 19.2.4 / Tailwind v4 / Turbopack**. The ecosystem
moved two majors since the plan was written. The scaffold even ships an
`AGENTS.md` saying *"This is NOT the Next.js you know — read
`node_modules/next/dist/docs/` before writing code."* — and that was correct
advice. README §line 111/159/560 updated 14 → 16 to keep the plan honest.

**Lesson, same as the Groq cadence:** pin the framework version explicitly in
v0.1 (`create-next-app@16` or an exact `next` version in `package.json`) so
the real `frontend/` build is reproducible and doesn't drift again mid-sprint.

### What actually changed vs. the "Next 14" mental model

- **Tailwind v4** — CSS-first config. No `tailwind.config.js` by default;
  theme tokens live in `globals.css` via `@theme`. `shadcn@latest` supports
  this; an older shadcn pinned to Tailwind v3 would have failed `init`.
- **Turbopack** is the default build+dev engine (`next build` says
  `(Turbopack)`).
- **`refresh()` from `next/cache`** is new in 16 — refreshes the client
  router after a mutation. Distinct from `revalidatePath` (invalidates the
  cache). For this block `revalidatePath("/")` is correct because the page
  re-render is what we need; `refresh()` is the lighter tool when there's no
  cached `fetch` involved. Both are documented and valid.
- Core Server Action API (`"use server"`, `<form action={fn}>`,
  `revalidatePath` from `next/cache`) is **unchanged** — my training-data
  mental model held for the part that mattered.

---

## 3. How a Server Action actually works (the part worth defending)

`"use server"` at the top of [app/actions.ts](block6-nextjs/app/actions.ts)
marks every export as a **Server Function**: code that runs only on the
server, never shipped in the client bundle. `<form action={addTraceAction}>`
binds it — no API route, no `fetch()`, no `onClick`.

At build time Next assigns each action a stable ID. The rendered form is:

```html
<form action="" method="POST" encType="multipart/form-data">
  <input type="hidden" name="$ACTION_ID_00fc54887f..." />
  <button type="submit">…</button>
</form>
```

That hidden `$ACTION_ID_*` input is the whole mechanism — it's how the server
knows which action a bare POST is invoking. This is **progressive
enhancement**: the form works with JavaScript disabled.

### Gotcha: there are TWO invocation protocols, don't mix them

First attempt to trigger the action via curl returned **500 "Connection
closed"**. Cause: I sent the JS-path `Next-Action: <id>` header *and* a
no-JS-path multipart body. They're two different protocols:

- **No-JS form submit** — plain `multipart/form-data` POST to the page URL,
  body carries `$ACTION_ID_<hash>=`. No special header.
- **JS (hydrated) submit** — `fetch` POST with a `Next-Action: <hash>` header
  and a React-serialized body.

Sending the header with the form body is neither. The fix was a plain
`curl -X POST / -F '$ACTION_ID_00fc...='` → 200, and the store mutated.

---

## 4. v0.1 `frontend/` work items that fell out of this block

When v0.1's real `frontend/` starts (repo root, per README §line 159/741):

1. **Pin the Next version** — exact `next` in `package.json`; §2.
2. **`trace-store.ts` → ClickHouse query module.** The Server Component
   reading data directly is the right shape; only the data source changes.
3. **Every Server Action needs an auth check.** The Next docs carry an
   explicit WARNING: Server Functions are reachable via direct POST, not just
   through your UI. v0.1's actions must verify the API key / session inside
   the function body — the `$ACTION_ID` is discoverable in page HTML.
4. **Decide `revalidatePath` vs `refresh` per mutation.** Cache invalidation
   vs client-router refresh; §2.
5. **TanStack Table + Recharts** (README §159) are not in this refresher —
   the shadcn `Table` is static markup. v0.1's trace explorer needs sorting/
   filtering/pagination, which is where TanStack Table comes in.
6. **`block6-nextjs/` is throwaway** — do not evolve it into `frontend/`.
   Start `frontend/` clean with the pinned version and the lessons here.

---

## 5. What I should be able to defend

1. **"Server Component vs Client Component?"** → Server Components are the
   App Router default; they run on the server, can read data directly, and
   ship zero JS. Client Components (`"use client"`) run in the browser and
   are needed for state/effects/event handlers. This page is a pure Server
   Component — the only interactivity is a form posting to a Server Action.

2. **"What does `"use server"` do?"** → marks a function (or all exports of a
   file) as a Server Function — server-only code, excluded from the client
   bundle, invocable from the client via a POST that Next routes by a
   build-time action ID.

3. **"Server Action vs API route — why pick the action?"** → no separate
   route file, no manual `fetch`, type-safe call site, and progressive
   enhancement for free (works with JS disabled). API routes are still right
   for third-party webhooks or non-form clients.

4. **"Why `revalidatePath` after the mutation?"** → the Server Action mutates
   server state but the client is still showing the old render.
   `revalidatePath("/")` tells Next the route's data changed; it re-renders
   the Server Component and streams fresh HTML — no client-side refetch.

5. **"How did you verify the action without a browser?"** → `npm run start`,
   then a plain multipart POST carrying the `$ACTION_ID` field (the no-JS
   form protocol). Trace count went 2 → 4 across two POSTs.

---

## 6. What this block intentionally does NOT do

- **Is not the real `frontend/`.** Throwaway scaffold in `week0/block6-nextjs/`.
  v0.1 builds `frontend/` fresh at the repo root.
- **No ClickHouse / no API.** Data is an in-memory module array that resets on
  server restart.
- **No auth.** The Server Action runs unauthenticated — fine for a sandbox,
  forbidden in v0.1 (§4 item 3).
- **No Client Components.** Zero `"use client"` — no client state, no
  `useActionState` pending UI, no optimistic updates. v0.1's explorer will
  need some.
- **No TanStack Table / Recharts / Monaco** — the shadcn `Table` is static
  markup; the planned interactive table/chart libs are untouched.
- **No styling polish or dark-mode wiring** beyond shadcn defaults.
- **No tests.** Playwright E2E is a v0.3 item.

---

**Last verified:** 2026-05-14 · `npm run build` green (TS clean) · `npm run
start` serves `/` at 200 · Server Action POST 200, store mutates 2 → 4 ·
scaffold + notes to be committed to `week0/` (the scaffold's own `.gitignore`
excludes `node_modules/` and `.next/`).
