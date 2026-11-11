import type { EngineState } from "@/lib/data";
import { fmtAge } from "@/lib/types";

/**
 * The engine badge.
 *
 * The most important element on the screen. If the worker is dead, the app
 * refuses to issue tickets, and the user deserves to know that BEFORE they
 * read a price and wonder why nothing is happening.
 *
 * Three states, not two:
 *   live     the heartbeat is fresh
 *   stale    the heartbeat is older than 2 minutes
 *   never    the worker has never connected
 */
export function EngineBadge({ engine }: { engine: EngineState | null }) {
  const status = engine?.status ?? "never_seen";
  const age = engine?.age_seconds ?? null;

  const config = {
    live: {
      color: "text-live",
      border: "border-live/30",
      bg: "bg-live/5",
      label: "LIVE",
      pulse: true,
    },
    stale: {
      color: "text-degraded",
      border: "border-degraded/30",
      bg: "bg-degraded/5",
      label: "STALE",
      pulse: false,
    },
    never_seen: {
      color: "text-down",
      border: "border-down/30",
      bg: "bg-down/5",
      label: "OFFLINE",
      pulse: false,
    },
  }[status];

  const title =
    status === "live"
      ? `Engine live. Last heartbeat ${fmtAge(age)}.`
      : status === "stale"
        ? `The engine has not reported for ${fmtAge(age)}. No tickets are being issued.`
        : "The live engine has never connected. No tickets are being issued.";

  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 px-2 py-1 rounded border ${config.border} ${config.bg}`}
    >
      <span
        className={`w-1.5 h-1.5 rounded-full ${config.color} ${config.pulse ? "pulse" : ""}`}
        style={{ backgroundColor: "currentColor" }}
      />
      <span className={`text-[10px] font-semibold uppercase tracking-wider ${config.color}`}>
        {config.label}
      </span>
      {status !== "never_seen" && (
        <span className="text-[10px] text-muted tnum hidden sm:inline">
          {fmtAge(age)}
        </span>
      )}
    </span>
  );
}
