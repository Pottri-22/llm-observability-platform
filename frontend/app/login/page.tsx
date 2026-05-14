// Login page — Server Component. If a session cookie is already present we skip
// straight to /traces; otherwise we render the paste-key form.

import { redirect } from "next/navigation";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { getApiKey } from "@/lib/session";

import { LoginForm } from "./login-form";

export default async function LoginPage() {
  if (await getApiKey()) redirect("/traces");

  return (
    <main className="flex flex-1 items-center justify-center p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="text-2xl">Aegis</CardTitle>
          <CardDescription>
            Paste a project API key to view its traces. The key is stored in an
            httpOnly cookie — it never reaches client-side JavaScript.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <LoginForm />
        </CardContent>
      </Card>
    </main>
  );
}
