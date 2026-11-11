import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";

/**
 * The magic-link callback.
 *
 * Supabase sends the user here with a token in the URL fragment. We exchange
 * it for a session and redirect to the app.
 */
export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get("code");
  const next = searchParams.get("next") ?? "/";

  if (code) {
    const supabase = await createClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) {
      return NextResponse.redirect(`${origin}${next}`);
    }
    console.error("code exchange failed", error);
  }

  return NextResponse.redirect(`${origin}/login?error=auth_failed`);
}
