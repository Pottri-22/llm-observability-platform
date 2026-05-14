// App-wide Server Functions. Currently just logout — clears the session cookie
// and bounces to /login. Bound to a <form action={logoutAction}> in the header.

"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { SESSION_COOKIE } from "@/lib/session";

export async function logoutAction() {
  const store = await cookies();
  store.delete(SESSION_COOKIE);
  redirect("/login");
}
