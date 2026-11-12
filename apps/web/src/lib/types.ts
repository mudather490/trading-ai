/**
 * The ticket contract.
 *
 * These types mirror the `get_ticket()` Postgres function exactly. The
 * function returns one of five statuses, and every one of them is a SUCCESS
 * state — none of them is an error.
 *
 *  ticket             a complete trade ticket
 *  wait               nothing qualifies right now, and here is why
 *  no_match           no registered setup exists for this instrument
 *  stale              the price data is too old to act on
 *  engine_unavailable the live worker is not running
 */

export type TicketStatus =
  | "ticket"
  | "wait"
  | "no_match"
  | "stale"
  | "engine_unavailable";

export type Side = "buy" | "sell" | "flat";

export interface TicketMetrics {
  trades?: number | null;
  win_rate?: number | null;
  expectancy?: number | null;
  sharpe?: number | null;
  max_drawdown?: number | null;
  dsr?: number | null;
  pbo?: number | null;
  n_trials?: number | null;
  period_start?: string | null;
  period_end?: string | null;
  benchmark?: string | null;
  benchmark_return?: number | null;
  data_source?: string | null;
  note?: string;
}

export interface TicketWarning {
  kind: string;
  severity: string;
  text: string;
  at?: string;
}

export interface OddsBand {
  /** Share of replays that hit the target first. */
  target_first?: number | null;
  /** Share of replays that hit the stop first. */
  stop_first?: number | null;
  /** Share of replays still open at the exit time. */
  neither?: number | null;
  /** The break-even hit rate at this R multiple. */
  break_even?: number | null;
  /** "leans toward the target" | "close to a coin flip" | ... */
  verdict?: string | null;
  /** 95% confidence interval on the target-first share. */
  ci_low?: number | null;
  ci_high?: number | null;
  /** How many past times this exact setup fired. */
  historical_fires?: number | null;
  /** How many of those hit the target. */
  historical_target?: number | null;
  /** The engine and seed, so the number can be reproduced. */
  method?: string | null;
  seed?: number | null;
}

export interface EngineStatus {
  status: "live" | "stale" | "never_seen" | null;
  last_seen_at?: string | null;
  age_seconds?: number | null;
  worker_id?: string | null;
  version?: string | null;
  streams?: Record<string, { connected: boolean; reconnects: number }>;
  lag_ms?: Record<string, number | null>;
}

export interface TradeTicket {
  status: "ticket";
  instrument: string;
  instrument_id: string;
  venue: string;
  asset_class: string;
  timeframe: string;
  side: Side;
  rule_id?: string;
  entry_price: number | null;
  entry_low: number | null;
  entry_high: number | null;
  stop_price: number | null;
  target_price: number | null;
  target_2_price: number | null;
  size_units: number | null;
  size_notional: number | null;
  risk_amount: number | null;
  stop_distance: number | null;
  reward_risk: number | null;
  confidence: number | null;
  strategy_id: string;
  strategy_name: string;
  strategy_family: string;
  version_id: string;
  version: number;
  metrics: TicketMetrics;
  odds_band: OddsBand;
  warnings: TicketWarning[];
  data_source: string;
  data_quality: string;
  data_as_of: string;
  signal_bar_close_time: string;
  signal_id: string;
  engine: EngineStatus;
}

export interface NonTicket {
  status: Exclude<TicketStatus, "ticket">;
  reason: string;
  /**
   * Machine-readable refusal. Additive to `status`; the five status values are
   * unchanged (I-8). This is what lets the UI say "the feed was never
   * connected" instead of "stale".
   */
  reason_code?: string | null;
  engine?: EngineStatus;
  instrument?: string;
  venue?: string;
  /** Present on a `stale` result: how old the price was. */
  age_seconds?: number | null;
  /** Present on a `stale` result: when the data was captured. */
  data_as_of?: string | null;
  /** Present on a `wait` result: the reward:risk that was refused. */
  reward_risk?: number | null;
  /** Present on a `wait` result: the floor that was not met. */
  rr_floor?: number | null;
  /** Present on a `wait` result: the risk budget in the account currency. */
  risk_budget_usd?: number | null;
  /** Present on a `wait` result: how far the stop was from the entry. */
  stop_distance?: number | null;
  /** Present on a `wait` result: how many tickets were issued today. */
  tickets_today?: number | null;
  /** Present on a `wait` result: how many positions are already open. */
  open_tickets?: number | null;
}

export type TicketResponse = TradeTicket | NonTicket;

export function isTradeTicket(t: TicketResponse | null): t is TradeTicket {
  return t?.status === "ticket";
}

/** Format a number for display. Nulls become an em dash, never "null". */
export function fmt(
  value: number | string | null | undefined,
  decimals = 2,
): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = typeof value === "string" ? Number(value) : value;
  if (Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export function fmtPct(
  value: number | string | null | undefined,
  decimals = 2,
): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = typeof value === "string" ? Number(value) : value;
  if (Number.isNaN(n)) return "—";
  return `${(n * 100).toFixed(decimals)}%`;
}

export function fmtAge(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}

/* ------------------------------------------------------------------ feeds */

export type VenueStatus =
  | "live"
  | "stale"
  | "market_closed"
  | "no_data"
  | "down"
  | "engine_down"
  | "not_configured";

export interface VenueHealth {
  venue: string;
  enabled: boolean;
  configured: boolean;
  connected: boolean;
  status: VenueStatus;
  hint: string | null;
  symbols: number;
  with_quotes: number;
  data_quality: string;
  last_tick_at: string | null;
  age_seconds: number | null;
  // The last provider contract check. null means nobody has checked this
  // venue's response shape yet, which is NOT the same as a failed check.
  contract_ok: boolean | null;
  contract_checked_at: string | null;
}

export interface FeedHealth {
  as_of: string;
  engine: EngineStatus | null;
  venues: VenueHealth[];
}

