"use client";

import { useState } from "react";
import type { Profile } from "@/lib/data";

/**
 * The risk settings form.
 *
 * The percentages are shown the way a human thinks about them (0.5%, 20%) and
 * posted as fractions, because that is the unit the ticket function uses. The
 * limits printed next to the fields are the real ones: the form is not the
 * enforcement, `update_my_profile()` is.
 */
export function SettingsForm({ profile }: { profile: Profile }) {
  const [displayName, setDisplayName] = useState(profile.display_name ?? "");
  const [capital, setCapital] = useState(
    profile.capital_usd == null ? "" : String(profile.capital_usd),
  );
  const [riskPct, setRiskPct] = useState(
    String(Number(profile.risk_per_trade_pct) * 100),
  );
  const [positionPct, setPositionPct] = useState(
    String(Number(profile.max_position_pct) * 100),
  );
  const [grossPct, setGrossPct] = useState(
    String(Number(profile.max_gross_pct) * 100),
  );
  const [correlatedPct, setCorrelatedPct] = useState(
    String(Number(profile.max_correlated_pct) * 100),
  );
  const [concurrent, setConcurrent] = useState(String(profile.max_concurrent));
  const [tickets, setTickets] = useState(String(profile.max_tickets_per_day));
  const [timezone, setTimezone] = useState(profile.timezone);

  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [clamped, setClamped] = useState<string[]>([]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setSaved(false);
    setError(null);
    setClamped([]);

    try {
      const res = await fetch("/api/settings/profile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          display_name: displayName,
          capital_usd: capital === "" ? null : Number(capital),
          risk_per_trade_pct: Number(riskPct) / 100,
          max_position_pct: Number(positionPct) / 100,
          max_gross_pct: Number(grossPct) / 100,
          max_correlated_pct: Number(correlatedPct) / 100,
          max_concurrent: Number(concurrent),
          max_tickets_per_day: Number(tickets),
          timezone,
        }),
      });
      const data = (await res.json()) as {
        error?: string;
        clamped?: string[];
      };
      if (!res.ok) {
        setError(data.error ?? "Could not save the profile.");
        return;
      }
      setSaved(true);
      setClamped(data.clamped ?? []);
    } catch {
      setError("Could not reach the settings service.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <section className="rounded-lg border border-border bg-panel p-4">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted mb-3">
          Account
        </h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Display name" hint="Shown only to you.">
            <input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              maxLength={80}
              className="w-full rounded border border-border bg-background px-2 py-1.5 text-sm"
            />
          </Field>
          <Field label="Timezone" hint="IANA name, e.g. Europe/London.">
            <input
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              maxLength={64}
              className="w-full rounded border border-border bg-background px-2 py-1.5 text-sm"
            />
          </Field>
          <Field
            label="Capital (USD)"
            hint="The budget tickets are sized against. Must be > 0."
          >
            <input
              type="number"
              min="0"
              step="0.01"
              value={capital}
              onChange={(e) => setCapital(e.target.value)}
              className="w-full rounded border border-border bg-background px-2 py-1.5 text-sm tnum"
            />
          </Field>
        </div>
      </section>

      <section className="rounded-lg border border-border bg-panel p-4">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted mb-3">
          Risk limits
        </h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Risk per trade (%)" hint="Capped at 2% of capital.">
            <input
              type="number"
              min="0.01"
              max="2"
              step="0.05"
              value={riskPct}
              onChange={(e) => setRiskPct(e.target.value)}
              className="w-full rounded border border-border bg-background px-2 py-1.5 text-sm tnum"
            />
          </Field>
          <Field label="Max position (%)" hint="Capped at 50% of capital.">
            <input
              type="number"
              min="0.01"
              max="50"
              step="1"
              value={positionPct}
              onChange={(e) => setPositionPct(e.target.value)}
              className="w-full rounded border border-border bg-background px-2 py-1.5 text-sm tnum"
            />
          </Field>
          <Field label="Max gross exposure (%)" hint="Capped at 200%.">
            <input
              type="number"
              min="1"
              max="200"
              step="1"
              value={grossPct}
              onChange={(e) => setGrossPct(e.target.value)}
              className="w-full rounded border border-border bg-background px-2 py-1.5 text-sm tnum"
            />
          </Field>
          <Field
            label="Max correlated exposure (%)"
            hint="Share of capital in one correlated cluster. Capped at 100%."
          >
            <input
              type="number"
              min="1"
              max="100"
              step="1"
              value={correlatedPct}
              onChange={(e) => setCorrelatedPct(e.target.value)}
              className="w-full rounded border border-border bg-background px-2 py-1.5 text-sm tnum"
            />
          </Field>
          <Field label="Max concurrent positions" hint="Capped at 10.">
            <input
              type="number"
              min="1"
              max="10"
              step="1"
              value={concurrent}
              onChange={(e) => setConcurrent(e.target.value)}
              className="w-full rounded border border-border bg-background px-2 py-1.5 text-sm tnum"
            />
          </Field>
          <Field label="Max tickets per day" hint="Capped at 20.">
            <input
              type="number"
              min="1"
              max="20"
              step="1"
              value={tickets}
              onChange={(e) => setTickets(e.target.value)}
              className="w-full rounded border border-border bg-background px-2 py-1.5 text-sm tnum"
            />
          </Field>
        </div>
      </section>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={saving}
          className="px-4 py-2 rounded text-sm font-medium bg-accent text-white hover:opacity-90 disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save settings"}
        </button>
        {saved && (
          <span className="text-xs text-live">
            Saved and audit-logged.
          </span>
        )}
        {error && <span className="text-xs text-down">{error}</span>}
      </div>

      {clamped.length > 0 && (
        <div className="rounded-lg border border-degraded/40 bg-degraded/5 p-3">
          <div className="text-xs font-semibold text-degraded mb-1">
            The database clamped these values
          </div>
          <ul className="text-xs text-muted space-y-0.5">
            {clamped.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      )}

      <p className="text-[11px] text-muted/70">
        These are safety limits, not preferences. The browser cannot raise
        them: the clamp lives in the database function that sizes every
        ticket.
      </p>
    </form>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="block text-[11px] text-muted mb-1">{label}</span>
      {children}
      {hint && (
        <span className="block text-[10px] text-muted/60 mt-1">{hint}</span>
      )}
    </label>
  );
}
