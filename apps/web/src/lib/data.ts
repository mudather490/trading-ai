import { createClient } from "@/lib/supabase/server";
import { redirect } from "next/navigation";

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
      configured: false,
      error:
        "NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY are not set.",
    };
  }

  try {
    const supabase = await createClient();

    const [instrumentsRes, quotesRes, heartbeatRes] = await Promise.all([
      supabase
        .from("instruments")
        .select(
          "id,symbol,venue,asset_class,display_name,data_quality,primary_source",
        )
        .eq("in_universe", true)
        .order("symbol"),
      supabase.from("latest_quotes").select("*"),
      supabase
        .from("worker_heartbeat")
        .select("*")
        .order("last_seen_at", { ascending: false })
        .limit(1),
    ]);

    const hb = heartbeatRes.data?.[0] as
      | {
          worker_id: string;
          version: string;
          last_seen_at: string;
          streams: Record<string, { connected: boolean; reconnects: number }>;
          lag_ms: Record<string, number | null>;
        }
      | undefined;

    let engine: EngineState | null = null;
    if (hb) {
      const age = Math.round(
        (Date.now() - new Date(hb.last_seen_at).getTime()) / 1000,
      );
      engine = {
        status: age > 120 ? "stale" : "live",
        last_seen_at: hb.last_seen_at,
        age_seconds: age,
        worker_id: hb.worker_id,
        version: hb.version,
        streams: hb.streams ?? {},
        lag_ms: hb.lag_ms ?? {},
      };
    } else {
      engine = {
        status: "never_seen",
        last_seen_at: null,
        age_seconds: null,
        worker_id: null,
        version: null,
        streams: {},
        lag_ms: {},
      };
    }

    return {
      instruments: (instrumentsRes.data ?? []) as Instrument[],
      quotes: (quotesRes.data ?? []) as Quote[],
      engine,
      configured: true,
      error: null,
    };
  } catch (err) {
    console.error("command center load failed", err);
    return {
      instruments: [],
      quotes: [],
      engine: null,
      configured: true,
      error: err instanceof Error ? err.message : "Could not load the dashboard.",
    };
  }
}
