import type { FeedHealth, VenueHealth } from "@/lib/types";

/**
 * The feed banner.
 *
 * This exists because the app used to say "Data is stale / No price data for
 * AAPL yet" when the actual problem was that no Alpaca key had ever been
 * configured. Those are different facts and the user is entitled to the true
 * one. `configured=false` renders as "not connected", never "stale".
 */
export function FeedBanner({ feeds }: { feeds: FeedHealth | null }) {
  if (!feeds || feeds.venues.length === 0) return null;

  return (
    <div className="flex items-center gap-2 px-4 py-1.5 border-b border-border overflow-x-auto">
      <span className="text-[10px] uppercase tracking-wider text-muted shrink-0">
        Feeds
      </span>
      {feeds.venues.map((venue) => (
        <VenueChip key={venue.venue} venue={venue} />
      ))}
      <span className="ml-auto text-[10px] text-muted/60 hidden md:inline shrink-0">
        Binance official · Alpaca IEX partial · OANDA practice indicative
      </span>
    </div>
  );
}

function VenueChip({ venue }: { venue: VenueHealth }) {
  const label = {
    live: `${venue.with_quotes}/${venue.symbols} live`,
    stale: "stale",
    market_closed: "market closed",
    no_data: "connected · no price",
    down: "disconnected",
    engine_down: "worker down",
    not_configured: "not connected",
  }[venue.status];

  const tone = {
    live: "text-live border-live/30",
    market_closed: "text-muted border-border",
    no_data: "text-degraded border-degraded/30",
    stale: "text-down border-down/30",
    down: "text-down border-down/30",
    engine_down: "text-down border-down/30",
    not_configured: "text-degraded border-degraded/30",
  }[venue.status];

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5 text-[10px] shrink-0 ${
        venue.contract_ok === false ? "text-down border-down/40" : tone
      }`}
      title={
        venue.contract_ok === false
          ? `${venue.venue}: the provider's response shape changed. The last check ran at ${
              venue.contract_checked_at ?? "an unknown time"
            }.`
          : (venue.hint ?? `${venue.venue}: ${venue.status}`)
      }
    >
      <span className="font-medium">{titleCase(venue.venue)}</span>
      <span className="opacity-80">
        {venue.contract_ok === false ? "contract changed" : label}
      </span>
    </span>
  );
}

function titleCase(value: string): string {
  return value.length === 0 ? value : value[0].toUpperCase() + value.slice(1);
}
