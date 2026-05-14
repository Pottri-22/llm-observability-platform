// Client Component: the only interactive bit of the login page. `useActionState`
// wires the form to the `loginAction` Server Function and surfaces its returned
// error (or the pending state while it's in flight). On success the action
// redirects, so this component just unmounts — no success branch needed here.

"use client";

import { useActionState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import { loginAction, type LoginState } from "./actions";

const INITIAL: LoginState = { error: null };

export function LoginForm() {
  const [state, formAction, pending] = useActionState(loginAction, INITIAL);

  return (
    <form action={formAction} className="space-y-3">
      <Input
        name="apiKey"
        type="password"
        placeholder="aegis_live_…"
        autoComplete="off"
        spellCheck={false}
        required
        aria-label="Aegis project API key"
      />
      {state.error ? (
        <p className="text-sm text-red-600 dark:text-red-400">{state.error}</p>
      ) : null}
      <Button type="submit" disabled={pending} className="w-full">
        {pending ? "Checking…" : "Connect"}
      </Button>
    </form>
  );
}
