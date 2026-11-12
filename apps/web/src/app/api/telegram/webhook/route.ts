import { NextResponse } from "next/server";
import {
  formatTicket,
  helpText,
  keyboard,
  symbolsKeyboard,
  telegramCall,
  timeframeKeyboard,
  type TelegramKeyboard,
} from "@/lib/telegram";
import type { TicketResponse } from "@/lib/types";

/**
 * The service-role client, created here and nowhere else.
 *
 * The webhook is one of exactly two places in the web tier that may hold the
 * service-role key (the other is the cron heartbeat), because a Telegram chat
 * is not a Supabase session: there is no cookie to read and no user to
 * impersonate. The key bypasses RLS, so it is used for exactly two things
 * here — reading the chat-to-user link, and asking `get_ticket()` for that
 * user. It never reads another user's data.
 */
async function createAdminClient() {
  const { createClient } = await import("@supabase/supabase-js");
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;

  if (!url || !key) {
    throw new Error(
      "NEXT_PUBLIC_SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for the webhook.",
    );
  }

  return createClient(url, key, {
    auth: { autoRefreshToken: false, persistSession: false },
  });
}

/**
 * POST /api/telegram/webhook
 *
 * The bot's front door. Telegram POSTs every update here.
 *
 * What this route does, in order:
 *   1. proves it is Telegram (the secret header we set with setWebhook),
 *   2. records the update, so a replay is a no-op,
 *   3. checks the chat against the allowlist,
 *   4. rate-limits the chat,
 *   5. resolves the chat to a USER (never the other way round),
 *   6. runs the command.
 *
 * What it never does:
 *   * take an order,
 *   * compute a size,
 *   * invent a ticket. It calls the same `get_ticket()` the dashboard calls,
 *     so the bot and the screen can never disagree.
 */

interface Update {
  update_id: number;
  message?: {
    text?: string;
    chat: { id: number; username?: string; first_name?: string };
    from?: { id: number; username?: string };
  };
  callback_query?: {
    id: string;
    data?: string;
    from?: { id: number };
    message?: { chat: { id: number } };
  };
}

const RATE_LIMIT_PER_MINUTE = 20;
const rateWindow = new Map<number, number[]>();

function parseAllowlist(raw: string | undefined): string[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((value) => value.trim())
    .filter((value) => value.length > 0);
}

function rateLimited(chatId: number): boolean {
  const now = Date.now();
  const recent = (rateWindow.get(chatId) ?? []).filter(
    (stamp) => now - stamp < 60_000,
  );
  if (recent.length >= RATE_LIMIT_PER_MINUTE) {
    rateWindow.set(chatId, recent);
    return true;
  }
  recent.push(now);
  rateWindow.set(chatId, recent);
  return false;
}

export async function POST(request: Request) {
  const secret = process.env.TELEGRAM_WEBHOOK_SECRET;
  const token = process.env.TELEGRAM_BOT_TOKEN;

  if (!secret || !token) {
    return NextResponse.json(
      { error: "Telegram is not configured on the server." },
      { status: 500 },
    );
  }

  // Telegram sends this exact header when a secret_token was set on the
  // webhook. It is the only thing that proves the request came from Telegram.
  const provided = request.headers.get("x-telegram-bot-api-secret-token");
  if (!provided || provided !== secret) {
    return NextResponse.json({ error: "unauthorised" }, { status: 403 });
  }

  let update: Update;
  try {
    update = (await request.json()) as Update;
  } catch {
    return NextResponse.json({ error: "invalid body" }, { status: 400 });
  }

  const updateId = Number(update?.update_id);
  if (!Number.isFinite(updateId)) {
    return NextResponse.json({ error: "no update_id" }, { status: 400 });
  }

  const admin = await createAdminClient();

  // ---- dedupe -----------------------------------------------------------
  const { data: seen } = await admin
    .from("telegram_updates")
    .select("update_id")
    .eq("update_id", updateId)
    .maybeSingle();

  if (seen) {
    return NextResponse.json({ ok: true, duplicate: true });
  }

  const message = update.message;
  const callback = update.callback_query;
  const chatId = message?.chat?.id ?? callback?.message?.chat?.id ?? null;
  const text = message?.text ?? null;
  const callbackData = callback?.data ?? null;

  const { error: insertError } = await admin
    .from("telegram_updates")
    .insert({
      update_id: updateId,
      chat_id: chatId,
      telegram_user_id: message?.from?.id ?? callback?.from?.id ?? null,
      message: text ?? callbackData,
      payload: update as unknown as Record<string, unknown>,
    });

  // A duplicate that raced past the SELECT above is still a duplicate.
  if (insertError && insertError.code === "23505") {
    return NextResponse.json({ ok: true, duplicate: true });
  }

  if (chatId === null) {
    return NextResponse.json({ ok: true });
  }

  // ---- allowlist --------------------------------------------------------
  const allowlist = parseAllowlist(process.env.TELEGRAM_ALLOWED_CHAT_IDS);
  if (allowlist.length > 0 && !allowlist.includes(String(chatId))) {
    // Record it and say nothing. An unlisted chat gets no answer at all.
    return NextResponse.json({ ok: true, ignored: "not_allowed" });
  }

  if (rateLimited(chatId)) {
    await telegramCall(token, "sendMessage", {
      chat_id: chatId,
      text: "Too many requests. Try again in a minute.",
    });
    return NextResponse.json({ ok: true, ignored: "rate_limited" });
  }

  // ---- who is this chat? ------------------------------------------------
  const { data: link } = await admin
    .from("telegram_links")
    .select("user_id,username,revoked_at")
    .eq("chat_id", chatId)
    .maybeSingle();

  const userId = link && !link.revoked_at ? (link.user_id as string) : null;

  if (callback) {
    await handleCallback({
      admin,
      token,
      chatId,
      callbackId: callback.id,
      data: callbackData,
      userId,
    });
  } else if (text) {
    await handleCommand({
      admin,
      token,
      chatId,
      text,
      username: message?.chat?.username ?? message?.from?.username ?? null,
      userId,
    });
  }

  await admin
    .from("telegram_updates")
    .update({ handled_at: new Date().toISOString() })
    .eq("update_id", updateId);

  return NextResponse.json({ ok: true });
}

