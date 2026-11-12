/**
 * Register the Telegram webhook.
 *
 *   node tools/telegram_setup.mjs set    https://your-app.vercel.app/api/telegram/webhook
 *   node tools/telegram_setup.mjs info
 *   node tools/telegram_setup.mjs delete
 *
 * The secret token is the SAME value as TELEGRAM_WEBHOOK_SECRET in the
 * server environment. Telegram sends it back in the
 * `x-telegram-bot-api-secret-token` header on every update, and the webhook
 * refuses anything without it. That header is what proves a request came
 * from Telegram: without it, anyone who learns the URL could make the bot
 * send messages as if the user had asked.
 *
 * Requires TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET in the environment.
 */

const token = process.env.TELEGRAM_BOT_TOKEN;
const secret = process.env.TELEGRAM_WEBHOOK_SECRET;
const command = process.argv[2] ?? "info";
const url = process.argv[3];

if (!token) {
  console.error("TELEGRAM_BOT_TOKEN is not set.");
  process.exit(1);
}

async function call(method, payload = {}) {
  const res = await fetch(`https://api.telegram.org/bot${token}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  if (!data.ok) {
    console.error(`Telegram refused ${method}: ${data.description ?? "unknown"}`);
    process.exit(1);
  }
  return data.result;
}

if (command === "set") {
  if (!url) {
    console.error("Usage: node tools/telegram_setup.mjs set <webhook url>");
    process.exit(1);
  }
  if (!secret) {
    console.error("TELEGRAM_WEBHOOK_SECRET is not set.");
    process.exit(1);
  }
  const result = await call("setWebhook", {
    url,
    secret_token: secret,
    allowed_updates: ["message", "callback_query"],
    drop_pending_updates: false,
  });
  console.log(`webhook set: ${result ? "ok" : "unknown"}`);
  console.log(`  ${url}`);
  console.log("  secret header: x-telegram-bot-api-secret-token");
} else if (command === "delete") {
  await call("deleteWebhook", { drop_pending_updates: false });
  console.log("webhook deleted");
} else {
  const info = await call("getWebhookInfo");
  console.log(JSON.stringify(info, null, 2));
}
