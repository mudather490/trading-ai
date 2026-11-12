"use client";

import { useCallback, useEffect, useState } from "react";
import {
  fmt,
  fmtPct,
  isTradeTicket,
  type NonTicket,
  type TicketResponse,
  type TradeTicket,
} from "@/lib/types";

/**
 * The Decision Center.
 *
 * This is the component that matters most, and the one that must be most
 * honest. It renders FIVE states, and four of them are "no trade":
 *
 *   ticket             a complete ticket
 *   wait               nothing qualifies right now
 *   no_match           no registered setup for this instrument
 *   stale              the data is too old to act on
 *   engine_unavailable the worker is not running
 *
 * There is no "Buy" button. The primary action is "Journal this ticket",
 * because the app advises and the human acts.
 */
export function DecisionCenter({
  symbol,
  timeframe,
}: {
  symbol: string | null;
  timeframe: string;
}) {
  const [ticket, setTicket] = useState<TicketResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [journalState, setJournalState] = useState<
    "idle" | "choosing" | "saving" | "saved" | "error"
  >("idle");
  const [journalAction, setJournalAction] = useState<string | null>(null);
  const [journalError, setJournalError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!symbol) return;
    setLoading(true);
    try {
      const res = await fetch(
        `/api/ticket?instrument=${encodeURIComponent(symbol)}&timeframe=${timeframe}`,
        { cache: "no-store" },
      );
      const data = (await res.json()) as TicketResponse;
      setTicket(data);
      setJournalState("idle");
      setJournalAction(null);
      setJournalError(null);
    } catch {
      setTicket({
        status: "engine_unavailable",
        reason: "Could not reach the ticket engine.",
      });
    } finally {
      setLoading(false);
    }
  }, [symbol, timeframe]);

  const journal = useCallback(
    async (action: "took" | "skipped") => {
      if (!symbol) return;
      setJournalState("saving");
      setJournalError(null);
      try {
        const res = await fetch("/api/journal", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ instrument: symbol, timeframe, action }),
        });
        const data = (await res.json()) as { error?: string };
        if (!res.ok) {
          setJournalError(data.error ?? "Could not journal this ticket.");
          setJournalState("error");
          return;
        }
        setJournalAction(action);
        setJournalState("saved");
      } catch {
        setJournalError("Could not reach the journal service.");
        setJournalState("error");
      }
    },
    [symbol, timeframe],
  );

  useEffect(() => {
    void load();
    // Re-check every 30s: a signal can expire, and the freshness gate is
    // time-dependent.
    const t = setInterval(() => void load(), 30_000);
    return () => clearInterval(t);
  }, [load]);

  if (!symbol) {
    return (
      <div className="flex items-center justify-center h-full p-6 text-sm text-muted">
        Select an instrument to see its setup.
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-2 border-b border-border">
        <div className="flex items-baseline gap-2">
          <h2 className="text-sm font-semibold">Decision Center</h2>
          <span className="text-xs text-muted">
            {symbol} · {timeframe}
          </span>
        </div>
        <button
          onClick={() => void load()}
          disabled={loading}
          className="text-xs px-2 py-1 rounded border border-border text-muted
            hover:text-foreground hover:border-muted disabled:opacity-50 transition-colors"
        >
          {loading ? "checking…" : "refresh"}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {loading && !ticket ? (
          <div className="text-sm text-muted">Checking for a setup…</div>
        ) : !ticket ? null : isTradeTicket(ticket) ? (
          <TicketCard
            ticket={ticket}
            journalState={journalState}
            journalAction={journalAction}
            journalError={journalError}
            onJournal={(action) => void journal(action)}
            onChoose={() => setJournalState("choosing")}
            onCancel={() => setJournalState("idle")}
          />
        ) : (
          <NoTradeCard ticket={ticket} />
        )}
      </div>
    </div>
  );
}

const REFUSAL_COPY: Record<
  string,
  { headline: string; hint?: string }
