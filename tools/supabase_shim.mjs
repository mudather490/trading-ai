/**
 * The Supabase shim.
 *
 * A hosted Supabase project provides the `auth` schema, the `auth.users`
 * table, the `auth.uid()` function and the `anon` / `authenticated` /
 * `service_role` roles. PGlite is bare Postgres, so we build a faithful double
 * of exactly those objects. The migration files themselves remain plain
 * Supabase SQL and are NOT modified to accommodate the shim.
 *
 * The same shim is used by the migration gate and by the local server, so
 * the two can never drift apart.
 */

export const SHIM = `
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

/** The identity the local server signs in. */
export const DEMO_USER_ID = "11111111-1111-1111-1111-111111111111";
export const DEMO_EMAIL = "demo@example.com";

/** The keys the local server accepts. They are not secrets; they are labels. */
export const ANON_KEY = "local-anon-key";
export const SERVICE_ROLE_KEY = "local-service-role-key";

/**
 * The demo user and its risk profile.
 *
 * The profile is what the ticket function needs: without one, `get_ticket`
 * refuses with "no risk profile", which is a correct answer but a boring
 * screen. The demo user is the OWNER, so the whole custody boundary can be
 * exercised locally.
 */
export const DEMO_SEED = `
insert into auth.users (id, email)
values ('${DEMO_USER_ID}', '${DEMO_EMAIL}')
on conflict (id) do nothing;

insert into public.user_profiles
  (user_id, role, display_name, capital_usd, risk_per_trade_pct,
   max_position_pct, max_gross_pct, max_correlated_pct, max_concurrent,
   max_tickets_per_day, venues, timezone)
values
  ('${DEMO_USER_ID}', 'owner', 'Local Demo', 25000, 0.005,
   0.20, 1.00, 0.30, 5, 10,
   array['binance','alpaca','oanda'], 'UTC')
on conflict (user_id) do nothing;
`;
