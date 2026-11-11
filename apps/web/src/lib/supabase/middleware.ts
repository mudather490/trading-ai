import { createServerClient } from "@supabase/ssr";
import { type NextRequest, NextResponse } from "next/server";

export interface SessionResult {
  response: NextResponse;
  user: { id: string } | null;
}

/**
 * Refresh the Supabase session in the proxy (Next 16's name for middleware).
 *
 * The proxy runs on the Edge runtime, where `cookies()` from `next/headers`
 * is not available. We therefore build a client bound to the request's own
 * cookie jar.
 *
 * The returned response carries the refreshed cookies back to the browser.
 */
export async function updateSession(request: NextRequest): Promise<SessionResult> {
  let response = NextResponse.next({ request });

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  if (!url || !key) {
    // Without Supabase configured the app cannot authenticate. Let the
    // request through so the login page can explain what is missing rather
    // than showing a blank redirect loop.
    return { response, user: null };
  }

  const supabase = createServerClient(url, key, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet) {
        cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
        response = NextResponse.next({ request });
        cookiesToSet.forEach(({ name, value, options }) =>
          response.cookies.set(name, value, options),
        );
      },
    },
  });

  // getUser() revalidates the JWT with the auth server. Calling
  // getSession() alone would trust a cookie we have not verified.
  const {
    data: { user },
  } = await supabase.auth.getUser();

  return { response, user };
}
