import { createClient } from "@/lib/supabase/server";
import { redirect } from "next/navigation";
import type { FeedHealth } from "@/lib/types";

export interface Instrument {
  id: string;
  symbol: string;
  venue: string;
  asset_class: string;
  display_name: string | null;
  data_quality: string;
  primary_source: string | null;
}

export interface Quote {
  instrument_id: string;
  bid: number | null;
  ask: number | null;
  last: number | null;
  change_pct: number | null;
  volume: number | null;
  source: string;
  official: boolean;
  as_of: string;
}

export interface EngineState {
  status: "live" | "stale" | "never_seen" | null;
  last_seen_at: string | null;
  age_seconds: number | null;
  worker_id: string | null;
  version: string | null;
  streams: Record<string, { connected: boolean; reconnects: number }>;
  lag_ms: Record<string, number | null>;
}

/**
 * Require a signed-in user.
 *
 * This calls `cookies()` UNCONDITIONALLY, which is what tells the framework
 * the page is request-time. If the session read were behind an environment
 * check, the build could prerender the page and bake in whatever the
 * environment looked like at build time.
 */
export async function requireUser(): Promise<string> {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  if (!url || !key) {
    // No database configured. Let the page render its "not configured"
    // explanation rather than redirecting to a login page that cannot work.
    return "";
  }

  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) {
    redirect("/login");
  }

  return user.id;
}

/**
 * Load the Command Center's initial state.
 *
 * This runs on the server with the user's session, so RLS decides what is
 * visible.
 */
export async function loadCommandCenter(): Promise<{
  instruments: Instrument[];
  quotes: Quote[];
  engine: EngineState | null;
  feeds: FeedHealth | null;
  configured: boolean;
  error: string | null;
}> {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  if (!url || !key) {
    return {
      instruments: [],
      quotes: [],
      engine: null,
      feeds: null,
      configured: false,
      error:
        "NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY are not set.",
    };
  }

  try {
    const supabase = await createClient();

    const [instrumentsRes, quotesRes, engineRes, feedsRes] = await Promise.all([
      supabase
        .from("instruments")
        .select(
          "id,symbol,venue,asset_class,display_name,data_quality,primary_source",
        )
        .eq("in_universe", true)
        .order("symbol"),
      supabase.from("latest_quotes").select("*"),
      supabase.rpc("engine_status"),
      supabase.rpc("feed_health"),
    ]);

    const hb = engineRes.data as
      | {
          status: "live" | "stale" | "never_seen" | null;
          last_seen_at: string | null;
          age_seconds: number | null;
          worker_id: string | null;
          version: string | null;
          streams: Record<string, { connected: boolean; reconnects: number }>;
          lag_ms: Record<string, number | null>;
        }
      | null;

    const engine: EngineState = hb
      ? {
          status: hb.status,
          last_seen_at: hb.last_seen_at,
          age_seconds: hb.age_seconds,
          worker_id: hb.worker_id,
          version: hb.version,
          streams: hb.streams ?? {},
          lag_ms: hb.lag_ms ?? {},
        }
      : {
          status: "never_seen",
          last_seen_at: null,
          age_seconds: null,
          worker_id: null,
          version: null,
          streams: {},
          lag_ms: {},
        };

    return {
      instruments: (instrumentsRes.data ?? []) as Instrument[],
      quotes: (quotesRes.data ?? []) as Quote[],
      engine,
      feeds: (feedsRes.error ? null : (feedsRes.data as FeedHealth | null)) ?? null,
      configured: true,
      error: null,
    };
  } catch (err) {
    console.error("command center load failed", err);
    return {
      instruments: [],
      quotes: [],
      engine: null,
      feeds: null,
      configured: true,
      error: err instanceof Error ? err.message : "Could not load the dashboard.",
    };
  }
}

/**
 * Per-venue feed status.
 *
 * `feed_health()` distinguishes "the venue was never connected" from "the
 * venue went quiet". The first is a setup state, the second is an outage, and
 * the UI must never call either of them "stale prices".
 */
export async function loadFeedHealth(): Promise<FeedHealth | null> {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) return null;

  try {
    const supabase = await createClient();
    const { data, error } = await supabase.rpc("feed_health");
    if (error) {
      console.error("feed_health failed", error);
      return null;
    }
    return (data as FeedHealth | null) ?? null;
  } catch (err) {
    console.error("feed_health failed", err);
    return null;
  }
}

/* --------------------------------------------------------------- markets */

export async function loadInstrument(
  symbol: string,
): Promise<Instrument | null> {
  const supabase = await createClient();
  const { data, error } = await supabase
    .from("instruments")
    .select(
      "id,symbol,venue,asset_class,display_name,data_quality,primary_source",
    )
    .eq("symbol", symbol.toUpperCase())
    .eq("in_universe", true)
    .maybeSingle();

  if (error) {
    console.error("instrument lookup failed", error);
    return null;
  }
  return (data as Instrument | null) ?? null;
}

/* --------------------------------------------------------------- journal */

export interface JournalEntry {
  id: string;
  ticket_id: string | null;
  instrument_id: string | null;
  action: string;
  note: string | null;
  entry_price: number | null;
  exit_price: number | null;
  quantity: number | null;
  pnl_usd: number | null;
  r_multiple: number | null;
  outcome_state: string | null;
  created_at: string;
}

