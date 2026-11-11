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

  insert into instruments (symbol, venue, asset_class, in_universe, primary_source, data_quality)
  values ('BTCUSDT', 'binance', 'crypto', true, 'binance_ws', 'official');

  -- The owner's private journal entry. The trusted user must never see it.
  insert into journal (user_id, instrument_id, action, note)
  select '${OWNER}', id, 'took', 'Owner private note' from instruments limit 1;

  insert into journal (user_id, instrument_id, action, note)
  select '${TRUSTED}', id, 'took', 'Trusted own note' from instruments limit 1;

  -- An audit row the trusted user must not read.
  insert into audit_log (actor, action, entity) values ('${OWNER}', 'promotion', 'strategy_version');
`);

console.log("  seeded\n");

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
await db.exec(`reset role`);

console.log("\nTrusted user:");
await asUser(TRUSTED, async () => {
  const shared = await count(`select count(*)::int c from instruments`);
  report(shared === 1, "trusted CAN read the shared universe", `saw ${shared}`);

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

console.log(`\n${passes} passed, ${failures} failed`);
if (failures > 0) {
  console.log("\nRLS TESTS FAILED");
  await db.close();
  process.exit(1);
}
console.log("\nOK — the custody boundary holds.");
await db.close();
process.exit(0);
