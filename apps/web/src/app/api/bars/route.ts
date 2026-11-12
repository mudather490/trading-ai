import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";

/**
 * GET /api/bars?symbol=BTCUSDT&timeframe=1h&limit=300
 *
 * The chart's data. Only CLOSED bars are returned: a forming candle is not a
 * candle, and showing one while the analysis panel uses closed bars would be
 * two different truths on one screen.
 */
export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const symbol = searchParams.get("symbol");
  const timeframe = searchParams.get("timeframe") ?? "1h";
  const limit = Math.min(
    Math.max(Number(searchParams.get("limit") ?? 300), 50),
    1000,
  );

  if (!symbol) {
    return NextResponse.json({ error: "No symbol given." }, { status: 400 });
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
    .select("id,symbol,venue,asset_class,data_quality,display_name")
    .eq("symbol", symbol.toUpperCase())
    .eq("in_universe", true)
    .maybeSingle();

  if (instrumentError) {
    console.error("instrument lookup failed", instrumentError);
    return NextResponse.json({ error: "Lookup failed." }, { status: 500 });
  }
  if (!instrument) {
    return NextResponse.json(
      { error: `${symbol.toUpperCase()} is not in the universe.` },
      { status: 404 },
    );
  }

  // Newest N, then reverse: PostgREST cannot express "last N" without a
  // window function, and the chart needs ascending time.
  const { data, error } = await supabase
    .from("bars")
    .select("ts,open,high,low,close,volume")
    .eq("instrument_id", instrument.id)
    .eq("timeframe", timeframe)
    .eq("is_closed", true)
    .order("ts", { ascending: false })
    .limit(limit);

  if (error) {
    console.error("bars query failed", error);
    return NextResponse.json({ error: "Could not read bars." }, { status: 500 });
  }

  const rows = data ?? [];

  // Which timeframes DO have closed bars for this instrument?
  //
  // A fresh install has 1m bars and no 1h bars for an hour, so a chart that
  // defaults to 1h would show an empty screen and look broken. Four tiny
  // queries answer it, and they only run when the requested timeframe is
  // empty.
  let available: Record<string, number> = {};
  if (rows.length === 0) {
    await Promise.all(
      ["1m", "5m", "1h", "1d"].map(async (tf) => {
        const { count } = await supabase
          .from("bars")
          .select("ts", { count: "exact", head: true })
          .eq("instrument_id", instrument.id)
          .eq("timeframe", tf)
          .eq("is_closed", true);
        if (count && count > 0) available[tf] = count;
      }),
    );
  }

  const bars = rows
    .slice()
    .reverse()
    .map((row) => ({
      time: Math.floor(new Date(row.ts as string).getTime() / 1000),
      open: Number(row.open),
      high: Number(row.high),
      low: Number(row.low),
      close: Number(row.close),
      volume: row.volume == null ? undefined : Number(row.volume),
    }));

  return NextResponse.json({
    symbol: instrument.symbol,
    timeframe,
    instrument,
    bars,
    available,
  });
}