/* ------------------------------------------------------------- commands */

interface CommandContext {
  admin: Awaited<ReturnType<typeof createAdminClient>>;
  token: string;
  chatId: number;
  text: string;
  username: string | null;
  userId: string | null;
}

async function handleCommand(ctx: CommandContext) {
  const { admin, token, chatId, userId } = ctx;

  // "/help@MyBot args" -> "help args"
  const raw = ctx.text.trim();
  const withoutBot = raw.replace(/^(\/\w+)@\S+/, "$1");
  const parts = withoutBot.split(/\s+/);
  const command = (parts[0] ?? "").toLowerCase();
  const args = parts.slice(1).filter(Boolean);

  if (command === "/start") {
    await telegramCall(token, "sendMessage", {
      chat_id: chatId,
      text: userId
        ? "Linked. Ask for a signal:\n/signal BTCUSDT 1h\n\n/symbols to browse the universe, /status for engine health."
        : "This bot is not linked yet.\n\n1. Open the app → Settings → Telegram → Generate link code.\n2. Send: /link <the code>\n\nThe code is single use and expires in 10 minutes.",
      reply_markup: userId ? mainKeyboard() : undefined,
    });
    return;
  }

  if (command === "/help") {
    await telegramCall(token, "sendMessage", {
      chat_id: chatId,
      text: helpText(),
    });
    return;
  }

  if (command === "/link") {
    const code = args[0];
    if (!code) {
      await telegramCall(token, "sendMessage", {
        chat_id: chatId,
        text: "Usage: /link <code>. Generate a code in the app under Settings → Telegram.",
      });
      return;
    }
    const { data, error } = await admin.rpc("redeem_telegram_link", {
      p_code: code,
      p_chat_id: chatId,
      p_username: ctx.username,
    });
    if (error || !data?.ok) {
      await telegramCall(token, "sendMessage", {
        chat_id: chatId,
        text:
          data?.reason ??
          "That code could not be redeemed. It may have expired or already been used.",
      });
      return;
    }
    await telegramCall(token, "sendMessage", {
      chat_id: chatId,
      text: "Linked. Ask for a signal:\n/signal BTCUSDT 1h\n\n/symbols to browse the universe.",
      reply_markup: mainKeyboard(),
    });
    return;
  }

  if (command === "/unlink") {
    const { data, error } = await admin.rpc("revoke_telegram_link", {
      p_chat_id: chatId,
    });
    await telegramCall(token, "sendMessage", {
      chat_id: chatId,
      text: error
        ? "Could not unlink."
        : (data?.revoked ?? 0) > 0
          ? "Unlinked. This chat will receive nothing."
          : "This chat was not linked.",
    });
    return;
  }

  if (command === "/symbols") {
    const { data: instruments } = await admin
      .from("instruments")
      .select("symbol")
      .eq("in_universe", true)
      .order("symbol");
    const symbols = (instruments ?? []).map((row) => row.symbol as string);
    await telegramCall(token, "sendMessage", {
      chat_id: chatId,
      text: "Pick an instrument:",
      reply_markup: symbolsKeyboard(symbols),
    });
    return;
  }

  if (command === "/status") {
    await telegramCall(token, "sendMessage", {
      chat_id: chatId,
      text: await statusText(admin),
    });
    return;
  }

  if (command === "/signal") {
    const symbol = (args[0] ?? "").toUpperCase();
    const timeframe = (args[1] ?? "1h").toLowerCase();

    if (!symbol) {
      const { data: instruments } = await admin
        .from("instruments")
        .select("symbol")
        .eq("in_universe", true)
        .order("symbol");
      const symbols = (instruments ?? []).map((row) => row.symbol as string);
      await telegramCall(token, "sendMessage", {
        chat_id: chatId,
        text: "Which instrument?",
        reply_markup: symbolsKeyboard(symbols),
      });
      return;
    }

    await sendSignal({ ...ctx, symbol, timeframe });
    return;
  }

  // A bare symbol ("BTCUSDT 1h") is treated as /signal.
  const bare = raw.split(/\s+/)[0]?.toUpperCase() ?? "";
  if (bare && bare !== command) {
    const timeframe = raw.split(/\s+/)[1]?.toLowerCase() ?? "1h";
    await sendSignal({ ...ctx, symbol: bare, timeframe });
    return;
  }

  await telegramCall(token, "sendMessage", {
    chat_id: chatId,
    text: helpText(),
  });
}

