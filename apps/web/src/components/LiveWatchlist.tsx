"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import type { Instrument, Quote } from "@/lib/data";

interface LiveState {
  quotes: Record<string, Quote>;
  connected: boolean;
  lastMessageAt: number | null;
}

/**
 * Postgres `numeric` arrives as a STRING over PostgREST (and from the local
 * emulator). A price of "73492.0000000000" formatted with toLocaleString
 * comes out as the whole string, which is how a watchlist ends up showing
 * ten decimals of nothing. Everything numeric is coerced once, here.
 */
function normaliseQuote(row: Partial<Quote> | null | undefined): Quote | null {
  if (!row || !row.instrument_id) return null;
  const num = (value: unknown): number | null => {
    if (value === null || value === undefined || value === "") return null;
    const parsed = typeof value === "number" ? value : Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };
  return {
    instrument_id: row.instrument_id,
    bid: num(row.bid),
    ask: num(row.ask),
    last: num(row.last),
    change_pct: num(row.change_pct),
    volume: num(row.volume),
    source: row.source ?? "realtime",
    official: row.official ?? true,
    as_of: row.as_of ?? new Date().toISOString(),
  };
}

/**
 * The live watchlist.
 *
 * Subscribes to the `ticks` Realtime channel. The worker broadcasts ONE
 * bundle every 5 seconds containing every symbol, which is roughly 518k
 * messages a month against the 2M free-tier quota. We never subscribe to
 * Postgres Changes for prices: every change would be a separate message.
 *
 * If the socket drops, the component says so rather than showing stale
 * prices as if they were live. The last known price stays on screen, dimmed.
 */
