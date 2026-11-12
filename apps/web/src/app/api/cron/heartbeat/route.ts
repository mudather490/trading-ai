import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";

/**
 * GET /api/cron/heartbeat
 *
 * A daily write that keeps the Supabase project from being paused.
 *
 * The Supabase free tier pauses a project after one week of inactivity, and
 * a paused project is a total outage. The live worker's heartbeat normally
 * prevents this, but if the worker is down for a week — or before it is
 * deployed — this route does it instead.
 *
 * Vercel Hobby allows one cron run per day per job, which is exactly what
 * this needs.
 *
 * It uses the SERVICE ROLE key, because it writes a system row that no
 * user should be able to forge. That key lives in the Vercel server
 * environment and is never exposed to the browser.
 */
export async function GET(request: Request) {
  // Vercel sends `Authorization: Bearer <CRON_SECRET>` when CRON_SECRET is
  // set. If it is not set, the endpoint is open, so we require the header
  // anyway and refuse without it.
  const secret = process.env.CRON_SECRET;
  const provided = request.headers.get("authorization");

  if (!secret) {
    return NextResponse.json(
      { ok: false, reason: "CRON_SECRET is not configured." },
      { status: 500 },
    );
  }

  if (provided !== `Bearer ${secret}`) {
    return NextResponse.json({ ok: false, reason: "unauthorised" }, { status: 401 });
  }

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;

  if (!url || !key) {
    return NextResponse.json(
      { ok: false, reason: "Supabase is not configured." },
      { status: 500 },
    );
  }

  try {
    // A service-role client, bypassing RLS, for a system-level write.
    const { createClient: createServiceClient } = await import(
      "@supabase/supabase-js"
    );
    const admin = createServiceClient(url, key, {
      auth: { autoRefreshToken: false, persistSession: false },
    });

    const { error } = await admin.from("worker_heartbeat").upsert(
      {
        worker_id: "cron-heartbeat",
        version: "cron",
        last_seen_at: new Date().toISOString(),
        streams: {},
        lag_ms: {},
        meta: { source: "vercel_cron", note: "keeps the free project awake" },
      },
      { onConflict: "worker_id" },
    );

    if (error) {
      console.error("cron heartbeat failed", error);
      return NextResponse.json(
        { ok: false, reason: "write failed" },
        { status: 500 },
      );
    }

    return NextResponse.json({ ok: true, at: new Date().toISOString() });
  } catch (err) {
    console.error("cron heartbeat threw", err);
    return NextResponse.json(
      { ok: false, reason: "unexpected error" },
      { status: 500 },
    );
  }
}
