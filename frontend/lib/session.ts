// Session = the project API key, held in an httpOnly cookie.
//
// Why a cookie and not localStorage: an httpOnly cookie is unreachable from
// client-side JS, so an XSS bug can't exfiltrate the key. It also rides along
// automatically on the server-component requests that fetch the backend — the
// browser never holds or forwards the key itself.
//
// Cookie *writes* (`set`/`delete`) can only happen in a Server Function or
// Route Handler — see app/login/actions.ts and app/actions.ts.

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

export const SESSION_COOKIE = "aegis_key";

/** Read the API key from the request cookie, or null if not logged in. */
export async function getApiKey(): Promise<string | null> {
  const store = await cookies();
  return store.get(SESSION_COOKIE)?.value ?? null;
}

/** Read the API key, or bounce to /login if there isn't one.
 *  Call this at the top of every protected server component. */
export async function requireApiKey(): Promise<string> {
  const key = await getApiKey();
  if (!key) redirect("/login");
  return key;
}
