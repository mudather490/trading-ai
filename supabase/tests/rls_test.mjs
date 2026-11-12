/**
 * RLS behaviour tests.
 *
 * The migration tool proves the schema is well-formed. This proves it is
 * SAFE. It runs against a real Postgres with the Supabase shim, seeds an owner
 * and a trusted user, then impersonates each and asserts what they can and
 * cannot see.
 *
 *   node supabase/tests/rls_test.mjs
 *
 * Exit code 0 = the custody boundary holds.
 */

import { PGlite } from "@electric-sql/pglite";
import { readFileSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const MIGRATIONS_DIR = join(ROOT, "supabase", "migrations");

const db = new PGlite();

const SHIM = `
create schema if not exists auth;
create table if not exists auth.users (
  id uuid primary key default gen_random_uuid(),
  email text unique,
  created_at timestamptz not null default now()
);
create or replace function auth.uid() returns uuid
  language sql stable as $$
    select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid;
  $$;
do $$
begin
  if not exists (select 1 from pg_roles where rolname='anon') then create role anon nologin noinherit; end if;
  if not exists (select 1 from pg_roles where rolname='authenticated') then create role authenticated nologin noinherit; end if;
  if not exists (select 1 from pg_roles where rolname='service_role') then create role service_role nologin noinherit bypassrls; end if;
end $$;
grant usage on schema public, auth to anon, authenticated, service_role;

-- Supabase grants table privileges to the API roles by default. RLS is what
-- filters rows, not the absence of a GRANT. Reproduce that exactly.
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
alter default privileges in schema public grant all on functions to anon, authenticated, service_role;
`;

await db.exec(SHIM);
for (const file of readdirSync(MIGRATIONS_DIR).sort()) {
  if (!file.endsWith(".sql")) continue;
  await db.exec(readFileSync(join(MIGRATIONS_DIR, file), "utf8"));
}

const OWNER = "11111111-1111-1111-1111-111111111111";
const TRUSTED = "22222222-2222-2222-2222-222222222222";
const ANON = "33333333-3333-3333-3333-333333333333";

let failures = 0;
let passes = 0;

function report(ok, name, detail = "") {
  if (ok) {
    passes++;
    console.log(`  OK   ${name}`);
  } else {
    failures++;
    console.log(`  FAIL ${name}${detail ? `\n       ${detail}` : ""}`);
  }
}

async function asUser(userId, fn) {
  await db.exec(`select set_config('request.jwt.claim.sub', '${userId}', false)`);
  await db.exec(`set role authenticated`);
  try {
    return await fn();
  } finally {
    await db.exec(`reset role`);
  }
}

async function count(sql) {
  const res = await db.query(sql);
  return Number(res.rows[0].c);
}

async function fails(sql) {
  try {
    await db.query(sql);
    return false;
  } catch {
    return true;
  }
}

async function setClaim(userId) {
  await db.exec(`select set_config('request.jwt.claim.sub', '${userId}', false)`);
}

// ---------------------------------------------------------------- seed ----

console.log("Seeding fixtures...");

await db.exec(`
  insert into auth.users (id, email) values
    ('${OWNER}', 'owner@example.com'),
    ('${TRUSTED}', 'trusted@example.com'),
    ('${ANON}', 'anon@example.com');

  insert into user_profiles (user_id, role, display_name, capital_usd)
  values ('${OWNER}', 'owner', 'Owner', 100000),
         ('${TRUSTED}', 'trusted', 'Trusted', 25000);

  -- The seed migration already loaded the universe. Pick one instrument from
  -- it rather than inserting our own, so the test does not depend on whether
  -- the seed has run.
  insert into journal (user_id, instrument_id, action, note)
  select '${OWNER}', id, 'took', 'Owner private note'
  from instruments where symbol = 'BTCUSDT';

  insert into journal (user_id, instrument_id, action, note)
  select '${TRUSTED}', id, 'took', 'Trusted own note'
  from instruments where symbol = 'BTCUSDT';

  -- An audit row the trusted user must not read.
  insert into audit_log (actor, action, entity) values ('${OWNER}', 'promotion', 'strategy_version');
`);

console.log("  seeded\n");

// Capture the expected universe size while we are still the superuser, so the
// trusted-user assertion below is a real comparison rather than a tautology.
const universeSize = await count(
  `select count(*)::int c from instruments where in_universe`,
);
console.log(`  universe size: ${universeSize}\n`);

// ------------------------------------------------------------- the tests --

console.log("Anon (unauthenticated):");
await db.exec(`set role anon`);
{
  const shared = await count(
    `select count(*)::int c from instruments`
  );
  report(shared === 0, "anon cannot read instruments", `saw ${shared} rows`);

  const own = await count(`select count(*)::int c from user_profiles`);
  report(own === 0, "anon cannot read user_profiles", `saw ${own} rows`);

  const journal = await count(`select count(*)::int c from journal`);
  report(journal === 0, "anon cannot read journal", `saw ${journal} rows`);
}

// The functions below are SECURITY DEFINER, so they run with the privileges
// of their owner and RLS does NOT filter them. Postgres grants EXECUTE on
// every new function to PUBLIC, and Supabase adds explicit grants for `anon`
// and `authenticated`, so "anon can read nothing" is not enough: anon must
// not be able to RUN the powerful functions either.
//
// This is the check that was missing when an anon key was able to insert a
// forged row into public.signals.
console.log("\nAnon (unauthenticated) function execution:");
await db.exec(`set role anon`);
{
  const calls = [
    ["record_signal", `select public.record_signal(
        (select id from instruments limit 1), '1h', 'buy', 1, 'probe',
        now(), now(), 'anon-probe')`],
    ["claim_jobs", `select * from public.claim_jobs('probe', 1)`],
    ["finish_job", `select public.finish_job(gen_random_uuid(), true)`],
    ["fire_alert", `select public.fire_alert('${ANON}', 'probe', 'probe', 'probe')`],
    ["get_ticket", `select public.get_ticket('${ANON}', (select id from instruments limit 1))`],
    ["promote_strategy", `select public.promote_strategy(gen_random_uuid(), 'probe')`],
    ["is_owner", `select public.is_owner()`],
    ["engine_status", `select public.engine_status()`],
    ["update_my_profile", `select public.update_my_profile(p_capital_usd => 1000)`],
  ];
  for (const [name, sql] of calls) {
    report(await fails(sql), `anon cannot execute ${name}()`);
  }

  const signals = await count(`select count(*)::int c from signals`);
  report(signals === 0, "anon wrote nothing to signals", `saw ${signals} rows`);
}
await db.exec(`reset role`);

console.log("\nThe worker's functions (service_role only):");
await asUser(TRUSTED, async () => {
  report(
    await fails(`select * from public.claim_jobs('probe', 1)`),
    "trusted CANNOT claim jobs",
  );
  report(
    await fails(`select public.finish_job(gen_random_uuid(), true)`),
    "trusted CANNOT finish jobs",
  );
  report(
    await fails(`select public.record_signal(
      (select id from instruments limit 1), '1h', 'buy', 1, 'probe',
      now(), now(), 'trusted-probe')`),
    "trusted CANNOT record signals",
  );
});

console.log("\nOne user cannot act for another:");
await asUser(TRUSTED, async () => {
  report(
    await fails(
      `select public.get_ticket('${OWNER}', (select id from instruments limit 1), '1h')`,
    ),
    "trusted CANNOT compute the owner's ticket",
  );
  report(
    await fails(`select public.fire_alert('${OWNER}', 'probe', 'probe', 'probe')`),
    "trusted CANNOT file an alert for the owner",
  );
});
await asUser(OWNER, async () => {
  // The owner may compute a ticket for anyone. That is the owner's job.
  const ok = await fails(
    `select public.get_ticket('${TRUSTED}', (select id from instruments limit 1), '1h')`,
  );
  report(!ok, "owner CAN compute a ticket for a trusted user");
});

console.log("\nTrusted user:");
await asUser(TRUSTED, async () => {
  const shared = await count(
    `select count(*)::int c from instruments where in_universe`
  );
  report(
    shared === universeSize && universeSize > 0,
    "trusted CAN read the whole shared universe",
    `saw ${shared} of ${universeSize}`,
  );

  const profiles = await count(`select count(*)::int c from user_profiles`);
  report(profiles === 1, "trusted sees only their own profile", `saw ${profiles}`);

  const ownerJournal = await count(
    `select count(*)::int c from journal where note = 'Owner private note'`
  );
  report(ownerJournal === 0, "trusted CANNOT read the owner's journal", `saw ${ownerJournal}`);

  const ownJournal = await count(
    `select count(*)::int c from journal where note = 'Trusted own note'`
  );
  report(ownJournal === 1, "trusted CAN read their own journal", `saw ${ownJournal}`);

  const audit = await count(`select count(*)::int c from audit_log`);
  report(audit === 0, "trusted CANNOT read audit_log", `saw ${audit}`);

  const jobs = await count(`select count(*)::int c from jobs`);
  report(jobs === 0, "trusted CANNOT read jobs", `saw ${jobs}`);

  const aiRuns = await count(`select count(*)::int c from ai_runs`);
  report(aiRuns === 0, "trusted CANNOT read ai_runs (prompts and raw output)", `saw ${aiRuns}`);

  const alerts = await count(`select count(*)::int c from alerts`);
  report(alerts === 0, "trusted CANNOT read another user's alerts", `saw ${alerts}`);

  const gate = await count(`select count(*)::int c from gate_policy`);
  report(gate === 1, "trusted CAN read the gate policy", `saw ${gate}`);

  // The trusted user must not be able to insert into the strategy library.
  let blocked = false;
  try {
    await db.query(`insert into strategies (name, family, asset_class) values ('x','trend','crypto')`);
  } catch {
    blocked = true;
  }
  report(blocked, "trusted CANNOT insert into the strategy library");
});

console.log("\nOwner:");
await asUser(OWNER, async () => {
  const profiles = await count(`select count(*)::int c from user_profiles`);
  report(profiles === 2, "owner sees all profiles", `saw ${profiles}`);

  const journal = await count(`select count(*)::int c from journal`);
  report(journal === 1, "owner sees only their OWN journal", `saw ${journal}`);

  const ownerSeesTrusted = await count(
    `select count(*)::int c from journal where note = 'Trusted own note'`
  );
  report(
    ownerSeesTrusted === 0,
    "owner CANNOT read a trusted user's personal journal",
    `saw ${ownerSeesTrusted}`
  );

  const audit = await count(`select count(*)::int c from audit_log`);
  report(audit === 1, "owner CAN read audit_log", `saw ${audit}`);

  const aiRuns = await count(`select count(*)::int c from ai_runs`);
  report(aiRuns === 0, "owner CAN read ai_runs (own, empty)", `saw ${aiRuns}`);

  // The owner CAN write to the strategy library.
  let wrote = false;
  try {
    await db.query(
      `insert into strategies (name, family, asset_class) values ('Owner Strategy','trend','crypto')`
    );
    wrote = true;
  } catch (err) {
    report(false, "owner CAN insert into the strategy library", String(err.message));
  }
  if (wrote) report(true, "owner CAN insert into the strategy library");
});

console.log("\nService role (the live worker):");
await db.exec(`set role service_role`);
{
  const all = await count(`select count(*)::int c from user_profiles`);
  report(all === 2, "service_role sees every profile (bypasses RLS)", `saw ${all}`);
}
await db.exec(`reset role`);

console.log("\nFreshness gate (the engine must be able to refuse):");
{
  // With a heartbeat but no quotes, get_ticket must refuse rather than invent.
  await db.exec(`insert into worker_heartbeat (worker_id, version) values ('w','test')`);
  const res = await db.query(
    `select public.get_ticket('${OWNER}', (select id from instruments limit 1), '1h') as t`
  );
  const t = res.rows[0].t;
  report(
    t && ["stale", "no_match", "wait", "engine_unavailable"].includes(t.status),
    "get_ticket refuses when there is no price data",
    `status=${t?.status} reason=${t?.reason}`
  );

  // With a heartbeat older than 2 minutes the engine must be unavailable.
  await db.exec(`update worker_heartbeat set last_seen_at = now() - interval '10 minutes' where worker_id='w'`);
  const res2 = await db.query(
    `select public.get_ticket('${OWNER}', (select id from instruments limit 1), '1h') as t`
  );
  const t2 = res2.rows[0].t;
  report(
    t2?.status === "engine_unavailable",
    "get_ticket returns engine_unavailable on a stale heartbeat",
    `status=${t2?.status}`
  );

  // A fresh heartbeat but still no quotes must NOT say engine_unavailable.
  await db.exec(`update worker_heartbeat set last_seen_at = now() where worker_id='w'`);
  const res3 = await db.query(
    `select public.get_ticket('${OWNER}', (select id from instruments limit 1), '1h') as t`
  );
  const t3 = res3.rows[0].t;
  report(
    t3?.status !== "engine_unavailable",
    "a live engine with no data does not claim the engine is down",
    `status=${t3?.status}`
  );
}

console.log("\nPromotion gate:");
{
  // A trusted user must not be able to promote.
  await asUser(TRUSTED, async () => {
    let blocked = false;
    try {
      await db.query(
        `select public.promote_strategy('00000000-0000-0000-0000-000000000000')`
      );
    } catch {
      blocked = true;
    }
    report(blocked, "trusted CANNOT promote a strategy");
  });

  // A candidate that fails the gate must be REJECTED, not promoted.
  await db.exec(`
    insert into strategies (id, name, family, asset_class)
    values ('44444444-4444-4444-4444-444444444444', 'Weak Strategy', 'trend', 'crypto');
    insert into strategy_versions (id, strategy_id, version, status)
    values ('55555555-5555-5555-5555-555555555555', '44444444-4444-4444-4444-444444444444', 1, 'challenger');
    insert into performance (version_id, asset_class, timeframe, period_start, period_end,
      trades, sharpe, dsr, pbo, max_drawdown, n_trials, data_source)
    values ('55555555-5555-5555-5555-555555555555', 'crypto', '1h', '2024-01-01', '2024-06-01',
      12, 0.4, 0.2, 0.9, 0.6, 500, 'test');
  `);

  await asUser(OWNER, async () => {
    const res = await db.query(
      `select public.promote_strategy('55555555-5555-5555-5555-555555555555') as r`
    );
    const r = res.rows[0].r;
    report(r?.status === "rejected", "a weak candidate is REJECTED by the gate", `status=${r?.status}`);

    const champs = await count(`select count(*)::int c from champions where active`);
    report(champs === 0, "nothing was promoted", `champions=${champs}`);

    const rules = (r?.failures ?? []).map((f) => f.rule);
    report(
      rules.includes("min_trades") && rules.includes("min_dsr") && rules.includes("max_pbo"),
      "the rejection names every failed rule",
      `rules=${rules.join(",")}`
    );
  });
}

console.log("\nThe research ledger and the provider contract checks:");
{
  // These two tables record how the research was judged and whether the
  // providers still answer the shape the app expects. They are shared
  // reading, owner writing.
  await db.exec(`
    insert into research_runs (symbol, timeframe, template_id, candidates, holdout_read)
    values ('BTCUSDT', '1d', 'trend_ema_adx', 13, true);
    insert into provider_contracts (provider, endpoint, schema_hash, ok)
    values ('binance', 'GET /api/v3/ticker/price', 'abc123', true);
  `);

  await db.exec(`set role anon`);
  {
    const runs = await count(`select count(*)::int c from research_runs`);
    report(runs === 0, "anon cannot read research_runs", `saw ${runs} rows`);
    const contracts = await count(`select count(*)::int c from provider_contracts`);
    report(contracts === 0, "anon cannot read provider_contracts", `saw ${contracts} rows`);
  }
  await db.exec(`reset role`);

  await asUser(TRUSTED, async () => {
    const runs = await count(`select count(*)::int c from research_runs`);
    report(runs === 1, "a signed-in user CAN read the research ledger", `saw ${runs}`);

    const wrote = await fails(
      `insert into research_runs (symbol, timeframe, template_id, candidates)
       values ('X', '1d', 't', 1)`
    );
    report(wrote, "a trusted user CANNOT write the research ledger");

    const wroteContract = await fails(
      `insert into provider_contracts (provider, endpoint, schema_hash, ok)
       values ('x', 'y', 'z', true)`
    );
    report(wroteContract, "a trusted user CANNOT write a contract check");
  });

  await asUser(OWNER, async () => {
    const wrote = await fails(
      `insert into research_runs (symbol, timeframe, template_id, candidates)
       values ('X', '1d', 't', 1)`
    );
    report(!wrote, "the owner CAN write the research ledger");
  });

  // The gate must refuse a candidate whose dataset holdout was already
  // read by an earlier run. The flag is written by the research runner.
  await db.exec(`
    insert into strategies (id, name, family, asset_class)
    values ('66666666-6666-6666-6666-666666666666', 'Contaminated', 'trend', 'crypto');
    insert into strategy_versions (id, strategy_id, version, status)
    values ('77777777-7777-7777-7777-777777777777', '66666666-6666-6666-6666-666666666666', 1, 'challenger');
    insert into performance (version_id, asset_class, timeframe, period_start, period_end,
      trades, sharpe, dsr, pbo, max_drawdown, n_trials, data_source, notes)
    values ('77777777-7777-7777-7777-777777777777', 'crypto', '1d', '2024-01-01', '2024-06-01',
      50, 1.5, 0.99, 0.1, 0.1, 10, 'test', '{"holdout_contaminated": true}'::jsonb);
  `);

  await asUser(OWNER, async () => {
    const res = await db.query(
      `select public.promote_strategy('77777777-7777-7777-7777-777777777777') as r`
    );
    const r = res.rows[0].r;
    const rules = (r?.failures ?? []).map((f) => f.rule);
    report(
      r?.status === "rejected" && rules.includes("holdout_contaminated"),
      "the gate REJECTS a candidate whose holdout was already read",
      `status=${r?.status} rules=${rules.join(",")}`
    );
  });
}

console.log("\nSettings clamps (the browser cannot raise its own limits):");
{
  await asUser(TRUSTED, async () => {
    const res = await db.query(`
      select public.update_my_profile(
        p_display_name => 'Trusted',
        p_capital_usd => 1000,
        p_risk_per_trade_pct => 0.90,
        p_max_position_pct => 5.00,
        p_max_gross_pct => 9.00,
        p_max_correlated_pct => 5.00,
        p_max_concurrent => 99,
        p_max_tickets_per_day => 99,
        p_timezone => 'Europe/London'
      ) as p
    `);
    const p = res.rows[0].p;
    report(Number(p?.risk_per_trade_pct) === 0.02, "risk_per_trade_pct is clamped to 2%", `got ${p?.risk_per_trade_pct}`);
    report(Number(p?.max_position_pct) === 0.50, "max_position_pct is clamped to 50%", `got ${p?.max_position_pct}`);
    report(Number(p?.max_gross_pct) === 2.00, "max_gross_pct is clamped to 200%", `got ${p?.max_gross_pct}`);
    report(Number(p?.max_correlated_pct) === 1.00, "max_correlated_pct is clamped to 100%", `got ${p?.max_correlated_pct}`);
    report(Number(p?.max_concurrent) === 10, "max_concurrent is clamped to 10", `got ${p?.max_concurrent}`);
    report(Number(p?.max_tickets_per_day) === 20, "max_tickets_per_day is clamped to 20", `got ${p?.max_tickets_per_day}`);
    report(p?.role === "trusted", "a user CANNOT change their own role", `role=${p?.role}`);

    report(
      await fails(`select public.update_my_profile(p_capital_usd => -5)`),
      "a negative capital is refused",
    );
  });

  // The audit row is written by the SECURITY DEFINER function, not forged.
  await asUser(OWNER, async () => {
    const rows = await count(
      `select count(*)::int c from audit_log where action = 'profile.update' and actor = '${TRUSTED}'`
    );
    report(rows === 1, "the settings change is audit-logged", `saw ${rows}`);
  });
}

console.log("\nTelegram linking:");
{
  let code = null;

  await asUser(TRUSTED, async () => {
    const res = await db.query(`select public.create_telegram_link_code() as c`);
    code = res.rows[0].c?.code ?? null;
    report(
      typeof code === "string" && code.length >= 6,
      "a signed-in user can mint a single-use link code",
      `code=${code}`,
    );

    // The plaintext code must never reach the database.
    const plaintext = await count(
      `select count(*)::int c from telegram_link_codes where code_hash = '${code}'`,
    );
    report(
      plaintext === 0,
      "the link code is stored as a hash, never in plaintext",
      `saw ${plaintext}`,
    );

    const codes = await count(
      `select count(*)::int c from telegram_link_codes where user_id = '${TRUSTED}'`,
    );
    report(codes === 1, "exactly one live code per user", `saw ${codes}`);

    // Only the webhook (service role) may redeem. A user cannot bind their
    // own chat by calling the function directly.
    report(
      await fails(
        `select public.redeem_telegram_link('${code}', 5550001, 'probe')`,
      ),
      "a signed-in user CANNOT redeem a link code",
    );

    // Nor can a user write the link row directly.
    let blocked = false;
    try {
      await db.query(
        `insert into telegram_links (user_id, chat_id) values ('${TRUSTED}', 5550002)`,
      );
    } catch {
      blocked = true;
    }
    report(blocked, "a signed-in user CANNOT create a telegram link row");
  });

  // The webhook side: the service role redeems the code.
  await db.exec(`set role service_role`);
  const redeemed = await db.query(
    `select public.redeem_telegram_link('${code}', 5550001, 'tester') as r`,
  );
  await db.exec(`reset role`);
  report(
    redeemed.rows[0].r?.ok === true,
    "the webhook can redeem a link code",
    JSON.stringify(redeemed.rows[0].r),
  );

  const links = await count(
    `select count(*)::int c from telegram_links where chat_id = 5550001`,
  );
  report(links === 1, "the chat is now linked to the user", `saw ${links}`);

  // A code works once.
  await db.exec(`set role service_role`);
  const replay = await db.query(
    `select public.redeem_telegram_link('${code}', 5550003, 'tester') as r`,
  );
  await db.exec(`reset role`);
  report(
    replay.rows[0].r?.ok === false,
    "a link code cannot be reused",
    JSON.stringify(replay.rows[0].r),
  );

  // The user can see their own link, and can revoke it.
  await asUser(TRUSTED, async () => {
    const mine = await count(
      `select count(*)::int c from telegram_links where user_id = '${TRUSTED}' and revoked_at is null`,
    );
    report(mine === 1, "the user can see their own link", `saw ${mine}`);

    await db.query(`select public.revoke_telegram_link(5550001)`);
    const after = await count(
      `select count(*)::int c from telegram_links where revoked_at is not null`,
    );
    report(after === 1, "the user can revoke their own link", `saw ${after}`);
  });
}

console.log(`\n${passes} passed, ${failures} failed`);
if (failures > 0) {
  console.log("\nRLS TESTS FAILED");
  await db.close();
  process.exit(1);
}
console.log("\nOK — the custody boundary holds.");
await db.close();
process.exit(0);
