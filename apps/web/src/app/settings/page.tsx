import { connection } from "next/server";
import { AppNav } from "@/components/AppNav";
import { FeedBanner } from "@/components/FeedBanner";
import { SettingsForm } from "@/components/SettingsForm";
import { TelegramLink } from "@/components/TelegramLink";
import { loadFeedHealth, loadProfile, requireUser } from "@/lib/data";
import { fmtAge } from "@/lib/types";

export const instant = false;

const DEFAULT_PROFILE = {
  user_id: "",
  role: "trusted",
  display_name: null,
  capital_usd: null,
  risk_per_trade_pct: 0.005,
  max_position_pct: 0.2,
  max_gross_pct: 1.0,
  max_correlated_pct: 0.3,
  max_concurrent: 5,
  max_tickets_per_day: 10,
  venues: [] as string[],
  timezone: "UTC",
  updated_at: new Date(0).toISOString(),
};

/**
 * Settings.
 *
 * Three blocks, in order of how much they can hurt you:
 *   1. risk limits (what the ticket sizer may do),
 *   2. feeds (what the app can see, and what it honestly cannot),
 *   3. account facts (role, timezone).
 *
 * There is deliberately no field for a broker key. The web tier has no
 * custody: venue credentials live on the worker, and the worker cannot place
 * an order either.
 */
export default async function SettingsPage() {
  await connection();
  await requireUser();

  const [{ profile, error: profileError }, feeds] = await Promise.all([
    loadProfile(),
    loadFeedHealth(),
  ]);

  return (
    <>
      <AppNav />
      <main className="flex-1 overflow-y-auto p-4 space-y-6">
        <header className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h1 className="text-sm font-semibold">Settings</h1>
            <p className="text-xs text-muted mt-1">
              The limits the engine must obey. Changes are clamped, saved and
              audit-logged in one database call.
            </p>
          </div>
          <span className="text-[10px] uppercase tracking-wider text-muted border border-border rounded px-1.5 py-0.5">
            role · {profile?.role ?? DEFAULT_PROFILE.role}
          </span>
        </header>

        {profileError && (
          <div className="rounded-lg border border-down/40 bg-down/5 p-3 text-sm text-muted">
            {profileError}
          </div>
        )}

        <SettingsForm profile={profile ?? DEFAULT_PROFILE} />

        <section>
          <h2 className="text-xs font-semibold uppercase tracking-wider text-muted mb-2">
            Feeds
          </h2>
          <div className="rounded-lg border border-border bg-panel overflow-hidden">
            <FeedBanner feeds={feeds} />
            {!feeds || feeds.venues.length === 0 ? (
              <div className="p-4 text-sm text-muted">
                Feed health is unavailable. The worker may not have started
                yet.
              </div>
            ) : (
              <div className="divide-y divide-border/60">
                {feeds.venues.map((venue) => (
                  <div
                    key={venue.venue}
                    className="p-3 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-xs"
                  >
                    <span className="font-medium capitalize w-20">
                      {venue.venue}
                    </span>
                    <StatusDot status={venue.status} />
                    <span className="text-muted">
                      {venue.with_quotes}/{venue.symbols} symbols have a quote
                    </span>
                    <span className="text-muted tnum">
                      last tick {fmtAge(venue.age_seconds)}
                    </span>
                    <span className="text-muted/70">
                      {venue.data_quality}
                    </span>
                    {venue.hint && (
                      <span className="text-degraded/90 basis-full">
                        {venue.hint}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
          <p className="text-[11px] text-muted/70 mt-2">
            Venue credentials belong in{" "}
            <code className="text-foreground">services/live-worker/.env</code>,
            never here. A venue that was never connected says so; it is not
            called &ldquo;stale&rdquo;.
          </p>
        </section>

        <section>
          <h2 className="text-xs font-semibold uppercase tracking-wider text-muted mb-2">
            Telegram
          </h2>
          <TelegramLink />
        </section>

        <section className="rounded-lg border border-border bg-panel p-4">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-muted mb-2">
            Custody
          </h2>
          <p className="text-sm text-muted leading-relaxed">
            This app has no order endpoint and no broker write keys. It issues
            tickets; you place them. The only write the web tier can make on
            your behalf is a journal entry that records what you say you did.
          </p>
        </section>
      </main>
    </>
  );
}

function StatusDot({ status }: { status: string }) {
  const tone =
    status === "live"
      ? "text-live"
      : status === "not_configured" || status === "market_closed"
        ? "text-muted"
        : "text-degraded";
  return (
    <span className={`font-medium ${tone}`}>
      {status.replaceAll("_", " ")}
    </span>
  );
}
