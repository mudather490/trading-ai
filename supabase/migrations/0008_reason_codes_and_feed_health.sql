-- =============================================================================
-- 0008_reason_codes_and_feed_health.sql
--
-- Two additions, both additive. Neither loosens the gate.
--
-- 1. `get_ticket()` gains `reason_code` on every refusal branch. The five
--    STATUS values are unchanged (I-8 still holds): a refusal is a success
--    state. `reason_code` exists so the UI and the bot can say "Alpaca is not
--    connected" instead of "Data is stale", which is what the user saw.
--
--      engine_down          the worker heartbeat is older than 2 minutes
--      no_profile           no risk profile yet
--      unknown_instrument   the uuid does not exist
--      not_in_universe      tracked, but not tradable
--      no_data              the venue is connected but has no price yet
--      feed_not_configured  the venue was never connected (missing keys)
--      market_closed        the session is closed; no fresh price is expected
--      quote_too_old        the session is open but the quote is older than
--                           the asset-class window
--      no_champion          no promoted strategy for this asset class+timeframe
--      no_signal            the champion has no active setup right now
--      no_stop              the setup has no valid stop
--      risk_blocked         a cap or the daily ticket limit refused the size
--      rr_below_floor       reward:risk below the strategy's floor
--
-- 2. `feed_health()` publishes per-venue state for the UI banner:
--    configured / connected / last tick / age / quality. It reads the
--    heartbeat meta the worker already writes, so no new writes are needed.
--
-- `market_open()` is a session approximation, not an exchange calendar. It
-- knows weekends and US regular hours. Holidays fall back to `quote_too_old`,
-- which is the honest refusal: we do not pretend to know the NYSE calendar.
-- =============================================================================

-- ------------------------------------------------------------- sessions ----
create or replace function public.market_open(
  p_asset_class public.asset_class,
  p_at timestamptz default now()
)
returns boolean
language sql
stable
security invoker
set search_path = public
as $$
  select case p_asset_class
    when 'crypto' then true
    when 'us_equity' then
      -- Regular hours only: Mon-Fri 09:30-16:00 New York. Holidays are not
      -- modelled; a holiday refuses as quote_too_old rather than inventing.
      extract(isodow from (p_at at time zone 'America/New_York')) between 1 and 5
      and (p_at at time zone 'America/New_York')::time >= time '09:30'
      and (p_at at time zone 'America/New_York')::time <  time '16:00'
    else
      -- FX/metals: closed from Friday 22:00 UTC to Sunday 22:00 UTC.
      not (
        extract(isodow from (p_at at time zone 'UTC')) = 6
        or (extract(isodow from (p_at at time zone 'UTC')) = 5
            and (p_at at time zone 'UTC')::time >= time '22:00')
        or (extract(isodow from (p_at at time zone 'UTC')) = 7
            and (p_at at time zone 'UTC')::time < time '22:00')
      )
  end;
$$;

-- ---------------------------------------------------------- get_ticket -----
create or replace function public.get_ticket(
  p_user_id uuid,
  p_instrument uuid,
  p_timeframe text default '1h'
)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_eng        jsonb;
  v_status     text;
  v_quote      public.latest_quotes%rowtype;
  v_signal     public.signals%rowtype;
  v_profile    public.user_profiles%rowtype;
  v_champion   public.champions%rowtype;
  v_version    public.strategy_versions%rowtype;
  v_strategy   public.strategies%rowtype;
  v_instr      public.instruments%rowtype;
  v_risk       jsonb := '{}'::jsonb;
  v_warnings   jsonb := '[]'::jsonb;
  v_tickets_today integer;
  v_open_count    integer;
  v_gross_open    numeric;
  v_risk_amount   numeric;
  v_stop_distance numeric;
  v_size_units    numeric;
  v_size_cap_units numeric;
  v_gross_cap_units numeric;
  v_size_notional numeric;
  v_rr          numeric;
  v_rr_floor    numeric := 1.0;
  v_refusal     text;
  v_catalyst    record;
  v_mid         numeric;
  v_max_age     interval;
  v_metric      jsonb;
  v_feed_configured boolean;
  v_market_open     boolean;
