// Display formatters for trace fields. Pure functions, no React — shared by the
// list and detail views so a number looks the same everywhere.

/** Cost in USD. Sub-cent values keep 6 dp (Groq free-tier traces are often $0). */
export function formatCost(usd: number): string {
  if (usd === 0) return "$0";
  if (usd < 0.01) return `$${usd.toFixed(6)}`;
  return `$${usd.toFixed(4)}`;
}

/** Latency: ms under a second, seconds above it. */
export function formatLatency(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(2)} s`;
}

/** Coarse "time ago" — computed at render time on the server, so it's a
 *  snapshot, not a live ticker (fine for v0.1; a live one would need a client component). */
export function formatRelativeTime(iso: string): string {
  const diffS = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (diffS < 60) return `${diffS}s ago`;
  if (diffS < 3600) return `${Math.round(diffS / 60)}m ago`;
  if (diffS < 86400) return `${Math.round(diffS / 3600)}h ago`;
  return `${Math.round(diffS / 86400)}d ago`;
}

/** Full timestamp for the detail view and row tooltips — ISO, seconds precision. */
export function formatTimestamp(iso: string): string {
  return new Date(iso).toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC");
}