/* ------------------------------------------------------------ callbacks */

async function handleCallback({
  admin,
  token,
  chatId,
  callbackId,
  data,
  userId,
}: {
  admin: Awaited<ReturnType<typeof createAdminClient>>;
  token: string;
  chatId: number;
  callbackId: string;
  data: string | null;
  userId: string | null;
}) {
  await telegramCall(token, "answerCallbackQuery", {
    callback_query_id: callbackId,
  });

  if (!data) return;

  const [kind, ...rest] = data.split(":");

  if (kind === "sym") {
    const symbol = rest[0];
    if (!symbol) return;
    await telegramCall(token, "sendMessage", {
      chat_id: chatId,
      text: `${symbol} — pick a timeframe:`,
      reply_markup: timeframeKeyboard(symbol),
    });
    return;
  }

  if (kind === "tf") {
    const [timeframe, symbol] = rest;
    if (!timeframe || !symbol) return;
    await sendSignal({ admin, token, chatId, userId, symbol, timeframe });
    return;
  }

  if (kind === "menu") {
    await telegramCall(token, "sendMessage", {
      chat_id: chatId,
      text: "What do you want to look at?",
      reply_markup: mainKeyboard(),
    });
  }
}

/* --------------------------------------------------------------- helpers */

async function sendSignal({
  admin,
  token,
  chatId,
  userId,
  symbol,
  timeframe,
}: {
  admin: Awaited<ReturnType<typeof createAdminClient>>;
  token: string;
  chatId: number;
  userId: string | null;
  symbol: string;
  timeframe: string;
}) {
  if (!userId) {
    await telegramCall(token, "sendMessage", {
      chat_id: chatId,
      text: "This chat is not linked to an account. Send /link <code> first.",
    });
    return;
  }

  const ticket = await ticketFor(admin, userId, symbol, timeframe);
  if (!ticket) {
    await telegramCall(token, "sendMessage", {
      chat_id: chatId,
      text: `${symbol.toUpperCase()} is not in the tracked universe.`,
    });
    return;
  }

  await telegramCall(token, "sendMessage", {
    chat_id: chatId,
    text: formatTicket(ticket),
  });
}

async function ticketFor(
  admin: Awaited<ReturnType<typeof createAdminClient>>,
  userId: string,
  symbol: string,
  timeframe: string,
): Promise<TicketResponse | null> {
  const { data: instrument } = await admin
    .from("instruments")
    .select("id")
    .eq("symbol", symbol.toUpperCase())
    .eq("in_universe", true)
    .maybeSingle();

  if (!instrument) return null;

  const { data, error } = await admin.rpc("get_ticket", {
    p_user_id: userId,
    p_instrument: instrument.id,
    p_timeframe: timeframe,
  });

  if (error) {
    return {
      status: "engine_unavailable",
      reason_code: "engine_down",
      reason: "The ticket engine did not respond.",
    };
  }
  return (data as TicketResponse | null) ?? null;
}

async function statusText(
  admin: Awaited<ReturnType<typeof createAdminClient>>,
): Promise<string> {
  const { data: engine } = await admin.rpc("engine_status");
  const { data: feeds } = await admin.rpc("feed_health");

  const engineStatus = (engine as { status?: string } | null)?.status ?? "never_seen";
  const lines = [`Engine: ${engineStatus}`];

  const venues = (feeds as { venues?: { venue: string; status: string; with_quotes: number; symbols: number }[] } | null)
    ?.venues ?? [];
  for (const venue of venues) {
    lines.push(
      `${venue.venue}: ${venue.status} (${venue.with_quotes}/${venue.symbols} symbols)`,
    );
  }
  lines.push("");
  lines.push("A stale or unconnected feed is a data state, not a signal.");
  return lines.join("\n");
}

function mainKeyboard(): TelegramKeyboard {
  return keyboard([
    [
      { text: "Signals", callback_data: "menu" },
      { text: "Symbols", callback_data: "sym:BTCUSDT" },
    ],
  ]);
}