begin

  -- ---- custody guard ------------------------------------------------------
  -- A ticket is computed from a user's capital, their open positions and their
  -- daily ticket count. It may therefore only be computed for the caller.
  -- The owner may compute one for anyone (that is the owner's job).
  if not public.caller_is_privileged()
     and p_user_id is distinct from auth.uid()
     and not public.is_owner() then
    raise exception 'Not permitted: a ticket may only be computed for yourself.'
      using errcode = '42501';
  end if;
  -- ---------------------------------------------------------------- engine --
  v_eng := public.engine_status();
  v_status := v_eng->>'status';
  if v_status is distinct from 'live' then
    return jsonb_build_object(
      'status', 'engine_unavailable',
      'reason_code', 'engine_down',
      'reason', 'The live engine is not running. No ticket can be issued.',
      'engine', v_eng
    );
  end if;

  -- -------------------------------------------------------------- profile --
  select * into v_profile from public.user_profiles where user_id = p_user_id;
  if not found then
    return jsonb_build_object(
      'status', 'no_match',
      'reason_code', 'no_profile',
      'reason', 'No risk profile. Complete onboarding to receive tickets.'
    );
  end if;

  -- ----------------------------------------------------------- instrument --
  select * into v_instr from public.instruments where id = p_instrument;
  if not found then
    return jsonb_build_object('status', 'no_match', 'reason_code', 'unknown_instrument',
                              'reason', 'Unknown instrument.');
  end if;
  if not v_instr.in_universe then
    return jsonb_build_object(
      'status', 'no_match',
      'reason_code', 'not_in_universe',
      'reason', format('%s is not in the trading universe.', v_instr.symbol)
    );
  end if;

  -- ---------------------------------------------------------- fresh quote --
  -- Is the venue even configured? A venue that was never connected is not a
  -- data outage: it is a setup problem, and it must say so. The worker
  -- publishes its configured venue list in the heartbeat meta.
  v_feed_configured := null;
  select nullif(h.meta->'venues'->v_instr.venue->>'configured', '')::boolean
    into v_feed_configured
    from public.worker_heartbeat h
    order by h.last_seen_at desc
    limit 1;

  v_market_open := public.market_open(v_instr.asset_class);

  select * into v_quote from public.latest_quotes where instrument_id = p_instrument;
  if not found then
    if not v_market_open then
      return jsonb_build_object(
        'status', 'stale',
        'reason_code', 'market_closed',
        'reason', format('%s is closed right now. No price is expected.', v_instr.symbol),
        'instrument', v_instr.symbol
      );
    end if;
    if v_feed_configured is false then
      return jsonb_build_object(
        'status', 'stale',
        'reason_code', 'feed_not_configured',
        'reason', format('%s is not connected. Add the %s credentials to the worker.',
                         v_instr.symbol, initcap(v_instr.venue)),
        'instrument', v_instr.symbol,
        'venue', v_instr.venue
      );
    end if;
    return jsonb_build_object(
      'status', 'stale',
      'reason_code', 'no_data',
      'reason', format('No price data for %s yet.', v_instr.symbol),
      'instrument', v_instr.symbol
    );
  end if;

  -- Freshness windows differ by asset class: crypto ticks continuously, US
  -- equities have session gaps and IEX is partial.
  v_max_age := case v_instr.asset_class
    when 'crypto' then interval '30 seconds'
    when 'forex'  then interval '2 minutes'
    when 'metal'  then interval '2 minutes'
    else interval '10 minutes'   -- us_equity: session gaps + IEX partial
  end;

  if v_quote.as_of < now() - v_max_age then
    if not v_market_open then
      return jsonb_build_object(
        'status', 'stale',
        'reason_code', 'market_closed',
        'reason', format('%s is closed. Last price is %s old; not acting on it.',
                          v_instr.symbol,
                          to_char(now() - v_quote.as_of, 'HH24:MI:SS')),
        'instrument', v_instr.symbol,
        'data_as_of', v_quote.as_of,
        'age_seconds', extract(epoch from (now() - v_quote.as_of))
      );
    end if;
    return jsonb_build_object(
      'status', 'stale',
      'reason_code', 'quote_too_old',
      'reason', format('Last price for %s is %s old. Not acting on stale data.',
                        v_instr.symbol,
                        to_char(now() - v_quote.as_of, 'MI:SS')),
      'instrument', v_instr.symbol,
      'data_as_of', v_quote.as_of,
      'age_seconds', extract(epoch from (now() - v_quote.as_of))
    );
  end if;

  -- ------------------------------------------------------ daily ticket cap --
  select count(*) into v_tickets_today
    from public.tickets
    where user_id = p_user_id and created_at >= date_trunc('day', now());
  if v_tickets_today >= v_profile.max_tickets_per_day then
    return jsonb_build_object(
      'status', 'wait',
      'reason_code', 'risk_blocked',
      'reason', format('Daily ticket limit reached (%s).', v_profile.max_tickets_per_day),
      'tickets_today', v_tickets_today
    );
  end if;

  -- ------------------------------------------------- concurrent positions --
  select count(*) into v_open_count
    from public.tickets
    where user_id = p_user_id and state = 'active';
  if v_open_count >= v_profile.max_concurrent then
    return jsonb_build_object(
      'status', 'wait',
      'reason_code', 'risk_blocked',
      'reason', format('Concurrent position limit reached (%s).', v_profile.max_concurrent),
      'open_tickets', v_open_count
    );
  end if;

  -- ------------------------------------------------------- champion lookup --
  -- Only a PROMOTED champion for this asset class + timeframe may produce a
  -- ticket. An unregistered strategy can never become advice (I-5, condition 3).
  select c.* into v_champion
    from public.champions c
    join public.strategy_versions sv on sv.id = c.version_id
    join public.strategies s on s.id = sv.strategy_id
    where c.asset_class = v_instr.asset_class
      and c.timeframe = p_timeframe
      and c.active
      and sv.status = 'champion'
    limit 1;

  if not found then
    return jsonb_build_object(
      'status', 'no_match',
      'reason_code', 'no_champion',
      'reason', format('No promoted %s strategy on the %s timeframe yet.',
                        v_instr.asset_class, p_timeframe),
      'instrument', v_instr.symbol
    );
  end if;

  select * into v_version   from public.strategy_versions where id = v_champion.version_id;
  select * into v_strategy  from public.strategies      where id = v_version.strategy_id;
  v_risk := coalesce(v_version.risk_spec, '{}'::jsonb);
  v_rr_floor := coalesce((v_risk->>'min_reward_risk')::numeric, 1.0);

  -- ------------------------------------------------------ active signal ----
  select * into v_signal
    from public.signals
    where instrument_id = p_instrument
      and timeframe = p_timeframe
      and status = 'active'
      and (p_timeframe = '1d' or bar_close_time > now() - interval '15 minutes')
      and (expires_at is null or expires_at > now())
    order by bar_close_time desc
    limit 1;

  if not found then
    return jsonb_build_object(
      'status', 'wait',
      'reason_code', 'no_signal',
      'reason', format('No %s setup for %s right now. The champion is waiting.',
                        p_timeframe, v_instr.symbol),
      'instrument', v_instr.symbol,
      'strategy', v_strategy.name,
      'strategy_version', v_version.version
    );
  end if;

  if v_signal.side = 'flat' then
    return jsonb_build_object(
      'status', 'wait',
      'reason_code', 'no_signal',
      'reason', format('The champion is flat on %s.', v_instr.symbol),
      'instrument', v_instr.symbol
    );
  end if;

  -- ------------------------------------------------------------- sizing ----
  v_mid := case
    when v_signal.entry_low is not null and v_signal.entry_high is not null
      then (v_signal.entry_low + v_signal.entry_high) / 2
    else v_signal.entry_price
  end;

  if v_signal.stop_price is null or v_mid is null or v_signal.stop_price >= v_mid then
    return jsonb_build_object(
      'status', 'wait',
      'reason_code', 'no_stop',
      'reason', 'The setup has no valid stop. Refusing to size it.',
      'instrument', v_instr.symbol
    );
  end if;

  v_stop_distance := abs(v_mid - v_signal.stop_price);
  if v_stop_distance = 0 then
    return jsonb_build_object(
      'status', 'wait',
      'reason_code', 'no_stop',
      'reason', 'Stop distance is zero. Refusing to size it.',
      'instrument', v_instr.symbol
    );
  end if;

  v_risk_amount := coalesce(v_profile.capital_usd, 0) * v_profile.risk_per_trade_pct;

  if v_profile.capital_usd is null or v_profile.capital_usd <= 0 then
    -- No capital set: we can still publish the ticket with per-unit sizing.
    v_size_units := null;
    v_size_notional := null;
  else
    v_size_units := floor(v_risk_amount / v_stop_distance);
    -- Position cap
    v_size_cap_units := floor(
      (v_profile.capital_usd * v_profile.max_position_pct) / v_mid);
    -- Gross exposure cap
    select coalesce(sum(coalesce(size_notional, 0)), 0) into v_gross_open
      from public.tickets where user_id = p_user_id and state = 'active';
    v_gross_cap_units := floor(
      greatest(coalesce(v_profile.capital_usd, 0) * v_profile.max_gross_pct - v_gross_open, 0)
      / v_mid);
    v_size_units := least(coalesce(v_size_units, v_size_cap_units),
                          v_size_cap_units, v_gross_cap_units);
    if v_size_units <= 0 then
      v_refusal := case
        when v_gross_cap_units <= 0 then 'Gross exposure cap reached.'
        when v_size_cap_units <= 0 then 'Position size cap reached.'
        else 'The stop is too far away to size within the risk budget.'
      end;
      return jsonb_build_object(
        'status', 'wait',
        'reason_code', 'risk_blocked',
        'reason', v_refusal,
        'instrument', v_instr.symbol,
        'risk_budget_usd', v_risk_amount,
        'stop_distance', v_stop_distance
      );
    end if;
    v_size_notional := v_size_units * v_mid;
  end if;

  -- ------------------------------------------------------ reward : risk ----
  v_rr := case
    when v_signal.target_price is null then null
    else abs(v_signal.target_price - v_mid) / v_stop_distance
  end;

  if v_rr is not null and v_rr < v_rr_floor then
    return jsonb_build_object(
      'status', 'wait',
      'reason_code', 'rr_below_floor',
      'reason', format('Reward:risk %s is below the %s floor. Not worth taking.',
                        to_char(v_rr, 'FM990.00'), to_char(v_rr_floor, 'FM990.00')),
      'instrument', v_instr.symbol,
      'reward_risk', v_rr,
      'rr_floor', v_rr_floor
    );
  end if;

  -- ------------------------------------------------------ news risk flag ---
  -- News NEVER changes the size (I-6). It can only add a warning, and at the
  -- 1m timeframe news features are not read at all (condition 8).
  if p_timeframe <> '1m' then
    select c.kind, c.title, c.occurs_at, c.severity into v_catalyst
      from public.catalysts c
      where c.instrument_id = p_instrument
        and c.occurs_at between now() and now() + interval '24 hours'
      order by c.occurs_at
      limit 1;

    if found then
      v_warnings := v_warnings || jsonb_build_array(jsonb_build_object(
        'kind', 'catalyst',
        'severity', v_catalyst.severity,
        'text', format('%s: %s (%s)',
                       v_catalyst.kind, v_catalyst.title,
                       to_char(v_catalyst.occurs_at, 'DD Mon HH24:MI')),
        'at', v_catalyst.occurs_at
      ));
    end if;
  end if;

  if v_quote.bid is not null and v_quote.ask is not null and v_mid > 0 then
    if (v_quote.ask - v_quote.bid) / v_mid > 0.005 then
      v_warnings := v_warnings || jsonb_build_array(jsonb_build_object(
        'kind', 'spread',
        'severity', 'medium',
        'text', format('Spread is %s%% of price. Wide.',
                       round(((v_quote.ask - v_quote.bid) / v_mid) * 100, 3))
      ));
    end if;
  end if;

  -- --------------------------------------------------------- the metrics ---
  select jsonb_build_object(
    'trades', p.trades,
    'win_rate', p.win_rate,
    'expectancy', p.expectancy,
    'sharpe', p.sharpe,
    'max_drawdown', p.max_drawdown,
    'dsr', p.dsr,
    'pbo', p.pbo,
    'n_trials', p.n_trials,
    'period_start', p.period_start,
    'period_end', p.period_end,
    'benchmark', p.benchmark,
    'benchmark_return', p.benchmark_ret,
    'data_source', p.data_source
  ) into v_metric
  from public.performance p
  where p.version_id = v_version.id
    and p.timeframe = p_timeframe
  order by p.as_of desc
  limit 1;

  v_metric := coalesce(v_metric, jsonb_build_object(
    'trades', null,
    'note', 'No published performance record for this version yet.'
  ));

  -- ------------------------------------------------------------ the ticket --
  return jsonb_build_object(
    'status', 'ticket',
    'instrument', v_instr.symbol,
    'instrument_id', v_instr.id,
    'venue', v_instr.venue,
    'asset_class', v_instr.asset_class,
    'timeframe', p_timeframe,
    'side', v_signal.side,
    'rule_id', v_signal.rule_id,
    'entry_price', v_signal.entry_price,
    'entry_low', v_signal.entry_low,
    'entry_high', v_signal.entry_high,
    'stop_price', v_signal.stop_price,
    'target_price', v_signal.target_price,
    'target_2_price', v_signal.target_2_price,
    'size_units', v_size_units,
    'size_notional', v_size_notional,
    'risk_amount', v_risk_amount,
    'stop_distance', v_stop_distance,
    'reward_risk', v_rr,
    'confidence', v_signal.confidence,
    'strategy_id', v_strategy.id,
    'strategy_name', v_strategy.name,
    'strategy_family', v_strategy.family,
    'version_id', v_version.id,
    'version', v_version.version,
    'metrics', v_metric,
    'odds_band', v_signal.odds_band,
    'warnings', v_warnings,
    'data_source', v_quote.source,
    'data_quality', v_instr.data_quality,
    'data_as_of', v_quote.as_of,
    'signal_bar_close_time', v_signal.bar_close_time,
    'signal_id', v_signal.id,
    'engine', v_eng
  );
end;
$$;

revoke all on function public.get_ticket(uuid, uuid, text) from public, anon;
grant execute on function public.get_ticket(uuid, uuid, text) to authenticated, service_role;

-- ---------------------------------------------------------- feed_health ----
-- One row per venue in the universe, plus the engine block. This is the
-- source of the "Alpaca not connected — add keys" banner. A venue that is
-- configured but silent is `down`; one that is streaming is `live`.
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
           (array_agg(i.asset_class::text))[1] as asset_class
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

comment on function public.feed_health() is
  'Per-venue data status for the UI banner. configured=false means the venue was never connected; that is a setup state, not an outage.';
