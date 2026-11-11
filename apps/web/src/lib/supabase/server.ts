import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

/**
 * The server Supabase client.
 *
 * This one carries the user's session, so RLS applies to every query it
 * makes. That is the point: the server does not get to see more than the
 * signed-in user is allowed to see.
 *
 * The service-role key is used only where a query genuinely needs to bypass
 * RLS, and never for user-scoped data. See `lib/service.ts`.
 */
export async function createClient() {
  const cookieStore = await cookies();

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  if (!url || !key) {
    throw new Error(
      "NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY are required.",
    );
  }

  return createServerClient(url, key, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          cookiesToSet.forEach(({ name, value, options }) =>
            cookieStore.set(name, value, options),
          );
        } catch {
          // Called from a Server Component, where cookies are read-only.
          // The middleware refreshes the session, so this is safe to ignore.
        }
      },
    },
  });
}
