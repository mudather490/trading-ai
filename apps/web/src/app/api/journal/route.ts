import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { isTradeTicket, type TicketResponse } from "@/lib/types";

/**
 * POST /api/journal
 *
 * Snapshot a live ticket into the journal.
 *
 * Two things matter here:
 *
 *   1. The client is not trusted. The ticket is re-computed server-side with
 *      `get_ticket()`; if it has expired or no longer qualifies, the request
 *      is refused. A journal row can never describe a ticket the engine would
 *      not issue now.
 *   2. The action is the user's. The app records `took` or `skipped`; it never
 *      assumes that reading a ticket means executing it.
 */
const ACTIONS = new Set(["took", "skipped"]);

export async function POST(request: Request) {
  let body: { instrument?: string; timeframe?: string; action?: string };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: "Invalid JSON." }, { status: 400 });
  }

  const symbol = (body.instrument ?? "").toUpperCase();
  const timeframe = body.timeframe ?? "1h";
  const action = body.action ?? "";

  if (!symbol) {
    return NextResponse.json({ error: "No instrument given." }, { status: 400 });
  }
  if (!ACTIONS.has(action)) {
    return NextResponse.json(
      { error: "Action must be 'took' or 'skipped'." },
      { status: 400 },
    );
  }

  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) {
    return NextResponse.json({ error: "Not signed in." }, { status: 401 });
  }

  const { data: instrument, error: instrumentError } = await supabase
    .from("instruments")
    .select("id")
    .eq("symbol", symbol)
    .eq("in_universe", true)
    .maybeSingle();

  if (instrumentError) {
    console.error("journal instrument lookup failed", instrumentError);
    return NextResponse.json({ error: "Lookup failed." }, { status: 500 });
  }
  if (!instrument) {
    return NextResponse.json(
      { error: `${symbol} is not in the universe.` },
      { status: 404 },
    );
  }

  // Recompute. The ticket must still be issuable at the moment of journaling.
  const { data: ticketData, error: ticketError } = await supabase.rpc(
    "get_ticket",
    {
      p_user_id: user.id,
      p_instrument: instrument.id,
      p_timeframe: timeframe,
    },
  );

  if (ticketError) {
    console.error("journal get_ticket failed", ticketError);
    return NextResponse.json(
      { error: "The ticket engine did not respond." },
      { status: 500 },
    );
  }

  const ticket = ticketData as TicketResponse | null;
  if (!ticket || !isTradeTicket(ticket)) {
    return NextResponse.json(
      {
        error: "This ticket is no longer actionable.",
        status: ticket?.status ?? "engine_unavailable",
        reason_code:
          ticket && "reason_code" in ticket ? ticket.reason_code : null,
        reason: ticket?.reason ?? "No ticket was returned.",
      },
      { status: 409 },
    );
  }

  const { data: ticketRow, error: insertError } = await supabase
    .from("tickets")
    .insert({
      user_id: user.id,
      signal_id: ticket.signal_id,
      instrument_id: ticket.instrument_id,
      timeframe: ticket.timeframe,
      side: ticket.side,
      entry_low: ticket.entry_low,
      entry_high: ticket.entry_high,
      stop_price: ticket.stop_price,
      target_price: ticket.target_price,
      target_2_price: ticket.target_2_price,
      size_units: ticket.size_units,
      size_notional: ticket.size_notional,
      risk_amount: ticket.risk_amount,
      reward_risk: ticket.reward_risk,
      confidence: ticket.confidence,
      metrics: ticket.metrics,
      strategy_id: ticket.strategy_id,
      version_id: ticket.version_id,
      data_source: ticket.data_source,
      data_quality: ticket.data_quality,
      data_as_of: ticket.data_as_of,
      odds_band: ticket.odds_band,
      warnings: ticket.warnings,
      state: "journaled",
    })
    .select("id")
    .maybeSingle();

  if (insertError || !ticketRow) {
    console.error("ticket snapshot insert failed", insertError);
    return NextResponse.json(
      { error: "Could not store the ticket snapshot." },
      { status: 500 },
    );
  }

  // Idempotent: a double click must not create two journal rows.
  const { data: existing } = await supabase
    .from("journal")
    .select("id")
    .eq("user_id", user.id)
    .eq("ticket_id", ticketRow.id)
    .maybeSingle();

  if (existing?.id) {
    return NextResponse.json({
      ok: true,
      ticket_id: ticketRow.id,
      journal_id: existing.id,
      action,
      already: true,
    });
  }

  const { data: journalRow, error: journalError } = await supabase
    .from("journal")
    .insert({
      user_id: user.id,
      ticket_id: ticketRow.id,
      instrument_id: ticket.instrument_id,
      action,
      entry_price: ticket.entry_price,
      quantity: ticket.size_units,
    })
    .select("id")
    .maybeSingle();

  if (journalError || !journalRow) {
    console.error("journal insert failed", journalError);
    return NextResponse.json(
      { error: "The ticket was saved but the journal entry failed." },
      { status: 500 },
    );
  }

  return NextResponse.json({
    ok: true,
    ticket_id: ticketRow.id,
    journal_id: journalRow.id,
    action,
  });
}
