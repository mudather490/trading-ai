/**
 * Local Telegram relay.
 *
 *   node tools/telegram_longpoll.mjs
 *
 * Telegram cannot reach http://localhost, so on a laptop the bot is tested
 * the other way round: this script long-polls the Bot API for updates and
 * POSTs each one to the local webhook, with the same secret header Telegram
 * would send. The webhook code path is therefore the SAME code path that
 * runs in production — only the transport is different.
 *
 * Requires:
 *   TELEGRAM_BOT_TOKEN        the bot token from @BotFather
 *   TELEGRAM_WEBHOOK_SECRET   the same value the app uses
 * Optional:
 *   WEBHOOK_URL               defaults to http://127.0.0.1:3000/api/telegram/webhook
 *
 * NOTE: getUpdates and setWebhook cannot be used at the same time. If the
 * webhook is registered with Telegram, delete it first:
 *   node tools/telegram_setup.mjs delete
 */

import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { existsSync, readFileSync } from "node:fs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

/** Read a .env file into the environment without overriding what is set. */
function loadEnv() {
  for (const candidate of [
    join(ROOT, "services", "live-worker", ".env"),
    join(ROOT, ".env"),
  ]) {
    if (!existsSync(candidate)) continue;
    for (const line of readFileSync(candidate, "utf8").split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const eq = trimmed.indexOf("=");
      if (eq < 0) continue;
      const key = trimmed.slice(0, eq).trim();
      const value = trimmed
        .slice(eq + 1)
        .trim()
        .replace(/^["']|["']$/g, "");
      if (process.env[key] === undefined) process.env[key] = value;
    }
  }
}

loadEnv();

const token = process.env.TELEGRAM_BOT_TOKEN;
const secret = process.env.TELEGRAM_WEBHOOK_SECRET;
const webhookUrl =
  process.env.WEBHOOK_URL ?? "http://127.0.0.1:3000/api/telegram/webhook";

if (!token) {
  console.error("TELEGRAM_BOT_TOKEN is not set.");
  process.exit(1);
}
if (!secret) {
  console.error("TELEGRAM_WEBHOOK_SECRET is not set.");
  process.exit(1);
}

console.log(`relaying Telegram updates to ${webhookUrl}`);

let stopping = false;
process.on("SIGINT", () => {
  stopping = true;
});

while (!stopping) {
  try {
    const res = await fetch(
      `https://api.telegram.org/bot${token}/getUpdates?timeout=25&allowed_updates=%5B%22message%22%2C%22callback_query%22%5D`,
      { method: "POST", headers: { "Content-Type": "application/json" } },
    );
    const data = await res.json().catch(() => ({}));
    if (!data.ok) {
      console.error("getUpdates failed:", data.description ?? "unknown");
      await new Promise((resolve) => setTimeout(resolve, 5000));
      continue;
    }

    for (const update of data.result ?? []) {
      const response = await fetch(webhookUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-telegram-bot-api-secret-token": secret,
        },
        body: JSON.stringify(update),
      });
      console.log(
        `update ${update.update_id} → ${response.status} ${await response.text()}`,
      );
    }
  } catch (err) {
    console.error("relay error:", err instanceof Error ? err.message : err);
    await new Promise((resolve) => setTimeout(resolve, 5000));
  }
}
