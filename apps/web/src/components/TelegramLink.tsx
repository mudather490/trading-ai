"use client";

import { useCallback, useEffect, useState } from "react";

interface LinkState {
  linked: boolean;
  chat_id: number | null;
  username: string | null;
  linked_at: string | null;
}

/**
 * The Telegram section of Settings.
 *
 * The handshake, from this side:
 *   1. the browser asks the database for a single-use code,
 *   2. the code is shown here ONCE,
 *   3. the user sends it to the bot,
 *   4. the bot redeems it and the chat becomes this account.
 *
 * The browser never learns the bot token, and the bot never learns the
 * account password. The code expires in ten minutes and works once.
 */
export function TelegramLink() {
  const [state, setState] = useState<LinkState | null>(null);
  const [code, setCode] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/telegram/link", { cache: "no-store" });
      if (!res.ok) return;
      setState((await res.json()) as LinkState);
    } catch {
      setError("Could not reach the server.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/telegram/link", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "create" }),
      });
      const data = (await res.json()) as {
        code?: string;
        expires_at?: string;
        error?: string;
      };
      if (!res.ok || !data.code) {
        setError(data.error ?? "Could not create a link code.");
        return;
      }
      setCode(data.code);
      setExpiresAt(data.expires_at ?? null);
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  async function unlink() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/telegram/link", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "revoke" }),
      });
      if (!res.ok) {
        setError("Could not unlink.");
        return;
      }
      setCode(null);
      await load();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  const linked = state?.linked === true;

  return (
    <div className="rounded-lg border border-border bg-panel p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2 mb-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted">
          Telegram
        </h2>
        <span
          className={`text-[10px] uppercase tracking-wider rounded border px-1.5 py-0.5 ${
            linked
              ? "text-live border-live/30"
              : "text-muted border-border"
          }`}
        >
          {linked ? "linked" : "not linked"}
        </span>
      </div>

      {linked ? (
        <div className="space-y-2">
          <p className="text-sm text-muted">
            Chat{" "}
            <span className="text-foreground tnum">{state?.chat_id}</span>
            {state?.username ? ` (@${state.username})` : ""} can ask for
            tickets on this account.
          </p>
          <button
            onClick={() => void unlink()}
            disabled={busy}
            className="px-3 py-1.5 rounded text-xs border border-border text-muted hover:text-foreground disabled:opacity-50"
          >
            Unlink this chat
          </button>
        </div>
      ) : (
        <div className="space-y-2">
          <p className="text-sm text-muted leading-relaxed">
            Link this account to a Telegram chat so you can ask for signals
            from anywhere. The bot can read this account&apos;s tickets and
            nothing else.
          </p>

          {code ? (
            <div className="rounded border border-accent/40 bg-accent/5 p-3">
              <div className="text-[10px] uppercase tracking-wider text-muted mb-1">
                Send this to the bot (one time only)
              </div>
              <code className="text-lg tracking-widest text-foreground">
                {code}
              </code>
              <p className="text-[11px] text-muted mt-2">
                Message the bot: <span className="text-foreground">/link {code}</span>
                {expiresAt ? (
                  <>
                    {" "}
                    — expires {new Date(expiresAt).toLocaleTimeString()}.
                  </>
                ) : null}
              </p>
            </div>
          ) : (
            <button
              onClick={() => void generate()}
              disabled={busy}
              className="px-3 py-1.5 rounded text-xs font-medium bg-accent text-white hover:opacity-90 disabled:opacity-50"
            >
              {busy ? "Working…" : "Generate link code"}
            </button>
          )}
        </div>
      )}

      {error && <p className="text-xs text-down mt-2">{error}</p>}
    </div>
  );
}
