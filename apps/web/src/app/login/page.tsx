"use client";

import { useState } from "react";
import { createClient } from "@/lib/supabase/client";

/**
 * Magic-link sign-in.
 *
 * No password. A one-time link sent by email. That removes the password
 * database, the password-reset flow, and the credential-stuffing surface,
 * which is a real simplification for a small trusted group.
 */
export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function signIn(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);

    try {
      const supabase = createClient();
      const { error: signInError } = await supabase.auth.signInWithOtp({
        email,
        options: {
          emailRedirectTo: `${window.location.origin}/auth/callback`,
        },
      });

      if (signInError) {
        setError(signInError.message);
      } else {
        setSent(true);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send the link.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex-1 flex items-center justify-center p-6">
      <div className="w-full max-w-sm">
        <h1 className="text-lg font-semibold tracking-tight mb-1">
          Trading Command Center
        </h1>
        <p className="text-sm text-muted mb-6">
          Sign in with a magic link. No password needed.
        </p>

        {sent ? (
          <div className="rounded-lg border border-live/30 bg-live/5 p-4">
            <div className="text-sm font-medium mb-1">Check your email</div>
            <p className="text-sm text-muted">
              We sent a sign-in link to{" "}
              <span className="text-foreground">{email}</span>. It expires in
              one hour.
            </p>
          </div>
        ) : (
          <form onSubmit={signIn} className="space-y-3">
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              autoComplete="email"
              className="w-full px-3 py-2 rounded bg-panel border border-border
                text-foreground placeholder:text-muted/60 focus:outline-none
                focus:border-accent transition-colors"
            />
            <button
              type="submit"
              disabled={busy}
              className="w-full px-4 py-2 rounded bg-accent text-white text-sm
                font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
            >
              {busy ? "Sending…" : "Send sign-in link"}
            </button>
          </form>
        )}

        {error && (
          <p className="mt-3 text-xs text-down leading-relaxed">{error}</p>
        )}

        <p className="mt-6 text-[11px] text-muted/60 leading-relaxed">
          This tool reads public market data and shows trade tickets for
          research. It does not place orders. Not financial advice.
        </p>
      </div>
    </main>
  );
}
