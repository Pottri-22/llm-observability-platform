// Login as a Server Function: validate the pasted key against the backend,
// and on success drop it into an httpOnly cookie and redirect to /traces.
//
// Doing this server-side (not a client fetch) keeps the key off the browser
// JS heap entirely — it goes straight from the form POST into the cookie.

"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { validateKey } from "@/lib/backend";
import { SESSION_COOKIE } from "@/lib/session";

export type LoginState = { error: string | null };

export async function loginAction(
  _prev: LoginState,
  formData: FormData,
): Promise<LoginState> {
  const apiKey = String(formData.get("apiKey") ?? "").trim();

  // Cheap format check first — no point round-tripping an obviously-wrong key.
  if (!apiKey.startsWith("aegis_")) {
    return { error: "That doesn't look like an Aegis key — they start with \"aegis_\"." };
  }

  // Authoritative check: the backend bcrypt-verifies the key.
  if (!(await validateKey(apiKey))) {
    return {
      error:
        "Key rejected. Check it's correct and that the Aegis API is reachable.",
    };
  }

  const store = await cookies();
  store.set(SESSION_COOKIE, apiKey, {
    httpOnly: true, // unreachable from client JS — XSS can't read the key
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24 * 7, // 7 days
    secure: process.env.NODE_ENV === "production",
  });

  // redirect() throws a control-flow signal; nothing after it runs.
  redirect("/traces");
}
