# Setup Runbook

Everything you must do, in order. Steps marked **← YOU** need your accounts.
Everything else I run.

**Time to first live price on your phone: about 3 days**, most of it waiting
for cloud account provisioning.

---

## Step 1 — Supabase project ← YOU

1. <https://supabase.com/dashboard> → **New project**.
2. Name it `trading-command-center`. Pick the region closest to the GCP VM
   you will create (both should be in the same region for latency).
3. Wait for provisioning (~2 minutes).
4. **Settings → Database → Connection string → URI.** Copy it. It looks like:
   ```
   postgresql://postgres.PROJECT_REF:PASSWORD@db.PROJECT_REF.supabase.co:5432/postgres
   ```
5. **Settings → API → Project URL** and **`anon` public key**. Copy both.
6. **Settings → API → `service_role` key.** Copy it. It bypasses RLS.
   Treat it like a password: it goes in the worker's environment and NOWHERE
   else.
7. **Settings → Auth → Providers.** Email is on by default. Leave it.
8. **Settings → Storage → Buckets → Create.** Name it `backups`.

> **Important:** the free plan pauses a project after 1 week of inactivity.
> The live worker's daily heartbeat prevents that. If you ever stop the
> worker for more than a week, the project may pause and need a manual
> resume.

---

## Step 2 — Apply the migrations ← ME

I run this against your project:

```bash
# With the Supabase CLI installed
supabase link --project-ref YOUR_PROJECT_REF
supabase db push
```

Or, if you prefer not to install the CLI, open the SQL editor in the
dashboard and run each file in `supabase/migrations/` in numeric order.
There are six, and they are idempotent.

**Then verify it worked:**

```bash
cd E:\trading-ai
npm run db:check     # applies the migrations to a local PGlite and checks 12 invariants
npm run db:test      # 27 RLS tests: anon / trusted / owner isolation
```

You should also create your user account now, because the RLS tests and the
app both need one:

1. Go to your Supabase URL → **Authentication → Users → Add user**.
2. Create yourself with email + password, and **confirm the email**.
3. Give yourself the owner role. In the SQL editor:

```sql
insert into public.user_profiles (user_id, role, display_name, capital_usd)
select id, 'owner', 'Owner', 100000
from auth.users
where email = 'you@example.com'
on conflict (user_id) do update
  set role = 'owner', capital_usd = 100000;
```

Set `capital_usd` to whatever you actually intend to trade. It is the number
the sizing function divides.

**For each trusted person**, create their auth user the same way, then:

```sql
insert into public.user_profiles (user_id, role, display_name, capital_usd)
select id, 'trusted', 'Friend', 10000
from auth.users
where email = 'friend@example.com'
on conflict (user_id) do update set role = 'trusted';
```

---

## Step 3 — GCP free VM ← YOU + ME

1. <https://console.cloud.google.com> → create a project, then enable the
   **Compute Engine API**.
2. **Create instance**:
   - Name: `trading-worker`
   - Region: the same region as your Supabase project
   - Machine type: **e2-micro**
   - Boot disk: **Ubuntu 24.04**, 30 GB standard persistent disk
   - Firewall: allow **SSH from your own IP only**
3. **Point to it** (I need the external IP):
   ```
   gcloud compute instances describe trading-worker --zone=YOUR_ZONE \
     --format='value(networkInterfaces[0].accessConfigs[0].natIP)'
   ```

**Cost control:** the e2-micro is free in `us-east1`, `us-central1` and
`us-west1`. In other regions it is **not** free and will start billing. Also
confirm the free-tier checkbox on your billing account.

Then I install the worker:

```bash
sudo apt update && sudo apt install -y python3-venv postgresql-client
sudo useradd -m -s /bin/bash trader
sudo mkdir -p /opt/trading-ai && sudo chown trader:trader /opt/trading-ai
# I copy the worker code up, create the venv, install deps, write .env
sudo cp /opt/trading-ai/deploy/trading-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trading-worker
journalctl -u trading-worker -f
```

You should see the Binance stream connect within seconds.

---

## Step 4 — Venue credentials ← YOU

All optional in v1 — the worker starts with crypto only and the others are
added as you supply keys.

