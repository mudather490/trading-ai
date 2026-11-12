-- =============================================================================
-- 0014_provider_contracts.sql
--
-- The provider contract check.
--
-- A market data API can change its response shape without saying so, and a
-- pipeline that keeps "working" with nulls is worse than one that is
-- loudly broken. Every week the worker calls each configured provider's
-- cheapest endpoint and records:
--
--   * the SET OF KEYS in the response (hashed),
--   * whether the sample values are non-null,
--   * the latency and the HTTP status.
--
-- A change in the key set is a schema change, and it is detected by a hash
-- rather than by reading a changelog. This is deterministic: it cannot
-- hallucinate a change, and it cannot miss one.
--
-- `feed_health()` now carries the result per venue, so the banner can say
-- "the provider's contract changed" instead of "no price data".
--
-- This is NOT an AI changelog reader. An LLM told to watch for API changes
-- would eventually decide that something changed without anything
-- changing, and nobody could audit why.
-- =============================================================================

create table public.provider_contracts (
  id          uuid primary key default gen_random_uuid(),
  -- 'binance' | 'alpaca' | 'oanda' | 'twelvedata' | 'finnhub'
  provider    text        not null,
  endpoint    text        not null,
  -- sha256 of the sorted key set of the response.
  schema_hash text        not null,
  -- The key set itself, so a human can see what changed.
  keys        jsonb,
  -- Did the check pass?
  ok          boolean     not null default true,
  status      integer,
  latency_ms  integer,
  -- A small sample, to prove the values are not null.
  sample      jsonb,
  note        text,
  checked_at  timestamptz not null default now()
);

create index provider_contracts_provider_idx
  on public.provider_contracts (provider, checked_at desc);

comment on table public.provider_contracts is
  'One row per contract check. The key set hash detects a response-shape '
  'change without reading a changelog. Failures are published, not hidden.';

alter table public.provider_contracts enable row level security;

create policy provider_contracts_read on public.provider_contracts
  for select to authenticated using (true);

create policy provider_contracts_owner_write on public.provider_contracts
  for all to authenticated
  using (public.is_owner()) with check (public.is_owner());

create or replace function public.feed_health()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_hb      public.worker_heartbeat%rowtype;
  v_has_hb  boolean := false;
  v_venues  jsonb := '[]'::jsonb;
  v_rec     record;
  v_configured boolean;
  v_enabled    boolean;
  v_connected  boolean;
  v_age        numeric;
  v_max_age    interval;
  v_status     text;
  v_hint       text;
begin
  select * into v_hb from public.worker_heartbeat order by last_seen_at desc limit 1;
  v_has_hb := found;

  for v_rec in
    select i.venue,
           count(*)::int as symbols,
           count(q.instrument_id)::int as with_quotes,
           max(q.as_of) as last_tick_at,
           (array_agg(i.data_quality::text))[1] as data_quality,
           (array_agg(i.asset_class::text))[1] as asset_class,
           -- The last contract check for this venue, if there was one.
           (select pc.ok from public.provider_contracts pc
             where pc.provider = i.venue
             order by pc.checked_at desc limit 1) as contract_ok,
           (select pc.checked_at from public.provider_contracts pc
             where pc.provider = i.venue
             order by pc.checked_at desc limit 1) as contract_checked_at
    from public.instruments i
    left join public.latest_quotes q on q.instrument_id = i.id
    where i.in_universe
    group by i.venue
    order by i.venue
  loop
    v_configured := null;
    v_enabled := null;
    v_connected := null;
    if v_has_hb then
      v_configured := nullif(v_hb.meta->'venues'->v_rec.venue->>'configured', '')::boolean;
      v_enabled    := nullif(v_hb.meta->'venues'->v_rec.venue->>'enabled', '')::boolean;
      v_connected  := nullif(v_hb.streams->v_rec.venue->>'connected', '')::boolean;
    end if;

    v_age := case when v_rec.last_tick_at is null then null
                  else extract(epoch from (now() - v_rec.last_tick_at)) end;

    v_max_age := case v_rec.data_quality
      when 'official'   then interval '30 seconds'
      when 'indicative' then interval '2 minutes'
      else interval '10 minutes'
    end;

    if v_configured is false then
      v_status := 'not_configured';
      v_hint := format('Add the %s credentials to the worker.', initcap(v_rec.venue));
    elsif not v_has_hb or now() - v_hb.last_seen_at > interval '2 minutes' then
      v_status := 'engine_down';
      v_hint := 'The worker is not running.';
    elsif v_connected is false then
      v_status := 'down';
      v_hint := format('The %s stream is disconnected.', initcap(v_rec.venue));
    elsif v_rec.last_tick_at is null then
      v_status := 'no_data';
      v_hint := 'Connected, but no price has arrived yet.';
    elsif v_age > extract(epoch from v_max_age) then
      if not public.market_open(v_rec.asset_class::public.asset_class) then
        v_status := 'market_closed';
        v_hint := 'The session is closed; the last price is expected to be old.';
      else
        v_status := 'stale';
        v_hint := 'The session is open but the feed has gone quiet.';
      end if;
    else
      v_status := 'live';
      v_hint := null;
    end if;

    v_venues := v_venues || jsonb_build_array(jsonb_build_object(
      'venue', v_rec.venue,
      -- null means nobody has checked this venue's contract yet, which is
      -- not the same as a failed check.
      'contract_ok', v_rec.contract_ok,
      'contract_checked_at', v_rec.contract_checked_at,
      'enabled', coalesce(v_enabled, true),
      'configured', coalesce(v_configured, (v_connected is not null), false),
      'connected', coalesce(v_connected, false),
      'status', v_status,
      'hint', v_hint,
      'symbols', v_rec.symbols,
      'with_quotes', v_rec.with_quotes,
      'data_quality', v_rec.data_quality,
      'last_tick_at', v_rec.last_tick_at,
      'age_seconds', v_age
    ));
  end loop;

  return jsonb_build_object(
    'as_of', now(),
    'engine', public.engine_status(),
    'venues', v_venues
  );
end;
$$;

revoke all on function public.feed_health() from public, anon;
grant execute on function public.feed_health() to authenticated, service_role;
