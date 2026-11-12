import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";

/**
 * GET  /api/settings/profile   read the caller's risk profile
 * POST /api/settings/profile   clamp, persist, and audit it
 *
 * The clamp is not in this file. It is in `update_my_profile()` in Postgres,
 * next to the data and next to the audit log. A serverless function is a fine
 * place to validate for a friendly message, but it is the wrong place to be
 * the last line of defence: the database must refuse a bad number even if
 * every client above it is wrong.
 */
interface ProfileInput {
  display_name?: string | null;
  capital_usd?: number | string | null;
  risk_per_trade_pct?: number | string | null;
  max_position_pct?: number | string | null;
  max_gross_pct?: number | string | null;
  max_correlated_pct?: number | string | null;
  max_concurrent?: number | string | null;
  max_tickets_per_day?: number | string | null;
  timezone?: string | null;
}

function num(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : Number.NaN;
}

export async function GET() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) {
    return NextResponse.json({ error: "Not signed in." }, { status: 401 });
  }

  const { data, error } = await supabase
    .from("user_profiles")
    .select("*")
    .eq("user_id", user.id)
    .maybeSingle();

  if (error) {
    console.error("profile read failed", error);
    return NextResponse.json({ error: "Could not read the profile." }, { status: 500 });
  }
  return NextResponse.json({ profile: data ?? null });
}

export async function POST(request: Request) {
  let body: ProfileInput;
  try {
    body = (await request.json()) as ProfileInput;
  } catch {
    return NextResponse.json({ error: "Invalid JSON." }, { status: 400 });
  }

  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) {
    return NextResponse.json({ error: "Not signed in." }, { status: 401 });
  }

  const capital = num(body.capital_usd);
  const risk = num(body.risk_per_trade_pct);
  const position = num(body.max_position_pct);
  const gross = num(body.max_gross_pct);
  const correlated = num(body.max_correlated_pct);
  const concurrent = num(body.max_concurrent);
  const tickets = num(body.max_tickets_per_day);

  for (const [name, value] of [
    ["capital_usd", capital],
    ["risk_per_trade_pct", risk],
    ["max_position_pct", position],
    ["max_gross_pct", gross],
    ["max_correlated_pct", correlated],
    ["max_concurrent", concurrent],
    ["max_tickets_per_day", tickets],
  ] as const) {
    if (value !== null && Number.isNaN(value)) {
      return NextResponse.json(
        { error: `${name} is not a number.` },
        { status: 400 },
      );
    }
  }

  if (capital !== null && capital <= 0) {
    return NextResponse.json(
      { error: "Capital must be greater than zero." },
      { status: 400 },
    );
  }

  const { data, error } = await supabase.rpc("update_my_profile", {
    p_display_name: body.display_name ?? null,
    p_capital_usd: capital,
    p_risk_per_trade_pct: risk,
    p_max_position_pct: position,
    p_max_gross_pct: gross,
    p_max_correlated_pct: correlated,
    p_max_concurrent: concurrent === null ? null : Math.trunc(concurrent),
    p_max_tickets_per_day: tickets === null ? null : Math.trunc(tickets),
    p_timezone: body.timezone ?? null,
  });

  if (error) {
    console.error("profile update failed", error);
    const message = error.message.includes("Capital")
      ? error.message
      : "Could not save the profile.";
    return NextResponse.json({ error: message }, { status: 400 });
  }

  // Tell the user what the database changed. Silent clamping teaches the
  // wrong lesson; a visible clamp teaches the limit.
  const profile = data as Record<string, number | string | null>;
  const clamped: string[] = [];
  const compare = (label: string, requested: number | null, actual: unknown) => {
    if (requested === null) return;
    const got = Number(actual);
    if (Number.isFinite(got) && Math.abs(got - requested) > 1e-9) {
      clamped.push(`${label}: ${requested} → ${got}`);
    }
  };
  compare("risk_per_trade_pct", risk, profile.risk_per_trade_pct);
  compare("max_position_pct", position, profile.max_position_pct);
  compare("max_gross_pct", gross, profile.max_gross_pct);
  compare("max_correlated_pct", correlated, profile.max_correlated_pct);
  compare("max_concurrent", concurrent, profile.max_concurrent);
  compare("max_tickets_per_day", tickets, profile.max_tickets_per_day);

  return NextResponse.json({ profile, clamped });
}