> = {
  engine_down: {
    headline: "Engine unavailable",
    hint: "Start the live worker. The dashboard is up, but no ticket can be issued without it.",
  },
  no_profile: {
    headline: "No risk profile",
    hint: "Complete onboarding so the app knows the budget it may size against.",
  },
  unknown_instrument: { headline: "Unknown instrument" },
  not_in_universe: { headline: "Not in the universe" },
  no_data: {
    headline: "No price data yet",
    hint: "The venue is connected but no bar has arrived yet. This is a normal startup state.",
  },
  feed_not_configured: {
    headline: "Feed not connected",
    hint: "Add this venue's credentials to the worker's .env and restart it.",
  },
  market_closed: {
    headline: "Market closed",
    hint: "The session is closed, so no fresh price is expected. The app will start measuring when it opens.",
  },
  quote_too_old: {
    headline: "Data is stale",
    hint: "The session is open but the quote is older than the safety window. Not acting on it.",
  },
  no_champion: {
    headline: "No promoted strategy",
    hint: "A champion must pass the promotion gate. The gate is not lowered to fill the gap.",
  },
  no_signal: { headline: "Waiting" },
  no_stop: {
    headline: "No valid stop",
    hint: "A setup without a stop is not a trade. It is refused.",
  },
  risk_blocked: {
    headline: "Risk limit reached",
    hint: "A position cap or the daily ticket limit refused the size.",
  },
  rr_below_floor: { headline: "Reward:risk too low" },
};

function NoTradeCard({ ticket }: { ticket: NonTicket }) {
  const reasonCode = ticket.reason_code ?? "";
  const copy = REFUSAL_COPY[reasonCode];

  const tone =
    ticket.status === "engine_unavailable" ||
    reasonCode === "feed_not_configured" ||
    reasonCode === "quote_too_old"
      ? "border-degraded/40 bg-degraded/5"
      : "border-border bg-panel";

  const headline =
    copy?.headline ??
    (ticket.status === "engine_unavailable"
      ? "Engine unavailable"
      : ticket.status === "stale"
        ? "Data is stale"
        : ticket.status === "no_match"
          ? "No registered setup"
          : "Waiting");

  return (
    <div className={`rounded-lg border p-4 ${tone}`}>
      <div className="flex items-baseline justify-between gap-2 mb-1">
        <div className="text-sm font-semibold">{headline}</div>
        {reasonCode && (
          <span className="text-[10px] uppercase tracking-wider text-muted/70 tnum">
            {reasonCode}
          </span>
        )}
      </div>
      <p className="text-sm text-muted leading-relaxed">{ticket.reason}</p>

      {copy?.hint && (
        <p className="text-xs text-muted/80 mt-2 leading-relaxed">{copy.hint}</p>
      )}

      {ticket.venue && (
        <p className="text-[11px] text-muted mt-2">
          Venue: <span className="text-foreground">{ticket.venue}</span>
        </p>
      )}

      {ticket.status === "engine_unavailable" &&
        ticket.engine?.age_seconds != null && (
          <p className="text-xs text-muted mt-2">
            Last heartbeat {Math.round(ticket.engine.age_seconds)}s ago.
          </p>
        )}

      {ticket.status === "stale" && ticket.age_seconds != null && (
        <p className="text-xs text-muted mt-2 tnum">
          Price age {Math.round(ticket.age_seconds)}s
          {ticket.data_as_of
            ? ` · captured ${new Date(ticket.data_as_of).toISOString()}`
            : ""}
        </p>
      )}

      <p className="text-[11px] text-muted/70 mt-3">
        This is a normal state, not an error. The app does not offer a setup it
        cannot measure.
      </p>
    </div>
  );
}

