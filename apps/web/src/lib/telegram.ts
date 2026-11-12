import { fmt, isTradeTicket, type NonTicket, type TicketResponse } from "@/lib/types";

/**
 * Telegram rendering.
 *
 * Two rules hold everywhere in this file:
 *
 *   1. Plain text only. No parse_mode, no HTML, no Markdown. A headline from
 *      a news feed can contain anything, and Telegram's parsers turn that
 *      into broken messages (or a surprise). The content is untrusted; the
 *      format is ours.
 *   2. The same facts as the dashboard. If the bot says a price, it is the
 *      price in the ticket, and the ticket carries its data-quality label.
 *      A ticket built on IEX-only data must not look consolidated in a chat
 *      message.
 */

export interface TelegramButton {
  text: string;
  callback_data: string;
}

export type TelegramKeyboard = { inline_keyboard: TelegramButton[][] };

export function keyboard(rows: TelegramButton[][]): TelegramKeyboard {
  return { inline_keyboard: rows };
}

export const TIMEFRAMES = ["1m", "5m", "1h", "1d"] as const;

export function symbolsKeyboard(symbols: string[]): TelegramKeyboard {
  const rows: TelegramButton[][] = [];
  for (let i = 0; i < symbols.length; i += 3) {
    rows.push(
      symbols.slice(i, i + 3).map((symbol) => ({
        text: symbol,
        callback_data: `sym:${symbol}`,
      })),
    );
  }
  return keyboard(rows);
}

export function timeframeKeyboard(symbol: string): TelegramKeyboard {
  return keyboard([
    TIMEFRAMES.map((tf) => ({
      text: tf,
      callback_data: `tf:${tf}:${symbol}`,
    })),
  ]);
}

/**
 * The ticket card.
 *
 * The same five states the dashboard shows, rendered for a phone. A refusal
 * is a success state and is rendered as one: the bot says exactly why there
 * is no ticket, and it never invents one.
 */
export function formatTicket(ticket: TicketResponse): string {
  if (!isTradeTicket(ticket)) return formatRefusal(ticket);

  const lines: string[] = [];

  const arrow = ticket.side === "buy" ? "BUY" : ticket.side === "sell" ? "SELL" : "FLAT";
  lines.push(`${arrow} ${ticket.instrument} · ${ticket.timeframe}`);
  lines.push("");

  if (ticket.entry_low != null && ticket.entry_high != null) {
    lines.push(
      `Entry   ${fmt(ticket.entry_low, 4)} – ${fmt(ticket.entry_high, 4)}`,
    );
  } else {
    lines.push(`Entry   ${fmt(ticket.entry_price, 4)}`);
  }
  lines.push(`Stop    ${fmt(ticket.stop_price, 4)}`);
  lines.push(`Target  ${fmt(ticket.target_price, 4)}`);
  if (ticket.target_2_price != null) {
    lines.push(`Target2 ${fmt(ticket.target_2_price, 4)}`);
  }
  lines.push(
    `Size    ${fmt(ticket.size_units, 6)} units${ticket.size_notional != null ? ` ($${fmt(ticket.size_notional, 2)})` : ""}`,
  );
  lines.push(
    `Risk    $${fmt(ticket.risk_amount, 2)}${ticket.reward_risk != null ? ` · R:R ${fmt(ticket.reward_risk, 2)}` : ""}`,
  );

  lines.push("");
  lines.push(
    `Strategy ${ticket.strategy_name} v${ticket.version}${ticket.rule_id ? ` (${ticket.rule_id})` : ""}`,
  );
  lines.push(
    `Data     ${ticket.data_source} · ${ticket.data_quality} · as of ${ticket.data_as_of}`,
  );
  lines.push(`Bar close ${ticket.signal_bar_close_time}`);

  if (ticket.warnings.length > 0) {
    lines.push("");
    for (const warning of ticket.warnings) {
      lines.push(`! ${warning.text}`);
    }
  }

  lines.push("");
  lines.push("The app advises. You place the trade.");

  return lines.join("\n");
}

export function formatRefusal(ticket: NonTicket): string {
  const headline = REFUSAL_HEADLINES[ticket.reason_code ?? ""] ?? {
    title: ticket.status.replaceAll("_", " "),
    hint: "",
  };

  const lines = [`${headline.title} · ${ticket.instrument ?? ""}`.trim()];
  lines.push("");
  lines.push(ticket.reason);
  if (ticket.reason_code) lines.push(`(${ticket.reason_code})`);
  if (headline.hint) {
    lines.push("");
    lines.push(headline.hint);
  }
  return lines.join("\n");
}

const REFUSAL_HEADLINES: Record<string, { title: string; hint: string }> = {
  engine_down: {
    title: "ENGINE UNAVAILABLE",
    hint: "The live worker is not running, so no ticket can be issued.",
  },
  no_profile: {
    title: "NO RISK PROFILE",
    hint: "Complete onboarding in the app so the engine knows the budget.",
  },
  unknown_instrument: { title: "UNKNOWN INSTRUMENT", hint: "" },
  not_in_universe: { title: "NOT IN THE UNIVERSE", hint: "" },
  no_data: {
    title: "NO PRICE DATA YET",
    hint: "The venue is connected but no bar has arrived yet.",
  },
  feed_not_configured: {
    title: "FEED NOT CONNECTED",
    hint: "Add the venue credentials to the worker and restart it.",
  },
  market_closed: {
    title: "MARKET CLOSED",
    hint: "No fresh price is expected while the session is shut.",
  },
  quote_too_old: {
    title: "DATA IS STALE",
    hint: "The session is open but the quote is older than the safety window.",
  },
  no_champion: {
    title: "NO PROMOTED STRATEGY",
    hint: "Nothing has passed the promotion gate for this asset class yet.",
  },
  no_signal: { title: "NO SETUP RIGHT NOW", hint: "The champion is waiting." },
  no_stop: {
    title: "NO VALID STOP",
    hint: "A setup without a stop is not a trade.",
  },
  risk_blocked: {
    title: "RISK LIMIT REACHED",
    hint: "A position cap or the daily ticket limit refused the size.",
  },
  rr_below_floor: { title: "REWARD:RISK TOO LOW", hint: "" },
};

export function helpText(): string {
  return [
    "Trading Command Center — bot",
    "",
    "/link <code>   link this chat to your account (code from Settings)",
    "/unlink        stop sending messages to this chat",
    "/signal <SYMBOL> <tf>   the current ticket",
    "/symbols       the tracked universe",
    "/status        engine and feed health",
    "/help          this message",
    "",
    "Timeframes: 1m · 5m · 1h · 1d",
    "A refusal is a valid answer. The bot will not invent a setup.",
  ].join("\n");
}

/**
 * Call the Bot API.
 *
 * Errors are returned, not thrown: a Telegram outage must not break the
 * webhook's own bookkeeping.
 */
export async function telegramCall(
  token: string,
  method: string,
  payload: Record<string, unknown>,
): Promise<{ ok: boolean; description?: string }> {
  try {
    const res = await fetch(`https://api.telegram.org/bot${token}/${method}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = (await res.json().catch(() => ({}))) as {
      ok?: boolean;
      description?: string;
    };
    return { ok: Boolean(data.ok), description: data.description };
  } catch (err) {
    return {
      ok: false,
      description: err instanceof Error ? err.message : "network error",
    };
  }
}
