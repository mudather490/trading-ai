/**
 * One command for the whole local stack.
 *
 *   npm run dev:local
 *
 * Starts three things and keeps them together:
 *
 *   1. the local Supabase (real Postgres in WASM, the real migrations, the
 *      real RLS policies, a PostgREST/GoTrue/Realtime surface),
 *   2. the live worker (streaming Binance — keyless, so the prices are
 *      real market prices),
 *   3. the Next.js app.
 *
 * No accounts, no keys, no cloud. The one thing you cannot have locally is
 * a magic-link EMAIL: the local auth server accepts the request and hands
 * out a session directly, so sign in by visiting
 *
 *   http://localhost:<port>/auth/callback?code=demo
 *
 * Ctrl+C stops everything.
 */

import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const LOCAL_SUPABASE_URL = "http://127.0.0.1:54321";
const LOCAL_ANON_KEY = "local-anon-key";
const LOCAL_SERVICE_KEY = "local-service-role-key";

const children = [];
let shuttingDown = false;

function start(name, command, args, options = {}) {
  const entry = { name, child: null, restarts: 0 };
  children.push(entry);

  const launch = () => {
    const child = spawn(command, args, {
      cwd: options.cwd ?? ROOT,
      env: { ...process.env, ...(options.env ?? {}) },
      stdio: "inherit",
      shell: process.platform === "win32",
    });
    entry.child = child;

    child.on("exit", (code) => {
      if (shuttingDown) return;
      console.log(`\n[local] ${name} exited (${code})`);

      // The worker is a long-lived service. If it dies, the stack would keep
      // running with a dead engine, and every screen would say "engine down"
      // for a reason that has nothing to do with the data. Bring it back,
      // with a backoff so a crash loop does not spin the CPU.
      if (options.restart === true) {
        entry.restarts += 1;
        const delay = Math.min(1000 * entry.restarts, 15000);
        console.log(`[local] restarting ${name} in ${delay / 1000}s`);
        setTimeout(() => {
          if (!shuttingDown) launch();
        }, delay);
        return;
      }

      if (code !== 0 && code !== null) shutdown();
    });
  };

  launch();
  return entry;
}

function shutdown() {
  shuttingDown = true;
  for (const { child } of children) {
    if (!child.killed) {
      try {
        child.kill();
      } catch {
        // already gone
      }
    }
  }
  children.length = 0;
}

process.on("SIGINT", () => {
  console.log("\n[local] stopping the stack");
  shutdown();
  process.exit(0);
});
process.on("SIGTERM", () => {
  shutdown();
  process.exit(0);
});

console.log("[local] starting the local Supabase on " + LOCAL_SUPABASE_URL);
start("supabase", process.execPath, [join(ROOT, "tools", "local_supabase.mjs")]);

setTimeout(() => {
  console.log("[local] starting the live worker (Binance, keyless)");
  start("worker", "python", ["-m", "worker.main"], {
    cwd: join(ROOT, "services", "live-worker"),
    restart: true,
    env: {
      SUPABASE_URL: LOCAL_SUPABASE_URL,
      SUPABASE_SERVICE_ROLE_KEY: LOCAL_SERVICE_KEY,
      ENABLED_VENUES: process.env.ENABLED_VENUES ?? "binance",
      WORKER_ID: process.env.WORKER_ID ?? "live-worker-local",
      LOG_LEVEL: process.env.LOG_LEVEL ?? "INFO",
      // The worker owns OUTBOUND delivery. Without a token it simply does
      // not deliver, and says so in the log.
      TELEGRAM_BOT_TOKEN: process.env.TELEGRAM_BOT_TOKEN ?? "",
    },
  });

  console.log("[local] starting the web app");
  start("web", "npm", ["--prefix", join(ROOT, "apps", "web"), "run", "dev"], {
    env: {
      NEXT_PUBLIC_SUPABASE_URL: LOCAL_SUPABASE_URL,
      NEXT_PUBLIC_SUPABASE_ANON_KEY: LOCAL_ANON_KEY,
      // The Telegram webhook needs the service role: a Telegram chat is not
      // a Supabase session, so the webhook cannot use the user's session.
      // Locally this is the emulator's fixed local key, not a real secret.
      SUPABASE_SERVICE_ROLE_KEY: LOCAL_SERVICE_KEY,
      TELEGRAM_BOT_TOKEN: process.env.TELEGRAM_BOT_TOKEN ?? "",
      TELEGRAM_WEBHOOK_SECRET: process.env.TELEGRAM_WEBHOOK_SECRET ?? "",
      TELEGRAM_ALLOWED_CHAT_IDS: process.env.TELEGRAM_ALLOWED_CHAT_IDS ?? "",
    },
  });

  console.log(
    "\n[local] ready. Sign in at http://localhost:3000/auth/callback?code=demo\n" +
      "[local] (if port 3000 is taken, Next.js will print the port it used)",
  );
}, 6000);
