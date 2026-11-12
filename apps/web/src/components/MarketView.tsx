"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { PriceChart } from "@/components/PriceChart";
import {
  analyse,
  marketOpen,
  type Candle,
} from "@/lib/analysis";
import {
  fmt,
  fmtPct,
  isTradeTicket,
  type TicketResponse,
} from "@/lib/types";

type Timeframe = "1m" | "5m" | "1h" | "1d";
const TIMEFRAMES: Timeframe[] = ["1m", "5m", "1h", "1d"];

export interface MarketInstrument {
  id: string;
  symbol: string;
  display_name: string | null;
  venue: string;
  asset_class: string;
  data_quality: string;
}

/**
 * One instrument, in full: closed bars, deterministic indicators, and the
 * engine's current verdict.
 *
 * Everything on this screen is computed from the same closed bars the signal
 * engine used. The chart is not decoration and the analysis is not a second
 * opinion: if they disagree with the ticket, the ticket is the one that sizes.
 */
export function MarketView({
  instrument,
  initialTimeframe = "1h",
}: {
  instrument: MarketInstrument;
  initialTimeframe?: Timeframe;
}) {
  const [timeframe, setTimeframe] = useState<Timeframe>(initialTimeframe);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [ticket, setTicket] = useState<TicketResponse | null>(null);
  const [barsError, setBarsError] = useState<string | null>(null);
  const [available, setAvailable] = useState<Record<string, number>>({});
  const [loadingBars, setLoadingBars] = useState(true);
  const [refreshAt, setRefreshAt] = useState<number>(Date.now());
  const [autoChosen, setAutoChosen] = useState(false);

  const loadBars = useCallback(async () => {
    setLoadingBars(true);
    setBarsError(null);
    try {
      const res = await fetch(
        `/api/bars?symbol=${encodeURIComponent(instrument.symbol)}&timeframe=${timeframe}&limit=300`,
        { cache: "no-store" },
      );
      const data = (await res.json()) as {
        bars?: Candle[];
        error?: string;
        available?: Record<string, number>;
      };
      if (!res.ok) {
        setBarsError(data.error ?? "Could not read bars.");
        setCandles([]);
        return;
      }
      setCandles(data.bars ?? []);
      setAvailable(data.available ?? {});
    } catch {
      setBarsError("Could not reach the bar store.");
      setCandles([]);
    } finally {
      setLoadingBars(false);
    }
  }, [instrument.symbol, timeframe]);

  const loadTicket = useCallback(async () => {
    try {
      const res = await fetch(
        `/api/ticket?instrument=${encodeURIComponent(instrument.symbol)}&timeframe=${timeframe}`,
        { cache: "no-store" },
      );
      setTicket((await res.json()) as TicketResponse);
    } catch {
      setTicket({
        status: "engine_unavailable",
        reason_code: "engine_down",
        reason: "Could not reach the ticket engine.",
      });
    }
  }, [instrument.symbol, timeframe]);

  useEffect(() => {
    void loadBars();
    void loadTicket();
    const t = setInterval(() => {
      void loadBars();
      void loadTicket();
      setRefreshAt(Date.now());
    }, 60_000);
    return () => clearInterval(t);
  }, [loadBars, loadTicket]);

  // A fresh install has 1m bars and no 1h bar for an hour. Rather than show
  // an empty chart, move to a timeframe that has data — once, and only if the
  // user has not already chosen one.
  useEffect(() => {
    if (autoChosen || candles.length > 0) return;
    const options = Object.keys(available);
    if (options.length === 0) return;
    const pick = options.includes("1m") ? "1m" : options[0];
    if (pick === timeframe) return;
    setAutoChosen(true);
    setTimeframe(pick as Timeframe);
  }, [available, autoChosen, candles.length, timeframe]);

  const analysis = useMemo(() => analyse(candles), [candles]);
  const session = useMemo(
    () => marketOpen(instrument.asset_class),
    [instrument.asset_class],
  );

  const levels = isTradeTicket(ticket)
    ? {
        entry: ticket.entry_price,
        stop: ticket.stop_price,
        target: ticket.target_price,
      }
    : {};

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <header className="flex flex-wrap items-center justify-between gap-2 px-4 py-2 border-b border-border">
        <div className="flex items-baseline gap-2 min-w-0">
          <h1 className="text-sm font-semibold">{instrument.symbol}</h1>
          <span className="text-xs text-muted truncate">
            {instrument.display_name ?? instrument.asset_class}
          </span>
          <span className="text-[10px] uppercase tracking-wider text-muted border border-border rounded px-1 py-0.5">
            {instrument.venue} · {instrument.data_quality}
          </span>
        </div>

        <div className="flex items-center gap-2">
          <span
            className={`text-[10px] rounded border px-1.5 py-0.5 ${
              session.open
                ? "text-live border-live/30"
                : "text-muted border-border"
            }`}
          >
            {session.label}
          </span>
          <div className="flex items-center rounded border border-border overflow-hidden">
            {TIMEFRAMES.map((tf) => (
              <button
                key={tf}
                onClick={() => setTimeframe(tf)}
                className={`px-2 py-1 text-[11px] transition-colors ${
                  timeframe === tf
                    ? "bg-panel-2 text-foreground"
                    : "text-muted hover:text-foreground"
                }`}
              >
                {tf}
              </button>
            ))}
          </div>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {loadingBars && candles.length === 0 ? (
          <div className="rounded-lg border border-border bg-panel p-6 text-sm text-muted">
            Loading closed bars…
          </div>
        ) : candles.length === 0 ? (
          <div className="rounded-lg border border-degraded/40 bg-degraded/5 p-4">
            <h2 className="text-sm font-semibold mb-1">No closed bars yet</h2>
            <p className="text-sm text-muted leading-relaxed">
              {barsError ??
                `The worker has not stored a closed ${timeframe} bar for ${instrument.symbol} yet. This is a data state, not a signal.`}
            </p>

            {Object.keys(available).length > 0 && (
              <div className="mt-3">
                <div className="text-[10px] uppercase tracking-wider text-muted mb-1">
                  Timeframes that do have data
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(available).map(([tf, count]) => (
                    <button
                      key={tf}
                      onClick={() => setTimeframe(tf as Timeframe)}
                      className="px-2 py-1 rounded text-[11px] border border-border bg-panel text-muted hover:text-foreground hover:border-muted transition-colors"
                    >
                      {tf} · {count} bars
                    </button>
                  ))}
                </div>
              </div>
            )}

            <p className="text-xs text-muted/80 mt-2">
              {instrument.data_quality === "partial"
                ? "This venue supplies partial-market data. Fewer bars and wider gaps are expected."
                : "If this persists, check the feed banner in Settings and confirm the venue is connected."}
            </p>
          </div>
        ) : (
          <>
            <PriceChart candles={candles} levels={levels} height={360} />

            <section className="grid grid-cols-2 md:grid-cols-4 gap-2">
              <Stat
                label="Last close"
                value={fmt(analysis.last, 4)}
                tone="text-foreground"
              />
              <Stat
                label="Trend"
                value={
                  analysis.trend === "up"
                    ? "Up"
                    : analysis.trend === "down"
                      ? "Down"
                      : analysis.trend === "range"
                        ? "Range"
                        : "Unknown"
                }
                tone={
                  analysis.trend === "up"
                    ? "text-live"
                    : analysis.trend === "down"
                      ? "text-down"
                      : "text-muted"
                }
                detail={analysis.trendDetail}
              />
              <Stat
                label="RSI 14"
                value={fmt(analysis.rsi, 1)}
                tone={
                  analysis.rsi == null
                    ? "text-muted"
                    : analysis.rsi >= 70
                      ? "text-down"
                      : analysis.rsi <= 30
                        ? "text-live"
                        : "text-foreground"
                }
                detail={
                  analysis.rsi == null
                    ? "Not enough bars"
                    : analysis.rsi >= 70
                      ? "Overbought"
                      : analysis.rsi <= 30
                        ? "Oversold"
                        : "Neutral"
                }
              />
              <Stat
                label="ATR 14"
                value={
                  analysis.atrPct == null ? "—" : fmtPct(analysis.atrPct, 2)
                }
                detail="of last price"
              />
              <Stat
                label="20-bar high"
                value={fmt(analysis.high20, 4)}
              />
              <Stat label="20-bar low" value={fmt(analysis.low20, 4)} />
              <Stat
                label="20-bar change"
                value={fmtPct(analysis.change20Pct, 2)}
                tone={
                  analysis.change20Pct == null
                    ? "text-muted"
                    : analysis.change20Pct >= 0
                      ? "text-live"
                      : "text-down"
                }
              />
              <Stat
                label="Bars"
                value={`${candles.length} · ${timeframe}`}
                detail={`at ${new Date(refreshAt).toLocaleTimeString()}`}
              />
            </section>
          </>
        )}

        <Verdict ticket={ticket} timeframe={timeframe} />
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  tone = "text-foreground",
  detail,
}: {
  label: string;
  value: string;
  tone?: string;
  detail?: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-panel p-3">
      <div className="text-[10px] uppercase tracking-wider text-muted mb-1">
        {label}
      </div>
      <div className={`text-sm font-medium tnum ${tone}`}>{value}</div>
      {detail && <div className="text-[10px] text-muted/70 mt-1">{detail}</div>}
    </div>
  );
}

