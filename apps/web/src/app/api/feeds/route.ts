import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";

/**
 * GET /api/feeds
 *
 * Per-venue feed status for the settings screen and any client that wants to
 * refresh it without reloading the page. The logic lives in the SQL function
 * `feed_health()`; this route adds nothing but authentication.
 */
export async function GET() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) {
    return NextResponse.json({ error: "Not signed in." }, { status: 401 });
  }

  const { data, error } = await supabase.rpc("feed_health");
  if (error) {
    console.error("feed_health failed", error);
    return NextResponse.json(
      { error: "Could not read feed health." },
      { status: 500 },
    );
  }

  return NextResponse.json(data ?? { venues: [], engine: null });
}
