import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { type TicketResponse } from "@/lib/types";

/**
 * GET /api/ticket?instrument=<symbol>&timeframe=<tf>
 *
 * The ONLY route that produces trade advice. It calls the `get_ticket()`
 * Postgres function, which is the deterministic core: it applies the
 * freshness gate, the session check, the news-risk check and the position
 * caps. This route adds no logic of its own.
 *
 * It deliberately does NOT:
 *   * call a language model to decide anything
 *   * compute a size, a stop or a target
 *   * place an order (there is no order endpoint in this codebase)
 */
export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const symbol = searchParams.get("instrument");
  const timeframe = searchParams.get("timeframe") ?? "1h";

  if (!symbol) {
    return NextResponse.json(
      { status: "no_match", reason: "No instrument given." },
      { status: 400 },
    );
  }

  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) {
    return NextResponse.json(
      { status: "no_match", reason: "Not signed in." },
      { status: 401 },
    );
  }

  // Resolve the symbol to its id. The user may only look at instruments in
  // the universe; RLS handles the rest.
  const { data: instrument, error: instrumentError } = await supabase
    .from("instruments")
    .select("id")
    .eq("symbol", symbol.toUpperCase())
    .eq("in_universe", true)
    .maybeSingle();

  if (instrumentError) {
    console.error("instrument lookup failed", instrumentError);
    return NextResponse.json(
      { status: "no_match", reason: "Could not look up that instrument." },
      { status: 500 },
    );
  }

  if (!instrument) {
    return NextResponse.json({
      status: "no_match",
      reason: `${symbol.toUpperCase()} is not in the trading universe.`,
    } satisfies TicketResponse);
  }

  const { data, error } = await supabase.rpc("get_ticket", {
    p_user_id: user.id,
    p_instrument: instrument.id,
    p_timeframe: timeframe,
  });

  if (error) {
    console.error("get_ticket failed", error);
    return NextResponse.json(
      {
        status: "engine_unavailable",
        reason: "The ticket engine did not respond.",
      } satisfies TicketResponse,
      { status: 500 },
    );
  }

  return NextResponse.json(data ?? {
    status: "engine_unavailable",
    reason: "The ticket engine returned nothing.",
  });
}
