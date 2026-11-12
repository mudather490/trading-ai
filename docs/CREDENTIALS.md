# Credentials checklist

Everything you need to supply, in one table, with the exact place to get it
and the exact tier that may hold it. Nothing here is ever pasted into a chat
and nothing here is ever committed.

**Rule of thumb:** a credential belongs to the tier that uses it, and no
other. The web tier cannot trade because it holds no venue keys. The worker
cannot place orders because it holds no broker write keys.

---

## 1. Supabase ← required

| Value | Where to get it | Where it goes | Notes |
|---|---|---|---|
| Project URL | Dashboard → Settings → API → Project URL | `NEXT_PUBLIC_SUPABASE_URL` (web) + `SUPABASE_URL` (worker) | `https://xxx.supabase.co` |
| `anon` public key | Dashboard → Settings → API → `anon` | `NEXT_PUBLIC_SUPABASE_ANON_KEY` (web) | Safe in the browser; RLS decides |
| `service_role` key | Dashboard → Settings → API → `service_role` | `SUPABASE_SERVICE_ROLE_KEY` (worker **and** web) | Bypasses RLS. Treat as a password. |
| Database password | Dashboard → Settings → Database → URI | only for `psql` / the CLI | Needed for `supabase db push` |
| Cron secret | you invent | `CRON_SECRET` (web) | `openssl rand -hex 32` |

The web tier uses the service-role key in exactly two routes:
`/api/cron/heartbeat` and `/api/telegram/webhook`. Every user-scoped read
goes through the user's own session, so RLS still applies to them.

---

## 2. Market data ← all optional, the app starts without them

| Venue | Where to get it | Env vars (worker only) | What it adds |
|---|---|---|---|
| **Binance** | nothing — the market stream is public | none | BTCUSDT, ETHUSDT, SOLUSDT. *official* full-market data. |
| **Alpaca** | <https://app.alpaca.markets> → **Paper Trading** → API keys | `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY` | QQQ, SPY, GLD, AAPL, NVDA, TLT. *partial* (IEX only). |
| **OANDA** | <https://developer.oanda.com> → **fxTrade Practice** → REST API key | `OANDA_TOKEN`, `OANDA_ACCOUNT_ID` | EURUSD, GBPUSD, XAUUSD. *indicative* (practice rates). |
| **Finnhub** | <https://finnhub.io> → register | `FINNHUB_API_KEY` | The economic calendar (event-risk warnings). |
| **Twelve Data** | <https://twelvedata.com> → register | `TWELVE_DATA_API_KEY` (**research worker only**) | A cross-check of the daily bars. See the warning below. |

A venue with no credentials is **skipped with a warning**, never fatal, and
the UI says "not connected" rather than "stale".

> **Alpaca and OANDA are the two that matter most.** The crypto rows are
> already live; the six US equities and the three FX/metals rows are empty
> until those two keys exist, and no other provider can fill them on a free
> plan. Both are free.

> **Twelve Data is a research tool, not a feed.** Its free plan is
> *"internal non-display usage only"*: the data may be fetched and used
> internally but **not shown in the app** — not on a chart, not on a
> watchlist, not in a ticket. Display access starts on the $29/month Grow
> plan. So the key lives in the **research worker only**, the tool is
> `python -m research.crosscheck`, and it prints a comparison to the
> terminal. Free allowance: 8 credits a minute, 800 a day; the cross-check
> of the six equities costs six credits a day.

---

## 3. AI (optional) ← the app works without it

| Provider | Where to get it | Env vars (worker only) | What it adds |
|---|---|---|---|
| **Groq** | <https://console.groq.com> → API keys | `GROQ_API_KEY` | Fast chat for explanations and narration. |
| **Gemini** | <https://aistudio.google.com> → API key | `GEMINI_API_KEY` | Bulk news extraction and clustering. |

> Free tiers receive **public market news only**. Never send journal
> entries, tickets, positions or any user data to a free provider.

---

## 4. Telegram ← the bot

| Value | Where to get it | Where it goes |
|---|---|---|
| Bot token | Message **@BotFather** → `/newbot` → copy the token | `TELEGRAM_BOT_TOKEN` (worker **and** web) |
| Webhook secret | you invent: `openssl rand -hex 32` | `TELEGRAM_WEBHOOK_SECRET` (worker **and** web) — the SAME value in both |
| Allowed chat ids | optional; empty at first | `TELEGRAM_ALLOWED_CHAT_IDS` (web) |

Your **chat id is not a credential you configure**. You link the chat from
the app: Settings → Telegram → Generate link code, then send `/link <code>`
to the bot. The code is single-use and expires in ten minutes.

---

## 5. Everything else

| Value | Where to get it | Where it goes | Notes |
|---|---|---|---|
| `SEC_USER_AGENT` | you invent | worker | SEC EDGAR asks you to identify yourself. Use a real address. |
| `NEXT_PUBLIC_SITE_URL` | your Vercel domain | web | The magic-link redirect. |
| `WORKER_ID` | you invent | worker | Defaults to `live-worker-1`. |
| `ENABLED_VENUES` | you choose | worker | `binance,alpaca,oanda`. |
| `TIMEFRAMES` | you choose | worker | `1m,5m,1h,1d`. |
| `GROK`/`GEMINI` model names | optional | worker | Leave the defaults. |

---

## The checklist

- [ ] Supabase project created, URL + anon + service-role copied
- [ ] One auth user created, with the `owner` role and a `capital_usd`
- [ ] Migrations applied (`npm run db:check` locally, `supabase db push` remotely)
- [ ] Vercel project imported with the web env vars from §1 and §4
- [ ] GCP e2-micro created in a free region, with the worker `.env` from §2, §3, §4
- [ ] **Alpaca paper-trading keys** → the six US equity rows go live
- [ ] **OANDA practice key** → the FX and metals rows go live
- [ ] Telegram webhook registered (`npm run telegram:setup -- set <url>`)
- [ ] Chat linked from Settings, and `/signal BTCUSDT 1h` answers
- [ ] `npm test` green

### The order that gets you the most, fastest

1. **Alpaca keys** — six instruments, live prices, and the US equities the
   research worker can cross-check against Twelve Data.
2. **OANDA key** — three more instruments.
3. **Telegram token** — the bot answers from your phone.
4. **Groq key** — the AI writes the explanations.
5. **Finnhub key** — the economic calendar starts warning you before
   releases.
6. **Twelve Data key** — the cross-check, if you want it.