export function LiveWatchlist({
  instruments,
  initialQuotes,
  selected,
  onSelect,
}: {
  instruments: Instrument[];
  initialQuotes: Quote[];
  selected: string | null;
  onSelect: (symbol: string) => void;
}) {
  const [state, setState] = useState<LiveState>({
    quotes: Object.fromEntries(
      initialQuotes
        .map((quote) => normaliseQuote(quote))
        .filter((quote): quote is Quote => quote !== null)
        .map((quote) => [quote.instrument_id, quote]),
    ),
    connected: false,
    lastMessageAt: null,
  });
  const [flash, setFlash] = useState<Record<string, "up" | "down">>({});
  const previous = useRef<Record<string, number | null>>({});

  useEffect(() => {
    const supabase = createClient();

    const channel = supabase
      .channel("ticks", { config: { broadcast: { self: false } } })
      .on("broadcast", { event: "bundle" }, (message) => {
        const payload = message.payload as
          | {
              ts: string;
              quotes: Record<
                string,
                { price: number; change_pct: number | null; as_of: string }
              >;
            }
          | undefined;

        if (!payload?.quotes) return;

        // Work out which symbols moved, OUTSIDE the state updater. React may
        // call the updater more than once, so computing side effects inside
        // it is a bug.
        const marks: Record<string, "up" | "down"> = {};
        for (const [instrumentId, q] of Object.entries(payload.quotes)) {
          const before = previous.current[instrumentId];
          if (before !== undefined && before !== null && q.price !== before) {
            marks[instrumentId] = q.price > before ? "up" : "down";
          }
          previous.current[instrumentId] = q.price;
        }

        setState((prev) => {
          const next = { ...prev.quotes };

          for (const [instrumentId, q] of Object.entries(payload.quotes)) {
            const existing = next[instrumentId];
            const normalised = normaliseQuote({
              instrument_id: instrumentId,
              bid: existing?.bid ?? null,
              ask: existing?.ask ?? null,
              last: q.price,
              change_pct: q.change_pct,
              volume: existing?.volume ?? null,
              source: existing?.source ?? "realtime",
              official: existing?.official ?? true,
              as_of: q.as_of,
            });
            if (normalised) next[instrumentId] = normalised;
          }

          return { quotes: next, connected: true, lastMessageAt: Date.now() };
        });

        if (Object.keys(marks).length > 0) {
          setFlash(marks);
          const timer = setTimeout(() => setFlash({}), 600);
          return () => clearTimeout(timer);
        }
      })
      .on("postgres_changes", { event: "*", schema: "public", table: "latest_quotes" },
        (payload) => {
          // Postgres Changes is a BACKSTOP for the broadcast. If the worker
          // writes a quote directly (for example during a backfill), the
          // watchlist still updates.
          const row = payload.new as Quote;
          const normalised = normaliseQuote(row);
          if (!normalised?.instrument_id) return;
          setState((prev) => ({
            ...prev,
            quotes: { ...prev.quotes, [normalised.instrument_id]: normalised },
          }));
        })
      .subscribe((status) => {
        setState((prev) => ({ ...prev, connected: status === "SUBSCRIBED" }));
      });

    return () => {
      supabase.removeChannel(channel);
    };
  }, []);

  // If no message has arrived for 15s, the feed is stale even if the socket
  // technically is open. Show it as such.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const stale =
    state.lastMessageAt !== null && now - state.lastMessageAt > 15_000;

  const rows = useMemo(() => {
    const byId = new Map(instruments.map((i) => [i.id, i]));
    return instruments
      .map((instrument) => ({
        instrument,
        quote: state.quotes[instrument.id] ?? null,
      }))
      .filter((r) => r.instrument)
      .sort((a, b) => {
        const order: Record<string, number> = {
          crypto: 0,
          us_equity: 1,
          forex: 2,
          metal: 3,
        };
        return (
          (order[a.instrument.asset_class] ?? 9) -
            (order[b.instrument.asset_class] ?? 9) ||
          a.instrument.symbol.localeCompare(b.instrument.symbol)
        );
      })
      .filter((r) => byId.has(r.instrument.id));
  }, [instruments, state.quotes]);

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-3 py-2 border-b border-border">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted">
          Watchlist
        </h2>
        <span
          className={`text-[10px] uppercase tracking-wider ${
            state.connected && !stale ? "text-live" : "text-degraded"
          }`}
        >
          {state.connected && !stale ? "live" : stale ? "stale" : "connecting"}
        </span>
      </div>

      <div className="flex-1 overflow-y-auto">
        {rows.map(({ instrument, quote }) => {
          const isSelected = selected === instrument.symbol;
          const mark = flash[instrument.id];
          const change = quote?.change_pct ?? null;

          return (
            <button
              key={instrument.id}
              onClick={() => onSelect(instrument.symbol)}
              className={`w-full text-left px-3 py-2 border-b border-border/50 transition-colors
                ${isSelected ? "bg-panel-2" : "hover:bg-panel-2/50"}
                ${mark === "up" ? "flash-up" : mark === "down" ? "flash-down" : ""}`}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="text-sm font-medium truncate">
                      {instrument.symbol}
                    </span>
                    {instrument.data_quality !== "official" && (
                      <span
                        className="text-[9px] uppercase px-1 py-px rounded border border-border text-muted"
                        title={
                          instrument.data_quality === "partial"
                            ? "IEX only: a fraction of consolidated market volume."
                            : "Indicative prices, not an executable feed."
                        }
                      >
                        {instrument.data_quality === "partial"
                          ? "partial"
                          : instrument.data_quality}
                      </span>
                    )}
                  </div>
                </div>
                <div className="text-right shrink-0">
                  <div className="text-sm tnum">
                    {quote?.last != null
                      ? quote.last.toLocaleString(undefined, {
                          minimumFractionDigits: 2,
                          maximumFractionDigits: 2,
                        })
                      : "—"}
                  </div>
                  {change != null && (
                    <div
                      className={`text-[11px] tnum ${
                        change >= 0 ? "text-up" : "text-down"
                      }`}
                    >
                      {change >= 0 ? "+" : ""}
                      {(change * 100).toFixed(2)}%
                    </div>
                  )}
                </div>
              </div>
            </button>
          );
        })}

        {rows.length === 0 && (
          <div className="p-4 text-xs text-muted">
            No instruments in the universe yet. Run the seed script.
          </div>
        )}
      </div>
    </div>
  );
}
