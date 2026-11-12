import { connection } from "next/server";
import { AppNav } from "@/components/AppNav";
import { PromoteButton } from "@/components/PromoteButton";
import { loadStrategies, requireUser } from "@/lib/data";
import { fmt, fmtPct } from "@/lib/types";

export const instant = false;

/**
 * The strategy lab, read-only for now.
 *
 * It answers three questions and refuses to answer a fourth:
 *   * what is registered?
 *   * what did the gate measure?
 *   * what is promoted?
 * and it never claims an edge the gate has not verified. If nothing passed,
 * the page says so. Lowering the gate to fill the screen would be a lie.
 */
export default async function StrategiesPage() {
  await connection();
  await requireUser();

  const { strategies, versions, champions, performance, gate, error } =
    await loadStrategies();

  const championByKey = new Map(
    champions.map((c) => [`${c.asset_class}/${c.timeframe}`, c]),
  );
  const versionById = new Map(versions.map((v) => [v.id, v]));
  const performanceByVersion = new Map<string, typeof performance>();
  for (const row of performance) {
    const list = performanceByVersion.get(row.version_id) ?? [];
    list.push(row);
    performanceByVersion.set(row.version_id, list);
  }

  const thresholds = gate?.thresholds ?? {};

  return (
    <>
      <AppNav />
      <main className="flex-1 overflow-y-auto p-4 space-y-6">
        <header className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h1 className="text-sm font-semibold">Strategies</h1>
            <p className="text-xs text-muted mt-1">
              A version becomes a champion only by passing the gate. The gate
              is a versioned predicate, not a judgement call.
            </p>
          </div>
          <span className="text-[10px] uppercase tracking-wider text-muted border border-border rounded px-1.5 py-0.5">
            gate {gate?.version ?? "not found"}
          </span>
        </header>

        {error && (
          <div className="rounded-lg border border-down/40 bg-down/5 p-3 text-sm text-muted">
            {error}
          </div>
        )}

        <section>
          <h2 className="text-[10px] uppercase tracking-wider text-muted mb-2">
            Champions
          </h2>
          {champions.length === 0 ? (
            <div className="rounded-lg border border-border bg-panel p-4">
              <div className="text-sm font-semibold mb-1">
                No promoted strategy yet
              </div>
              <p className="text-sm text-muted leading-relaxed">
                No version has passed the gate, so the app answers{" "}
                <code className="text-foreground">no_champion</code> for every
                instrument. That is the honest answer: a signal with no
                verified edge is not a signal.
              </p>
            </div>
          ) : (
            <div className="grid gap-2 sm:grid-cols-2">
              {[...championByKey.entries()].map(([key, champion]) => {
                const version = versionById.get(champion.version_id);
                const strategy = strategies.find(
                  (s) => s.id === version?.strategy_id,
                );
                return (
                  <div
                    key={key}
                    className="rounded-lg border border-live/30 bg-live/5 p-3"
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="text-xs font-semibold">{key}</span>
                      <span className="text-[10px] text-muted">
                        v{version?.version ?? "?"}
                      </span>
                    </div>
                    <div className="text-sm mt-1">{strategy?.name ?? key}</div>
                    <div className="text-[11px] text-muted mt-1">
                      promoted {new Date(champion.promoted_at).toLocaleString()}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>

        <section>
          <h2 className="text-[10px] uppercase tracking-wider text-muted mb-2">
            Promotion gate · the thresholds a candidate must clear
          </h2>
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(thresholds).map(([name, value]) => (
              <span
                key={name}
                className="rounded border border-border bg-panel px-2 py-1 text-[10px] text-muted"
              >
                {name}{" "}
                <span className="text-foreground tnum">{String(value)}</span>
              </span>
            ))}
          </div>
          {gate?.notes && (
            <p className="text-[11px] text-muted/70 mt-2">{gate.notes}</p>
          )}
        </section>

        <section>
          <h2 className="text-[10px] uppercase tracking-wider text-muted mb-2">
            Registered strategies
          </h2>
          {strategies.length === 0 ? (
            <div className="rounded-lg border border-border bg-panel p-4 text-sm text-muted">
              No strategies are registered. Seed the templates and run the
              research worker.
            </div>
          ) : (
            <div className="space-y-2">
              {strategies.map((strategy) => {
                const strategyVersions = versions.filter(
                  (v) => v.strategy_id === strategy.id,
                );
                return (
                  <div
                    key={strategy.id}
                    className="rounded-lg border border-border bg-panel p-3"
                  >
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <div className="flex items-baseline gap-2">
                        <span className="text-sm font-medium">
                          {strategy.name}
                        </span>
                        <span className="text-[10px] uppercase tracking-wider text-muted">
                          {strategy.family} · {strategy.asset_class}
                        </span>
                        {strategy.template_id && (
                          <span className="text-[10px] text-muted/70">
                            {strategy.template_id}
                          </span>
                        )}
                      </div>
                      <span className="text-[10px] text-muted">
                        {strategyVersions.length} version
                        {strategyVersions.length === 1 ? "" : "s"}
                      </span>
                    </div>

                    {strategy.description && (
                      <p className="text-xs text-muted mt-1.5 leading-relaxed">
                        {strategy.description}
                      </p>
                    )}

                    {strategyVersions.length > 0 && (
                      <div className="mt-2 divide-y divide-border/60 border-t border-border/60">
                        {strategyVersions.map((version) => {
                          const perf = performanceByVersion.get(version.id) ?? [];
                          return (
                            <div
                              key={version.id}
                              className="py-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px]"
                            >
                              <span className="font-medium">
                                v{version.version}
                              </span>
                              <VersionStatus status={version.status} />
                              <span className="text-muted">
                                {version.timeframes.join(" · ")}
                              </span>
                              {perf.length === 0 ? (
                                <span className="text-muted/70">
                                  no performance record
                                </span>
                              ) : (
                                perf.map((row) => (
                                  <span
                                    key={`${row.timeframe}`}
                                    className="text-muted tnum"
                                  >
                                    {row.timeframe}: Sharpe{" "}
                                    <span className="text-foreground">
                                      {fmt(row.sharpe, 2)}
                                    </span>{" "}
                                    · DD{" "}
                                    <span className="text-foreground">
                                      {fmtPct(row.max_drawdown, 1)}
                                    </span>{" "}
                                    · DSR{" "}
                                    <span className="text-foreground">
                                      {fmt(row.dsr, 2)}
                                    </span>{" "}
                                    · PBO{" "}
                                    <span className="text-foreground">
                                      {fmt(row.pbo, 2)}
                                    </span>
                                  </span>
                                ))
                              )}
                              <PromoteButton versionId={version.id} />
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </section>

        <p className="text-[11px] text-muted/70">
          DSR is the deflated Sharpe ratio: the probability the edge survives
          the number of trials it took to find it. PBO is the probability of
          backtest overfitting. Neither is a return promise.
        </p>
      </main>
    </>
  );
}

function VersionStatus({ status }: { status: string }) {
  const tone =
    status === "champion"
      ? "text-live border-live/30"
      : status === "candidate"
        ? "text-degraded border-degraded/30"
        : status === "retired" || status === "rejected"
          ? "text-down border-down/30"
          : "text-muted border-border";
  return (
    <span className={`rounded border px-1 py-0.5 text-[10px] ${tone}`}>
      {status}
    </span>
  );
}
