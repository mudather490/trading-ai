/**
 * Small, dependency-free indicator maths for the analysis panel.
 *
 * The chart is a picture; the analysis is what the picture means. Both are
 * computed from the same closed bars the signal engine sees. Nothing here is
 * a prediction, and nothing here sizes a trade.
 */

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

export interface Analysis {
  trend: "up" | "down" | "range" | "unknown";
  trendDetail: string;
  rsi: number | null;
  atrPct: number | null;
  high20: number | null;
  low20: number | null;
  change20Pct: number | null;
  last: number | null;
}

export function ema(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (values.length < period) return out;

  const k = 2 / (period + 1);
  let sum = 0;
  for (let i = 0; i < period; i++) sum += values[i];
  let value = sum / period;
  out[period - 1] = value;

  for (let i = period; i < values.length; i++) {
    value = values[i] * k + value * (1 - k);
    out[i] = value;
  }
  return out;
}

export function rsi(values: number[], period = 14): number | null {
  if (values.length <= period) return null;

  let gains = 0;
  let losses = 0;
  for (let i = 1; i <= period; i++) {
    const change = values[i] - values[i - 1];
    if (change >= 0) gains += change;
    else losses -= change;
  }
  let avgGain = gains / period;
  let avgLoss = losses / period;

  for (let i = period + 1; i < values.length; i++) {
    const change = values[i] - values[i - 1];
    avgGain = (avgGain * (period - 1) + Math.max(change, 0)) / period;
    avgLoss = (avgLoss * (period - 1) + Math.max(-change, 0)) / period;
  }

  if (avgLoss === 0) return 100;
  const rs = avgGain / avgLoss;
  return 100 - 100 / (1 + rs);
}

export function atr(candles: Candle[], period = 14): number | null {
  if (candles.length <= period) return null;

  const trueRanges: number[] = [];
  for (let i = 1; i < candles.length; i++) {
    const c = candles[i];
    const previous = candles[i - 1].close;
    trueRanges.push(
      Math.max(
        c.high - c.low,
        Math.abs(c.high - previous),
        Math.abs(c.low - previous),
      ),
    );
  }
  if (trueRanges.length < period) return null;

  let value =
    trueRanges.slice(0, period).reduce((sum, tr) => sum + tr, 0) / period;
  for (let i = period; i < trueRanges.length; i++) {
    value = (value * (period - 1) + trueRanges[i]) / period;
  }
  return value;
}

export function analyse(candles: Candle[]): Analysis {
  if (candles.length === 0) {
    return {
      trend: "unknown",
      trendDetail: "No closed bars yet.",
      rsi: null,
      atrPct: null,
      high20: null,
      low20: null,
      change20Pct: null,
      last: null,
    };
  }

  const closes = candles.map((c) => c.close);
  const last = closes[closes.length - 1];
  const e20 = ema(closes, 20).at(-1) ?? null;
  const e50 = ema(closes, 50).at(-1) ?? null;
  const e200 = ema(closes, 200).at(-1) ?? null;

  let trend: Analysis["trend"] = "range";
  if (e20 != null && e50 != null && e200 != null) {
    if (last > e20 && e20 > e50 && e50 > e200) trend = "up";
    else if (last < e20 && e20 < e50 && e50 < e200) trend = "down";
  } else if (e20 != null && e50 != null) {
    if (last > e20 && e20 > e50) trend = "up";
    else if (last < e20 && e20 < e50) trend = "down";
  }

  const format = (v: number | null) =>
    v == null ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: 2 });

  const window = candles.slice(-20);
  const high20 = Math.max(...window.map((c) => c.high));
  const low20 = Math.min(...window.map((c) => c.low));
  const first20 = window[0]?.close ?? null;
  const atrValue = atr(candles, 14);

  return {
    trend,
    trendDetail: `EMA20 ${format(e20)} · EMA50 ${format(e50)} · EMA200 ${format(e200)}`,
    rsi: rsi(closes, 14),
    atrPct: atrValue != null && last > 0 ? atrValue / last : null,
    high20,
    low20,
    change20Pct:
      first20 != null && first20 > 0 ? (last - first20) / first20 : null,
    last,
  };
}

/* --------------------------------------------------------------- sessions */

export interface SessionState {
  open: boolean;
  label: string;
}

/**
 * Is the venue's session open?
 *
 * This mirrors `market_open()` in migration 0008. It is an approximation, not
 * an exchange calendar: weekends and regular US hours are known, holidays are
 * not. The panel uses it to explain why a price is old, and it must agree with
 * the database or the UI will contradict the ticket.
 */
export function marketOpen(
  assetClass: string,
  at: Date = new Date(),
): SessionState {
  if (assetClass === "crypto") {
    return { open: true, label: "24/7" };
  }

  if (assetClass === "us_equity") {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(at);
    const get = (type: string) =>
      parts.find((part) => part.type === type)?.value ?? "";
    const weekday = get("weekday");
    const minutes = Number(get("hour")) * 60 + Number(get("minute"));
    const weekend = weekday === "Sat" || weekday === "Sun";
    const inHours = minutes >= 9 * 60 + 30 && minutes < 16 * 60;

    return {
      open: !weekend && inHours,
      label: weekend
        ? "Weekend — closed"
        : inHours
          ? "US session"
          : "US session closed",
    };
  }

  // FX and metals: closed from Friday 22:00 UTC to Sunday 22:00 UTC.
  const day = at.getUTCDay();
  const minutes = at.getUTCHours() * 60 + at.getUTCMinutes();
  const closed =
    day === 6 ||
    (day === 5 && minutes >= 22 * 60) ||
    (day === 0 && minutes < 22 * 60);

  return {
    open: !closed,
    label: closed ? "Weekend — closed" : "FX session",
  };
}
