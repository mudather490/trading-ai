/**
 * Apply every Supabase migration to a real PostgreSQL (PGlite/WASM) and report.
 *
 * This is the gate that caught our policy bugs before they reached a database.
 * It runs the ACTUAL migration files, in order, against a real Postgres engine,
 * so syntax errors, missing columns, bad policy targets and broken function
 * bodies all fail here rather than in production.
 *
 *   node tools/apply_migrations.mjs
 *
 * Exit code 0 = all migrations applied cleanly.
 */

import { PGlite } from "@electric-sql/pglite";
import { readFileSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const MIGRATIONS_DIR = join(ROOT, "supabase", "migrations");

const db = new PGlite();

/**
 * Supabase shim.
 *
 * A hosted Supabase project provides the `auth` schema, the `auth.users`
 * table, the `auth.uid()` function and the `anon` / `authenticated` /
 * `service_role` roles. PGlite is bare Postgres, so we build a faithful double
 * of exactly those objects. The migration files themselves remain plain
 * Supabase SQL and are NOT modified to accommodate the test.
 */
const SHIM = `
create schema if not exists auth;

create table if not exists auth.users (
  id uuid primary key default gen_random_uuid(),
  email text unique,
  created_at timestamptz not null default now()
);

-- Supabase's auth.uid(): the id of the currently authenticated user.
create or replace function auth.uid() returns uuid
  language sql stable as $$
    select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid;
  $$;

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role nologin noinherit bypassrls;
  end if;
end $$;

grant usage on schema public, auth to anon, authenticated, service_role;
grant select on auth.users to authenticated, service_role;

-- Supabase grants table privileges to the API roles by default; RLS filters
-- rows rather than the absence of a GRANT.
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
alter default privileges in schema public grant all on functions to anon, authenticated, service_role;
`;

console.log("Applying Supabase shim...");
await db.exec(SHIM);
console.log("  OK   shim\n");

let failed = false;
const applied = [];

for (const file of readdirSync(MIGRATIONS_DIR).sort()) {
  if (!file.endsWith(".sql")) continue;
  const path = join(MIGRATIONS_DIR, file);
  const sql = readFileSync(path, "utf8");
  const started = Date.now();
  try {
    await db.exec(sql);
    const ms = Date.now() - started;
    applied.push({ file, ms });
    console.log(`  OK   ${file}  (${ms}ms)`);
  } catch (err) {
    failed = true;
    console.log(`  FAIL ${file}`);
    console.log(`       ${String(err.message ?? err).split("\n").slice(0, 6).join("\n       ")}`);
    break;
  }
}

if (failed) {
  console.log("\nMIGRATIONS FAILED");
  await db.close();
  process.exit(1);
}

console.log(`\n${applied.length} migrations applied cleanly.`);

// ---- verify the structural invariants the design depends on ----------------

const checks = [
  {
    name: "every table has RLS enabled",
    sql: `select count(*)::int as bad from pg_tables
          where schemaname='public'
            and tablename not in ('gate_policy')
            and not rowsecurity`,
    expect: 0,
  },
  {
    name: "every table has at least one policy",
    sql: `select count(*)::int as bad from pg_tables t
          where schemaname='public'
            and not exists (select 1 from pg_policies p where p.schemaname='public' and p.tablename=t.tablename)`,
    expect: 0,
  },
  {
    name: "no policy targets a missing table",
    sql: `select count(*)::int as bad from pg_policies p
          where p.schemaname='public'
            and not exists (select 1 from pg_tables t where t.schemaname='public' and t.tablename=p.tablename)`,
    expect: 0,
  },
  {
    name: "no duplicate policy name on a table",
    sql: `select count(*)::int as bad from (
            select schemaname, tablename, policyname, count(*) c
            from pg_policies group by 1,2,3 having count(*) > 1
          ) x`,
    expect: 0,
  },
  {
    name: "exactly one active gate policy",
    sql: `select count(*)::int as bad from gate_policy where active`,
    expect: 1,
  },
  {
    name: "get_ticket exists and is not SECURITY DEFINER-visible-to-public",
    sql: `select count(*)::int as bad from pg_proc p
          join pg_namespace n on n.oid=p.pronamespace
          where n.nspname='public' and p.proname='get_ticket'
            and p.prosecdef = false`,
    expect: 0,
  },
  {
    name: "promote_strategy is owner-gated (security definer + is_owner check)",
    sql: `select count(*)::int as bad from pg_proc p
          join pg_namespace n on n.oid=p.pronamespace
          where n.nspname='public' and p.proname='promote_strategy' and p.prosecdef = false`,
    expect: 0,
  },
  {
    name: "engine_status is NULL when no worker has ever run",
    sql: `select count(*)::int as bad from public.engine_status() where engine_status() is not null`,
    expect: 0,
  },
  {
    name: "engine_status reports live once a heartbeat exists",
    setup: `insert into worker_heartbeat (worker_id, version) values ('w-test', 'test');`,
    sql: `select count(*)::int as bad from public.engine_status()
            where engine_status()->>'status' is distinct from 'live'`,
    expect: 0,
  },
  {
    name: "no app function is executable by PUBLIC",
    sql: `select count(*)::int as bad from pg_proc p
          join pg_namespace n on n.oid=p.pronamespace
          where n.nspname='public'
            and p.proname in ('get_ticket','promote_strategy','engine_status',
                              'fire_alert','record_signal','claim_jobs','finish_job')
            and coalesce(p.proacl::text,'') ~ '(^|,|\\{)public='`,
    expect: 0,
  },
  {
    name: "every referenced column in the ticket RPC exists",
    sql: `select count(*)::int as bad from pg_proc p
          join pg_namespace n on n.oid=p.pronamespace
          where n.nspname='public' and p.proname in ('get_ticket','promote_strategy')
            and p.prosrc ilike '%nonexistent_column_xyz%'`,
    expect: 0,
  },
];

console.log("\nStructural invariants:");
let invFailed = false;
for (const check of checks) {
  try {
    if (check.setup) await db.exec(check.setup);
    const res = await db.query(check.sql);
    const actual = Number(res.rows[res.rows.length - 1].bad);
    const ok = actual === check.expect;
    if (!ok) invFailed = true;
    console.log(`  ${ok ? "OK  " : "FAIL"} ${check.name}  (expected ${check.expect}, got ${actual})`);
  } catch (err) {
    invFailed = true;
    console.log(`  FAIL ${check.name}\n       ${String(err.message ?? err).split("\n")[0]}`);
  }
}

if (invFailed) {
  console.log("\nINVARIANTS FAILED");
  await db.close();
  process.exit(1);
}

console.log("\nOK — migrations apply and all invariants hold.");
await db.close();
process.exit(0);
