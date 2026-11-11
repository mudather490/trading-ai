"use client";

import { createContext, useContext, useMemo, useState } from "react";

/**
 * The selected timeframe.
 *
 * The decision engine evaluates 1-minute closed bars and level triggers.
 * "1h" and "1d" mean the strategy's own bar, not the chart's. The UI must be
 * clear about which is which, or the user will read a 1h chart and think the
 * signal came from that candle.
 */
const TIMEFRAMES = ["1m", "5m", "1h", "1d"] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];

const TimeframeContext = createContext<{
  timeframe: Timeframe;
  setTimeframe: (t: Timeframe) => void;
}>({ timeframe: "1h", setTimeframe: () => {} });

export function TimeframePicker() {
  const { timeframe, setTimeframe } = useTimeframe();

  return (
    <div className="flex items-center rounded border border-border overflow-hidden">
      {TIMEFRAMES.map((tf) => (
        <button
          key={tf}
          onClick={() => setTimeframe(tf)}
          className={`px-2 py-1 text-[11px] transition-colors ${
            timeframe === tf
              ? "bg-panel-2 text-foreground"
              : "text-muted hover:text-foreground"
          }`}
        >
          {tf}
        </button>
      ))}
    </div>
  );
}

export function TimeframeProvider({ children }: { children: React.ReactNode }) {
  const [timeframe, setTimeframe] = useState<Timeframe>("1h");
  const value = useMemo(() => ({ timeframe, setTimeframe }), [timeframe]);
  return (
    <TimeframeContext.Provider value={value}>{children}</TimeframeContext.Provider>
  );
}

export function useTimeframe() {
  return useContext(TimeframeContext);
}
