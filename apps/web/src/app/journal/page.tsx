import { connection } from "next/server";
import { AppNav } from "@/components/AppNav";
import { loadJournal, requireUser } from "@/lib/data";
import { fmt } from "@/lib/types";

export const instant = false;

/**
 * The journal.
 *
 * This is the accountability screen: every ticket the user chose to act on or
 * pass, with the exact size, stop and risk that the engine computed at the
 * time. Nothing here is inferred after the fact. A trade that was never
 * journaled cannot be claimed later.
 */
export default async function JournalPage() {
  await connection();
  await requireUser();

  const { entries, tickets, instruments, error } = await loadJournal();

  return (
    <>
      <AppNav />
      <main className="flex-1 overflow-y-auto p-4 space-y-4">
        <header>
          <h1 className="text-sm font-semibold">Journal</h1>
          <p className="text-xs text-muted mt-1">
            A snapshot of the ticket at the moment you acted, plus what you
            chose to do. {entries.length}{" "}
            {entries.length === 1 ? "entry" : "entries"}.
          </p>
        </header>

        {error && (
          <div className="rounded-lg border border-down/40 bg-down/5 p-3 text-sm text-muted">
            {error}
          </div>
        )}

        {entries.length === 0 ? (
          <div className="rounded-lg border border-border bg-panel p-5">
            <h2 className="text-sm font-semibold mb-1">Nothing journaled yet</h2>
            <p className="text-sm text-muted leading-relaxed">
              When a ticket is issued, use{" "}
              <span className="text-foreground">Journal this ticket</span> in
              the Command Center to record whether you took it or passed. The
              app never places the order and never assumes you did.
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            {entries.map((entry) => {
              const ticket = entry.ticket_id
                ? tickets[entry.ticket_id]
                : undefined;
              const instrument = entry.instrument_id
                ? instruments[entry.instrument_id]
                : ticket?.instrument_id
                  ? instruments[ticket.instrument_id]
                  : undefined;

              return (
                <article
                  key={entry.id}
                  className="rounded-lg border border-border bg-panel p-3"
                >
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <div className="flex items-baseline gap-2">
                      <span className="text-sm font-medium">
                        {instrument?.symbol ?? "Unknown instrument"}
                      </span>
                      {ticket && (
                        <span
                          className={`text-xs font-semibold ${
                            ticket.side === "buy" ? "text-up" : "text-down"
                          }`}
                        >
                          {ticket.side.toUpperCase()}
                        </span>
                      )}
                      <ActionBadge action={entry.action} />
                      {ticket && (
                        <span className="text-[10px] text-muted">
                          {ticket.timeframe}
                        </span>
                      )}
                    </div>
                    <time className="text-[11px] text-muted tnum">
                      {new Date(entry.created_at).toLocaleString()}
                    </time>
                  </div>

                  {ticket && (
                    <div className="grid grid-cols-2 sm:grid-cols-5 gap-x-4 gap-y-1 text-[11px] mt-2">
                      <Fact
                        label="Entry"
                        value={`${fmt(ticket.entry_low, 4)} – ${fmt(
                          ticket.entry_high,
                          4,
                        )}`}
                      />
                      <Fact label="Stop" value={fmt(ticket.stop_price, 4)} />
                      <Fact
                        label="Target"
                        value={fmt(ticket.target_price, 4)}
                      />
                      <Fact label="Size" value={fmt(ticket.size_units, 6)} />
                      <Fact
                        label="Risk"
                        value={fmt(ticket.risk_amount, 2)}
                      />
                    </div>
                  )}

                  {(entry.pnl_usd != null || entry.r_multiple != null) && (
                    <div className="flex items-center gap-4 text-[11px] mt-2">
                      {entry.pnl_usd != null && (
                        <span
                          className={
                            entry.pnl_usd >= 0 ? "text-up" : "text-down"
                          }
                        >
                          P&L {fmt(entry.pnl_usd, 2)}
                        </span>
                      )}
                      {entry.r_multiple != null && (
                        <span
                          className={
                            entry.r_multiple >= 0 ? "text-up" : "text-down"
                          }
                        >
                          {fmt(entry.r_multiple, 2)}R
                        </span>
                      )}
                      <span className="text-muted">
                        {entry.outcome_state ?? "open"}
                      </span>
                    </div>
                  )}

                  {entry.note && (
                    <p className="text-xs text-muted mt-2 leading-relaxed">
                      {entry.note}
                    </p>
                  )}
                </article>
              );
            })}
          </div>
        )}

        <p className="text-[11px] text-muted/70">
          Outcome fields are filled in after the fact. Until then an entry is
          open, not a winner.
        </p>
      </main>
    </>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span className="text-muted">{label} </span>
      <span className="tnum">{value}</span>
    </div>
  );
}

function ActionBadge({ action }: { action: string }) {
  const tone =
    action === "took"
      ? "text-live border-live/30"
      : action === "skipped"
        ? "text-muted border-border"
        : "text-degraded border-degraded/30";
  const label =
    action === "took"
      ? "took it"
      : action === "skipped"
        ? "passed"
        : action;
  return (
    <span className={`rounded border px-1.5 py-0.5 text-[10px] ${tone}`}>
      {label}
    </span>
  );
}
