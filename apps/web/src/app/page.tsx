import { redirect } from "next/navigation";
import { connection } from "next/server";
import { loadCommandCenter, requireUser } from "@/lib/data";
import { EngineBadge } from "@/components/EngineBadge";
import { AppNav } from "@/components/AppNav";
import { FeedBanner } from "@/components/FeedBanner";
import { TimeframePicker, TimeframeProvider } from "@/components/TimeframePicker";
import { SymbolProvider } from "@/components/SymbolProvider";
import {
  DecisionCenterClient,
  LiveWatchlistClient,
} from "@/components/CommandCenterClient";

/**
 * The Command Center must never be prerendered.
 *
 * It reads the session and live prices. With Cache Components enabled, a
 * route that blocks on the server opts out of instant navigation:
 *
 *   export const instant = false
 *
 * Without this the page would be prerendered at BUILD time — and at build
 * time the Supabase env vars are absent, so it would bake in the
 * "not configured" screen and serve that forever.
 */
export const instant = false;

/**
 * The Command Center.
 *
 * One screen. The answer to "what should I do right now, and why" is above
 * the fold on a phone: the watchlist on the left, the decision on the right,
 * the engine's health at the top.
 *
 * If the engine is down, the page says so in the top bar. It does not show a
 * stale ticket and hope nobody notices.
 */
export default async function CommandCenterPage() {
  // `connection()` marks this route as request-time. Without it the page
  // would be prerendered at BUILD time — and at build time the Supabase env
  // vars are absent, so it would bake in the "not configured" screen and
  // serve that forever.
  await connection();

  // The session read happens first and unconditionally.
  await requireUser();

  const { instruments, quotes, engine, feeds, configured, error } =
    await loadCommandCenter();

  if (!configured) {
    return (
      <main className="flex-1 flex items-center justify-center p-6">
        <div className="max-w-md rounded-lg border border-degraded/40 bg-degraded/5 p-5">
          <h1 className="text-sm font-semibold mb-2">Not configured</h1>
          <p className="text-sm text-muted leading-relaxed">{error}</p>
          <p className="text-xs text-muted mt-3">
            Copy <code className="text-foreground">.env.example</code> to{" "}
            <code className="text-foreground">.env.local</code> and fill it in.
          </p>
        </div>
      </main>
    );
  }

  if (error) {
    return (
      <main className="flex-1 flex items-center justify-center p-6">
        <div className="max-w-md rounded-lg border border-down/40 bg-down/5 p-5">
          <h1 className="text-sm font-semibold mb-2">Could not load</h1>
          <p className="text-sm text-muted leading-relaxed">{error}</p>
        </div>
      </main>
    );
  }

  if (instruments.length === 0) {
    return (
      <main className="flex-1 flex items-center justify-center p-6">
        <div className="max-w-md rounded-lg border border-border bg-panel p-5">
          <h1 className="text-sm font-semibold mb-2">No instruments yet</h1>
          <p className="text-sm text-muted leading-relaxed">
            The database is reachable but the universe is empty. Run the seed
            script to load the 12 instruments, then start the live worker.
          </p>
        </div>
      </main>
    );
  }

  // Open on an instrument that actually has a price. Alphabetically the
  // first symbol is AAPL, which is closed at this hour and looks broken.
  const quoted = new Set(quotes.map((quote) => quote.instrument_id));
  const initialSymbol =
    instruments.find((instrument) => quoted.has(instrument.id))?.symbol ??
    instruments[0]?.symbol ??
    null;

  return (
    <SymbolProvider initial={initialSymbol}>
      <TimeframeProvider>
        <div className="flex-1 flex flex-col min-h-0">
          <AppNav />
          <header className="flex items-center justify-between gap-3 px-4 py-2 border-b border-border">
            <div className="flex items-center gap-3 min-w-0">
              <h1 className="text-sm font-semibold tracking-tight shrink-0">
                Command Center
              </h1>
              <span className="text-xs text-muted hidden sm:inline truncate">
                Trading Command Center
              </span>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <TimeframePicker />
              <EngineBadge engine={engine} />
            </div>
          </header>

          <FeedBanner feeds={feeds} />

          <div className="flex-1 grid grid-cols-1 md:grid-cols-[minmax(200px,240px)_1fr] min-h-0">
            <aside className="border-b md:border-b-0 md:border-r border-border min-h-0 h-48 md:h-auto">
              <LiveWatchlistClient
                instruments={instruments}
                initialQuotes={quotes}
              />
            </aside>
            <section className="min-h-0 overflow-hidden">
              <DecisionCenterClient />
            </section>
          </div>
        </div>
      </TimeframeProvider>
    </SymbolProvider>
  );
}
