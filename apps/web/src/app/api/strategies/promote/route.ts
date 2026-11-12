import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";

/**
 * POST /api/strategies/promote
 *
 * The one button that can change what the app advises.
 *
 * It calls `promote_strategy()`, which is the GATE: a versioned SQL
 * predicate that measures the candidate against the published thresholds and
 * either promotes it or explains every rule it failed. The gate is the
 * decision; this route is the button.
 *
 * Two things this route deliberately does not do:
 *   * lower a threshold (the thresholds live in `gate_policy`, and changing
 *     them requires a new version row, not a request),
 *   * hide a failure (the gate returns the failed rules and they are shown).
 *
 * Only the owner may call it. That is enforced in the database, not here.
 */
export async function POST(request: Request) {
  let body: { version_id?: string; note?: string };
  try {
    body = (await request.json()) as { version_id?: string; note?: string };
  } catch {
    return NextResponse.json({ error: "Invalid JSON." }, { status: 400 });
  }

  const versionId = body.version_id;
  if (!versionId) {
    return NextResponse.json({ error: "No version given." }, { status: 400 });
  }

  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) {
    return NextResponse.json({ error: "Not signed in." }, { status: 401 });
  }

  const { data, error } = await supabase.rpc("promote_strategy", {
    p_version_id: versionId,
    p_note: body.note ?? null,
  });

  if (error) {
    // The gate refuses by RAISING for the two cases that are not about the
    // candidate's numbers: a non-owner, and a version with no performance
    // record. Both are real refusals and both are reported.
    console.error("promote_strategy failed", error);
    return NextResponse.json(
      { error: error.message },
      { status: 403 },
    );
  }

  return NextResponse.json(data ?? {});
}
