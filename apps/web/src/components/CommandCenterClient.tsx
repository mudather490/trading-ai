"use client";

/**
 * The client half of the Command Center.
 *
 * `page.tsx` is a Server Component: it loads the instruments, the quotes and
 * the engine heartbeat on the server, then renders this tree on the client.
 *
 * The wrappers live HERE, in a file marked "use client", because they call
 * `useSymbol()` and `useTimeframe()`. Those are React hooks, and a hook
 * cannot be called from a Server Component. Defining the wrappers in the
 * server file and calling the hooks there is exactly the mistake this
 * separation prevents.
 */

import { LiveWatchlist } from "@/components/LiveWatchlist";
import { DecisionCenter } from "@/components/DecisionCenter";
import { useSymbol } from "@/components/SymbolProvider";
import { useTimeframe } from "@/components/TimeframePicker";
import type { Instrument, Quote } from "@/lib/data";

/**
 * The watchlist, wired to the shared symbol selection.
 */
export function LiveWatchlistClient({
  instruments,
  initialQuotes,
}: {
  instruments: Instrument[];
  initialQuotes: Quote[];
}) {
  const { selected, setSelected } = useSymbol();
  return (
    <LiveWatchlist
      instruments={instruments}
      initialQuotes={initialQuotes}
      selected={selected}
      onSelect={setSelected}
    />
  );
}

/**
 * The Decision Center, wired to the shared symbol and timeframe selection.
 */
export function DecisionCenterClient() {
  const { selected } = useSymbol();
  const { timeframe } = useTimeframe();
  return <DecisionCenter symbol={selected} timeframe={timeframe} />;
}
