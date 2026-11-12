import Link from "next/link";
import { connection } from "next/server";
import { AppNav } from "@/components/AppNav";
import { loadCommandCenter, requireUser } from "@/lib/data";
import { fmt, fmtPct, fmtAge } from "@/lib/types";

export const instant = false;

/**
 * The market list.
 *
 * One row per instrument in the universe, grouped by asset class, with the
 * last quote and its quality tier. It is a map, not an opinion: the decision
 * lives on the instrument page and in the Command Center.
 */
export default async function MarketsPage() {
  await connection();
  await requireUser();

  const { instruments, quotes, configured, error } = await loadCommandCenter();

  if (!configured) {
    return (
      <>
        <AppNav />
        <main className="flex-1 flex items-center justify-center p-6">
          <div className="max-w-md rounded-lg border border-degraded/40 bg-degraded/5 p-5">
            <h1 className="text-sm font-semibold mb-2">Not configured</h1>
            <p className="text-sm text-muted leading-relaxed">{error}</p>
          </div>
        </main>
      </>
    );
  }

  const quoteByInstrument = new Map(quotes.map((q) => [q.instrument_id, q]));
  const classes = ["crypto", "us_equity", "forex", "metal"].filter((c) =>
    instruments.some((i) => i.asset_class === c),
  );

  return (
    <>
      <AppNav />
      <main className="flex-1 overflow-y-auto p-4 space-y-6">
        <header>
          <h1 className="text-sm font-semibold">Markets</h1>
          <p className="text-xs text-muted mt-1">
            {instruments.length} instruments. Prices are for display; the
            engine decides on closed bars.
          </p>
        </header>

        {error && (
          <div className="rounded-lg border border-down/40 bg-down/5 p-3 text-sm text-muted">
            {error}
          </div>
        )}

        {classes.map((assetClass) => (
          <section key={assetClass}>
            <h2 className="text-[10px] uppercase tracking-wider text-muted mb-2">
              {assetClass.replace("_", " ")}
            </h2>
            <div className="rounded-lg border border-border overflow-hidden">
              <table className="w-full text-xs">
                <thead className="bg-panel-2 text-muted">
                  <tr className="text-left">
                    <th className="px-3 py-2 font-medium">Symbol</th>
                    <th className="px-3 py-2 font-medium">Venue</th>
                    <th className="px-3 py-2 font-medium text-right">Last</th>
                    <th className="px-3 py-2 font-medium text-right">Change</th>
                    <th className="px-3 py-2 font-medium text-right">Age</th>
                    <th className="px-3 py-2 font-medium">Quality</th>
                  </tr>
                </thead>
                <tbody>
                  {instruments
                    .filter((i) => i.asset_class === assetClass)
                    .map((instrument) => {
                      const quote = quoteByInstrument.get(instrument.id);
                      const age = quote
                        ? (Date.now() - new Date(quote.as_of).getTime()) / 1000
                        : null;
                      return (
                        <tr
                          key={instrument.id}
                          className="border-t border-border/60 hover:bg-panel-2/50"
                        >
                          <td className="px-3 py-2">
                            <Link
                              href={`/markets/${instrument.symbol}`}
                              className="font-medium hover:text-accent"
                            >
                              {instrument.symbol}
                            </Link>
                            <span className="text-muted ml-2 hidden sm:inline">
                              {instrument.display_name}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-muted">
                            {instrument.venue}
                          </td>
                          <td className="px-3 py-2 text-right tnum">
                            {fmt(quote?.last, 4)}
                          </td>
                          <td
                            className={`px-3 py-2 text-right tnum ${
                              quote?.change_pct == null
                                ? "text-muted"
                                : quote.change_pct >= 0
                                  ? "text-live"
                                  : "text-down"
                            }`}
                          >
                            {fmtPct(quote?.change_pct, 2)}
                          </td>
                          <td className="px-3 py-2 text-right text-muted tnum">
                            {fmtAge(age)}
                          </td>
                          <td className="px-3 py-2">
                            <QualityBadge quality={instrument.data_quality} />
                          </td>
                        </tr>
                      );
                    })}
                </tbody>
              </table>
            </div>
          </section>
        ))}

        <p className="text-[11px] text-muted/70">
          Quality tiers: <strong className="text-muted">official</strong> is the
          venue&apos;s own full feed;{" "}
          <strong className="text-muted">partial</strong> covers part of the
          market (Alpaca IEX);{" "}
          <strong className="text-muted">indicative</strong> is OANDA practice
          pricing. Tickets carry the same label.
        </p>
      </main>
    </>
  );
}

function QualityBadge({ quality }: { quality: string }) {
  const tone =
    quality === "official"
      ? "text-live border-live/30"
      : quality === "partial"
        ? "text-degraded border-degraded/30"
        : "text-muted border-border";
  return (
    <span className={`rounded border px-1.5 py-0.5 text-[10px] ${tone}`}>
      {quality}
    </span>
  );
}