| Venue | Where to get it | What you get |
|---|---|---|
| **Alpaca** | <https://app.alpaca.markets> → sign up → **Paper Trading** → API keys | Free. IEX-only real-time. 30 WS symbols. We use 6. |
| **OANDA** | <https://developer.oanda.com> → create a **fxTrade Practice** account → REST API key | Free practice. Indicative rates. We use 3. |
| **Finnhub** | <https://finnhub.io> → register | Free key, 60 calls/min. News and earnings dates. |
| **Groq** | <https://console.groq.com> → API keys | Free tier, 30 RPM / 1,000/day. Chat and narration. |
| **Gemini** | <https://aistudio.google.com> → API key | Free tier. Bulk news extraction. |
| **Telegram** | Message `@BotFather` → `/newbot` → copy the token | Free. Alerts. |

> **Do NOT put an Alpaca or OANDA key in Vercel.** Only the worker holds
> venue credentials. That separation is the custody boundary.

---

## Step 5 — Vercel deployment ← YOU + ME

1. <https://vercel.com> → import the repository.
2. **Root directory:** `apps/web`
3. **Environment variables** (3 only):

   | Variable | Value |
   |---|---|
   | `NEXT_PUBLIC_SUPABASE_URL` | your project URL |
   | `NEXT_PUBLIC_SUPABASE_ANON_KEY` | your anon key |
   | `NEXT_PUBLIC_SITE_URL` | your Vercel domain |

   **Do not add** `SUPABASE_SERVICE_ROLE_KEY`. The web tier must not be able
   to bypass RLS.

4. Deploy. Then **Settings → Environment → Cron Jobs**, add one job:
   ```
   Path:     /api/cron/heartbeat
   Schedule: 0 8 * * *
   ```
   This writes a row once a day so the free Supabase project never goes idle.

5. Add your custom domain if you want one (~`$10/year`).

---

## Step 6 — First run ← ME

```bash
cd E:\trading-ai
npm test          # migrations + RLS + worker + research
```

Then:

1. Open the Vercel URL → sign in with the magic link.
2. You should see the Command Center with 12 instruments and the engine
   badge.
3. **If the badge says OFFLINE**, the worker is not running or cannot reach
   Supabase. Check `journalctl -u trading-worker`.

### The Phase 0 acceptance test

| Check | Expected |
|---|---|
| Engine badge | **LIVE**, green, updating every 10s |
| BTCUSDT in the watchlist | price changing every few seconds |
| Click BTCUSDT | Decision Center loads |
| The ticket panel | shows **WAIT** or **NO_MATCH** — *not* a ticket, because no strategy is promoted yet |
| Kill the worker (`sudo systemctl stop trading-worker`) | the badge turns **STALE** within ~2 minutes and the Decision Center says **ENGINE_UNAVAILABLE** |

That last row is the most important one: **the app must refuse to advise
when the engine is down.** If it shows a ticket anyway, something is wrong.

---

## Everyday operation

| Task | Where |
|---|---|
| See what the bot is doing | The Command Center |
| Get alerts on your phone | Telegram bot (Phase 2) |
| Backups | `python tools/backup.py` — daily, via cron on the VM |
| Check the engine | `journalctl -u trading-worker -f` |
| Check quotas | The heartbeat's `meta.ai_quota` field |
| Rebuild the worker | `git pull` on the VM, then `systemctl restart trading-worker` |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Engine badge OFFLINE | Worker not running | `systemctl status trading-worker` |
| Engine badge STALE | Worker running but cannot reach Supabase | Check the URL and service-role key in `.env` |
| No prices in the watchlist | Binance WS blocked, or the symbol is not in the universe | Check the worker log; the seed loads 12 symbols |
| Login redirects in a loop | `NEXT_PUBLIC_SUPABASE_URL` wrong in Vercel | Redeploy |
| Command Center shows "Not configured" | Env vars missing at build time | Vercel → Settings → Environment → redeploy |
| `npm run db:check` fails | A migration has a syntax error | The error names the file and line |
| Supabase project paused | 1 week of inactivity | Resume it in the dashboard; then re-enable the daily cron |
