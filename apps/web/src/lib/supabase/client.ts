import { createBrowserClient } from "@supabase/ssr";

/**
 * The browser Supabase client.
 *
 * Only the ANON key is used here. The service-role key never reaches the
 * browser: it lives in the Vercel server environment and nowhere else.
 */
export function createClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  if (!url || !key) {
    throw new Error(
      "NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY are required.",
    );
  }

  return createBrowserClient(url, key);
}