function Verdict({
  ticket,
  timeframe,
}: {
  ticket: TicketResponse | null;
  timeframe: string;
}) {
  if (!ticket) {
    return (
      <div className="rounded-lg border border-border bg-panel p-4 text-sm text-muted">
        Checking the engine…
      </div>
    );
  }

  if (isTradeTicket(ticket)) {
    return (
      <div className="rounded-lg border border-live/30 bg-live/5 p-4">
        <div className="flex items-baseline justify-between gap-2 mb-2">
          <h2 className="text-sm font-semibold">
            {ticket.side.toUpperCase()} {ticket.instrument} · {timeframe}
          </h2>
          <span className="text-[10px] uppercase tracking-wider text-muted">
            {ticket.rule_id ?? "champion"}
          </span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
          <Field label="Entry" value={fmt(ticket.entry_price, 4)} />
          <Field label="Stop" value={fmt(ticket.stop_price, 4)} />
          <Field label="Target" value={fmt(ticket.target_price, 4)} />
          <Field label="Size" value={fmt(ticket.size_units, 6)} />
          <Field label="Risk" value={fmt(ticket.risk_amount, 2)} />
          <Field label="R:R" value={fmt(ticket.reward_risk, 2)} />
          <Field label="Data" value={ticket.data_quality} />
          <Field
            label="Bar close"
            value={new Date(ticket.signal_bar_close_time).toLocaleTimeString()}
          />
        </div>
        <p className="text-xs text-muted mt-3">
          The full ticket, warnings and journal action live in the{" "}
          <Link href="/" className="text-accent hover:underline">
            Command Center
          </Link>
          . This app never places the order.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border bg-panel p-4">
      <div className="flex items-baseline justify-between gap-2 mb-1">
        <h2 className="text-sm font-semibold">
          No ticket · {ticket.status.replaceAll("_", " ")}
        </h2>
        {ticket.reason_code && (
          <span className="text-[10px] uppercase tracking-wider text-muted/70">
            {ticket.reason_code}
          </span>
        )}
      </div>
      <p className="text-sm text-muted leading-relaxed">{ticket.reason}</p>
      <p className="text-[11px] text-muted/70 mt-2">
        A refusal is a successful answer. The engine will not invent a setup.
      </p>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted">
        {label}
      </div>
      <div className="tnum">{value}</div>
    </div>
  );
}
