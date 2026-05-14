// Root route — there's no landing page in v0.1. Send everyone to /traces, which
// itself bounces to /login when there's no session.

import { redirect } from "next/navigation";

export default function RootPage() {
  redirect("/traces");
}