function TicketCard({
  ticket,
  journalState,
  journalAction,
  journalError,
  onJournal,
  onChoose,
  onCancel,
}: {
  ticket: TradeTicket;
  journalState: "idle" | "choosing" | "saving" | "saved" | "error";
  journalAction: string | null;
  journalError: string | null;
  onJournal: (action: "took" | "skipped") => void;
  onChoose: () => void;
  onCancel: () => void;
}) {
  const isBuy = ticket.side === "buy";
  const sideColor = isBuy ? "text-up" : "text-down";

  return (
    <div className="space-y-4">
      {/* The signal */}
      <div className="rounded-lg border border-border bg-panel p-4">
        <div className="flex items-baseline justify-between mb-3">
          <span className={`text-2xl font-bold ${sideColor}`}>
            {ticket.side.toUpperCase()}
          </span>
          {ticket.confidence != null && (
            <span className="text-xs text-muted tnum">
              confidence {(ticket.confidence * 100).toFixed(0)}%
            </span>
          )}
        </div>

        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
          <Row
            label="Entry"
            value={
              ticket.entry_low != null && ticket.entry_high != null
                ? `${fmt(ticket.entry_low)} – ${fmt(ticket.entry_high)}`
                : fmt(ticket.entry_price)
            }
          />
          <Row label="Stop" value={fmt(ticket.stop_price)} tone="text-down" />
          <Row label="Target 1" value={fmt(ticket.target_price)} tone="text-up" />
          {ticket.target_2_price != null && (
            <Row label="Target 2" value={fmt(ticket.target_2_price)} tone="text-up" />
          )}
          <Row
            label="Size"
            value={
              ticket.size_units != null
                ? `${fmt(ticket.size_units, 4)} @ ${fmt(ticket.size_notional, 0)}`
                : "set your capital"
            }
          />
          <Row
            label="Risk"
            value={
              ticket.risk_amount != null
                ? `${fmt(ticket.risk_amount, 2)} (${fmt(ticket.stop_distance)})`
                : "—"
            }
          />
          <Row label="R:R" value={fmt(ticket.reward_risk)} />
          <Row
            label="Data"
            value={`${ticket.data_source} · ${ticket.data_quality}`}
            tone="text-muted"
          />
        </dl>
      </div>

      {/* The odds band */}
      <OddsBandCard ticket={ticket} />

      {/* The evidence */}
      <div className="rounded-lg border border-border bg-panel p-4">
        <div className="text-xs font-semibold uppercase tracking-wider text-muted mb-2">
          Why — {ticket.strategy_name} v{ticket.version}
        </div>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
          <Row label="Trades" value={fmt(ticket.metrics.trades, 0)} />
          <Row label="Win rate" value={fmtPct(ticket.metrics.win_rate)} />
          <Row label="Expectancy" value={fmt(ticket.metrics.expectancy, 4)} />
          <Row label="Sharpe" value={fmt(ticket.metrics.sharpe)} />
          <Row label="Max DD" value={fmtPct(ticket.metrics.max_drawdown)} />
          <Row label="DSR / PBO" value={`${fmt(ticket.metrics.dsr)} / ${fmt(ticket.metrics.pbo)}`} />
          <Row label="Trials" value={fmt(ticket.metrics.n_trials, 0)} />
          <Row
            label="Period"
            value={
              ticket.metrics.period_start
                ? `${ticket.metrics.period_start} → ${ticket.metrics.period_end}`
                : "—"
            }
          />
          {ticket.metrics.benchmark && (
            <Row
              label="Benchmark"
              value={`${ticket.metrics.benchmark} ${fmtPct(ticket.metrics.benchmark_return)}`}
              tone="text-muted"
            />
          )}
        </dl>
        {ticket.metrics.note && (
          <p className="text-[11px] text-muted mt-2">{ticket.metrics.note}</p>
        )}
      </div>

      {/* Warnings */}
      {ticket.warnings.length > 0 && (
        <div className="rounded-lg border border-degraded/30 bg-degraded/5 p-3">
          <div className="text-xs font-semibold uppercase tracking-wider text-degraded mb-1.5">
            Warnings
          </div>
          <ul className="space-y-1">
            {ticket.warnings.map((w, i) => (
              <li key={i} className="text-xs text-muted">
                {w.text}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* The action */}
      <div className="space-y-2">
        {journalState === "saved" ? (
          <div className="flex flex-wrap items-center gap-2">
            <span className="px-4 py-2 rounded text-sm font-medium bg-panel-2 text-muted border border-border">
              Journaled · {journalAction === "took" ? "took it" : "passed"}
            </span>
            <a
              href="/journal"
              className="text-xs text-accent hover:underline"
            >
              View journal
            </a>
          </div>
        ) : journalState === "choosing" || journalState === "saving" ? (
          <div className="rounded-lg border border-border bg-panel-2 p-3">
            <p className="text-xs text-muted mb-2">
              Record what you actually did. The app does not execute the trade.
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <button
                onClick={() => onJournal("took")}
                disabled={journalState === "saving"}
                className="px-3 py-1.5 rounded text-xs font-medium bg-accent text-white hover:opacity-90 disabled:opacity-50"
              >
                {journalState === "saving" ? "saving…" : "I took it"}
              </button>
              <button
                onClick={() => onJournal("skipped")}
                disabled={journalState === "saving"}
                className="px-3 py-1.5 rounded text-xs font-medium border border-border text-muted hover:text-foreground disabled:opacity-50"
              >
                I passed
              </button>
              <button
                onClick={onCancel}
                disabled={journalState === "saving"}
                className="text-[11px] text-muted hover:text-foreground disabled:opacity-50"
              >
                cancel
              </button>
            </div>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <button
              onClick={onChoose}
              className="px-4 py-2 rounded text-sm font-medium transition-colors bg-accent text-white hover:opacity-90"
            >
              Journal this ticket
            </button>
            <span className="text-[11px] text-muted">
              The app advises. You decide.
            </span>
          </div>
        )}

        {journalState === "error" && (
          <p className="text-xs text-down">
            {journalError ?? "Could not journal this ticket."}
          </p>
        )}
      </div>

      <p className="text-[10px] text-muted/60 leading-relaxed">
        Signal as of {new Date(ticket.signal_bar_close_time).toISOString()} ·
        price as of {new Date(ticket.data_as_of).toISOString()}. Not financial
        advice.
      </p>
    </div>
  );
}

function OddsBandCard({ ticket }: { ticket: TradeTicket }) {
  const band = ticket.odds_band;
  if (
    band?.target_first == null &&
    band?.stop_first == null &&
    band?.neither == null
  ) {
    return null;
  }

  const target = band.target_first ?? 0;
  const stop = band.stop_first ?? 0;
  const neither = band.neither ?? 0;
  const total = Math.max(target + stop + neither, 0.0001);

  return (
    <div className="rounded-lg border border-border bg-panel p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-muted">
          Odds band
        </span>
        {band.verdict && (
          <span className="text-xs text-foreground">{band.verdict}</span>
        )}
      </div>

      {/* The bar */}
      <div className="flex h-2 rounded overflow-hidden bg-panel-2">
        <div className="bg-up" style={{ width: `${(target / total) * 100}%` }} />
        <div className="bg-down" style={{ width: `${(stop / total) * 100}%` }} />
        <div className="bg-border" style={{ width: `${(neither / total) * 100}%` }} />
      </div>

      <div className="flex justify-between text-[11px] mt-1.5">
        <span className="text-up">target {(target * 100).toFixed(1)}%</span>
        <span className="text-down">stop {(stop * 100).toFixed(1)}%</span>
        <span className="text-muted">open {(neither * 100).toFixed(1)}%</span>
      </div>

      {band.break_even != null && (
        <p className="text-[11px] text-muted mt-2">
          Break-even at this R is {(band.break_even * 100).toFixed(1)}%.
          {band.ci_low != null && band.ci_high != null && (
            <>
              {" "}
              95% CI on target: [{(band.ci_low * 100).toFixed(1)}%,{" "}
              {(band.ci_high * 100).toFixed(1)}%].
            </>
          )}
        </p>
      )}

      {band.historical_fires != null && band.historical_fires > 0 && (
        <p className="text-[11px] text-muted mt-1">
          This setup fired {band.historical_fires} time
          {band.historical_fires === 1 ? "" : "s"} before;{" "}
          {band.historical_target ?? 0} reached the target.
        </p>
      )}

      {band.method && (
        <p className="text-[10px] text-muted/60 mt-2">{band.method}</p>
      )}
    </div>
  );
}

function Row({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <dt className="text-muted shrink-0">{label}</dt>
      <dd className={`tnum text-right ${tone ?? ""}`}>{value}</dd>
    </div>
  );
}
