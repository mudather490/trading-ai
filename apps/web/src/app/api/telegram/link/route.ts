import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";

/**
 * GET  /api/telegram/link   is this account linked, and to which chat?
 * POST /api/telegram/link   mint a single-use code, or revoke the link
 *
 * The code is returned once, in plaintext, and only its SHA-256 is stored.
 * The user types it into the bot; the bot redeems it. Neither side trusts
 * the other on its own.
 */
export async function GET() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) {
    return NextResponse.json({ error: "Not signed in." }, { status: 401 });
  }

  const { data, error } = await supabase
    .from("telegram_links")
    .select("chat_id,username,linked_at,revoked_at")
    .eq("user_id", user.id)
    .maybeSingle();

  if (error) {
    console.error("telegram link read failed", error);
    return NextResponse.json(
      { error: "Could not read the link state." },
      { status: 500 },
    );
  }

  return NextResponse.json({
    linked: Boolean(data && !data.revoked_at),
    chat_id: data?.chat_id ?? null,
    username: data?.username ?? null,
    linked_at: data?.linked_at ?? null,
  });
}

export async function POST(request: Request) {
  let body: { action?: string };
  try {
    body = (await request.json()) as { action?: string };
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

  if (body.action === "revoke") {
    const { data, error } = await supabase.rpc("revoke_telegram_link", {
      p_chat_id: null,
    });
    if (error) {
      console.error("telegram revoke failed", error);
      return NextResponse.json(
        { error: "Could not revoke the link." },
        { status: 500 },
      );
    }
    return NextResponse.json({ ok: true, revoked: data?.revoked ?? 0 });
  }

  const { data, error } = await supabase.rpc("create_telegram_link_code");
  if (error) {
    console.error("telegram link code failed", error);
    return NextResponse.json(
      { error: "Could not create a link code." },
      { status: 500 },
    );
  }

  return NextResponse.json(data);
}