export interface JournalTicket {
  id: string;
  instrument_id: string | null;
  timeframe: string;
  side: string;
  entry_low: number | null;
  entry_high: number | null;
  stop_price: number | null;
  target_price: number | null;
  size_units: number | null;
  risk_amount: number | null;
  reward_risk: number | null;
}

export async function loadJournal(): Promise<{
  entries: JournalEntry[];
  tickets: Record<string, JournalTicket>;
  instruments: Record<string, Instrument>;
  error: string | null;
}> {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) {
    return { entries: [], tickets: {}, instruments: {}, error: "Not signed in." };
  }

  const [journalRes, ticketRes, instrumentRes] = await Promise.all([
    supabase
      .from("journal")
      .select(
        "id,ticket_id,instrument_id,action,note,entry_price,exit_price,quantity,pnl_usd,r_multiple,outcome_state,created_at",
      )
      .eq("user_id", user.id)
      .order("created_at", { ascending: false })
      .limit(200),
    supabase
      .from("tickets")
      .select(
        "id,instrument_id,timeframe,side,entry_low,entry_high,stop_price,target_price,size_units,risk_amount,reward_risk",
      )
      .eq("user_id", user.id)
      .order("created_at", { ascending: false })
      .limit(200),
    supabase
      .from("instruments")
      .select(
        "id,symbol,venue,asset_class,display_name,data_quality,primary_source",
      )
      .eq("in_universe", true),
  ]);

  const firstError =
    journalRes.error ?? ticketRes.error ?? instrumentRes.error ?? null;

  const instruments = Object.fromEntries(
    ((instrumentRes.data ?? []) as Instrument[]).map((i) => [i.id, i]),
  );
  const tickets = Object.fromEntries(
    ((ticketRes.data ?? []) as JournalTicket[]).map((t) => [t.id, t]),
  );

  return {
    entries: (journalRes.data ?? []) as JournalEntry[],
    tickets,
    instruments,
    error: firstError ? firstError.message : null,
  };
}

/* --------------------------------------------------------------- profile */

export interface Profile {
  user_id: string;
  role: string;
  display_name: string | null;
  capital_usd: number | null;
  risk_per_trade_pct: number;
  max_position_pct: number;
  max_gross_pct: number;
  max_correlated_pct: number;
  max_concurrent: number;
  max_tickets_per_day: number;
  venues: string[];
  timezone: string;
  updated_at: string;
}

export async function loadProfile(): Promise<{
  profile: Profile | null;
  error: string | null;
}> {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return { profile: null, error: "Not signed in." };

  const { data, error } = await supabase
    .from("user_profiles")
    .select("*")
    .eq("user_id", user.id)
    .maybeSingle();

  if (error) {
    console.error("profile load failed", error);
    return { profile: null, error: error.message };
  }
  return { profile: (data as Profile | null) ?? null, error: null };
}

/* ------------------------------------------------------------ strategies */

export interface StrategyRow {
  id: string;
  name: string;
  family: string;
  asset_class: string;
  description: string | null;
  template_id: string | null;
  is_template: boolean;
  created_at: string;
}

export interface StrategyVersionRow {
  id: string;
  strategy_id: string;
  version: number;
  status: string;
  timeframes: string[];
  params: Record<string, unknown>;
}

export interface ChampionRow {
  asset_class: string;
  timeframe: string;
  version_id: string;
  promoted_at: string;
  active: boolean;
}

export interface PerformanceRow {
  version_id: string;
  asset_class: string;
  timeframe: string;
  trades: number;
  sharpe: number | null;
  cagr: number | null;
  calmar: number | null;
  max_drawdown: number | null;
  dsr: number | null;
  pbo: number | null;
  neighbour_pass_rate: number | null;
  ai_feature_gain_sharpe: number | null;
  n_trials: number;
  benchmark: string | null;
}

export interface GatePolicy {
  version: string;
  thresholds: Record<string, number>;
  notes: string | null;
}

export async function loadStrategies(): Promise<{
  strategies: StrategyRow[];
  versions: StrategyVersionRow[];
  champions: ChampionRow[];
  performance: PerformanceRow[];
  gate: GatePolicy | null;
  error: string | null;
}> {
  const supabase = await createClient();

  const [strategyRes, versionRes, championRes, performanceRes, gateRes] =
    await Promise.all([
      supabase.from("strategies").select("*").order("created_at"),
      supabase.from("strategy_versions").select("*").order("version"),
      supabase.from("champions").select("*").eq("active", true),
      supabase.from("performance").select("*"),
      supabase.from("gate_policy").select("*").eq("active", true).maybeSingle(),
    ]);

  const firstError =
    strategyRes.error ??
    versionRes.error ??
    championRes.error ??
    performanceRes.error ??
    gateRes.error ??
    null;

  return {
    strategies: (strategyRes.data ?? []) as StrategyRow[],
    versions: (versionRes.data ?? []) as StrategyVersionRow[],
    champions: (championRes.data ?? []) as ChampionRow[],
    performance: (performanceRes.data ?? []) as PerformanceRow[],
    gate: (gateRes.data as GatePolicy | null) ?? null,
    error: firstError ? firstError.message : null,
  };
}
