// Shared chrome for every /traces/* route: a header with the brand and a logout
// button. Logout is a <form action={logoutAction}> — a Server Function POST, no
// client JS, works with JavaScript disabled.

import Link from "next/link";

import { logoutAction } from "@/app/actions";
import { Button } from "@/components/ui/button";

export default function TracesLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      <header className="border-b">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-3">
          <Link href="/traces" className="text-lg font-semibold tracking-tight">
            Aegis
          </Link>
          <form action={logoutAction}>
            <Button type="submit" variant="ghost" size="sm">
              Log out
            </Button>
          </form>
        </div>
      </header>
      {children}
    </>
  );
}
