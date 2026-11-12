"use client";

import { useState } from "react";

interface GateFailure {
  rule: string;
  ok: boolean;
  value?: number | string | null;
  threshold?: number | string | null;
  detail?: string;
}

interface GateResult {
  status: "promoted" | "rejected";
  policy_version?: string;
  message?: string;
  failures?: GateFailure[];
}

/**
 * The promote button.
 *
 * It asks the GATE, and it shows the answer. When the gate refuses, every
 * failed rule is listed with its value, its threshold and its detail: a
 * refusal is a result, and hiding it would make the gate look arbitrary.
 *
 * The button cannot lower a threshold. The thresholds live in
 * `gate_policy`, and changing them means a new version row and a migration.
 */
export function PromoteButton({
  versionId,
  label = "Promote",
}: {
  versionId: string;
  label?: string;
}) {
  const [state, setState] = useState<"idle" | "busy" | "done" | "failed">(
    "idle",
  );
  const [result, setResult] = useState<GateResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function promote() {
    setState("busy");
    setError(null);
    setResult(null);
    try {
      const res = await fetch("/api/strategies/promote", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ version_id: versionId }),
      });
      const data = (await res.json()) as GateResult & { error?: string };
      if (!res.ok) {
        setError(data.error ?? "The gate did not answer.");
        setState("failed");
        return;
      }
      setResult(data);
      setState(data.status === "promoted" ? "done" : "failed");
    } catch {
      setError("Could not reach the gate.");
      setState("failed");
    }
  }

  return (
    <div className="mt-2">
      <div className="flex items-center gap-2">
        <button
          onClick={() => void promote()}
          disabled={state === "busy" || state === "done"}
          className={`px-2.5 py-1 rounded text-[11px] border transition-colors ${
            state === "done"
              ? "text-live border-live/30 bg-live/5 cursor-default"
              : "text-foreground border-border hover:border-muted disabled:opacity-50"
          }`}
        >
          {state === "busy"
            ? "asking the gate…"
            : state === "done"
              ? "promoted"
              : label}
        </button>
        {result?.policy_version && (
          <span className="text-[10px] text-muted/70">
            gate {result.policy_version}
          </span>
        )}
      </div>

      {error && <p className="text-[11px] text-down mt-1.5">{error}</p>}

      {result?.status === "rejected" && (
        <div className="rounded border border-degraded/40 bg-degraded/5 p-2 mt-1.5">
          <div className="text-[11px] font-semibold text-degraded mb-1">
            {result.message ?? "The gate refused this candidate."}
          </div>
          <ul className="space-y-1">
            {(result.failures ?? []).map((failure) => (
              <li key={failure.rule} className="text-[11px] text-muted">
                <span className="text-foreground">{failure.rule}</span>
                {failure.value !== undefined && failure.value !== null && (
                  <>
                    {" "}
                    = {String(failure.value)}
                  </>
                )}
                {failure.threshold !== undefined &&
                  failure.threshold !== null && (
                    <> (needs {String(failure.threshold)})</>
                  )}
                {failure.detail && (
                  <span className="block text-muted/80">{failure.detail}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {result?.status === "promoted" && (
        <p className="text-[11px] text-live mt-1.5">{result.message}</p>
      )}
    </div>
  );
}
