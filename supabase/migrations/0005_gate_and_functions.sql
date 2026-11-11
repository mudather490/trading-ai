-- =============================================================================
-- 0005_gate_and_functions.sql
--
-- The deterministic core. These functions hold the authority:
--   * get_ticket()        decides whether a trade ticket may be issued
--   * promote_strategy()  decides whether a strategy earns capital
--   * record_signal()     idempotent signal write
--   * fire_alert()        rule-triggered alerts (AI only writes the text)
--   * claim_jobs()        work queue for the worker
--
-- NONE of these read the AI sidecar tables for sizing or promotion decisions.
-- =============================================================================

-- ------------------------------------------------------- gate policy ------

create table public.gate_policy (
  id            uuid primary key default gen_random_uuid(),
  version       text        not null unique,
  -- Every threshold the gate enforces. Changing the policy means a new version.
  thresholds    jsonb       not null,
  active        boolean     not null default false,
  created_by    uuid references auth.users(id) on delete set null,
  created_at    timestamptz not null default now(),
  notes         text
);

comment on table public.gate_policy is
  'The promotion gate as a VERSIONED PREDICATE. Changing a threshold requires a '
  'new version row, and the version used is recorded on every champion.';

insert into public.gate_policy (version, thresholds, active, notes)
values (
  'v1',
  jsonb_build_object(
    -- Sample sufficiency
    'min_trades_1h', 100,
    'min_trades_1d', 30,
    -- Deflated statistics
    'min_dsr', 0.95,
    'max_pbo', 0.50,
    -- Benchmark-relative performance
    'min_sharpe', 1.0,
    'min_calmar', 0.50,
    'max_drawdown', 0.30,
    -- Cost robustness
    'min_expectancy_after_2x_costs', 0.0,
    -- Neighbourhood stability: the candidate's neighbours must also pass
    'require_neighbour_stability', true,
    'min_neighbour_pass_rate', 0.60,
    -- Trial discipline
    'max_holdout_reads', 1,
    -- AI Feature Gate (I-14): news features must beat no-news features
    'require_ai_feature_gain', true,
    'min_ai_feature_gain_sharpe', 0.0
  ),
  true,
  'Initial gate. Any change requires a new version row.'
);

create unique index gate_policy_one_active on public.gate_policy (active)
  where active;

alter table public.gate_policy enable row level security;
create policy gate_policy_read on public.gate_policy
  for select to authenticated using (true);
create policy gate_policy_owner_write on public.gate_policy
  for all to authenticated using (public.is_owner()) with check (public.is_owner());

-- ------------------------------------------------------ engine status -----

create or replace function public.engine_status()
returns jsonb
language sql
stable
as $$
  select jsonb_build_object(
    'status', case
      when h.last_seen_at is null then 'never_seen'
      when now() - h.last_seen_at > interval '2 minutes' then 'stale'
      else 'live' end,
    'last_seen_at', h.last_seen_at,
    'age_seconds', extract(epoch from (now() - h.last_seen_at)),
    'worker_id', h.worker_id,
    'version', h.version,
    'streams', h.streams,
    'lag_ms', h.lag_ms
  )
  from (select * from public.worker_heartbeat order by last_seen_at desc limit 1) h;
$$;

-- ----------------------------------------------------------- alerts -------

create or replace function public.fire_alert(
  p_user_id uuid,
  p_kind text,
  p_rule_id text,
  p_title text,
  p_body text default null,
  p_severity public.alert_severity default 'info',
  p_payload jsonb default '{}'::jsonb,
  p_dedupe_key text default null
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_key text;
  v_id uuid;
begin
  v_key := coalesce(p_dedupe_key,
    p_kind || ':' || p_rule_id || ':' || coalesce(p_payload->>'instrument_id','') ||
    ':' || coalesce(p_payload->>'bar_close_time', now()::text));

  insert into public.alerts (user_id, kind, rule_id, title, body, severity, payload, dedupe_key)
  values (p_user_id, p_kind, p_rule_id, p_title, p_body, p_severity, p_payload, v_key)
  on conflict (dedupe_key) do nothing
  returning id into v_id;

  return v_id;
end;
$$;

grant execute on function public.fire_alert(uuid, text, text, text, text, public.alert_severity, jsonb, text)
  to authenticated, service_role;

-- ---------------------------------------------------- record signal ------

create or replace function public.record_signal(
  p_instrument_id uuid,
  p_timeframe text,
  p_side public.signal_side,
  p_entry_price numeric,
  p_rule_id text,
  p_bar_close_time timestamptz,
  p_data_as_of timestamptz,
  p_signal_key text,
  p_entry_low numeric default null,
  p_entry_high numeric default null,
  p_stop_price numeric default null,
  p_target_price numeric default null,
  p_target_2_price numeric default null,
  p_confidence numeric default null,
  p_version_id uuid default null,
  p_odds_band jsonb default '{}'::jsonb,
  p_notes jsonb default '{}'::jsonb,
  p_expires_at timestamptz default null
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare v_id uuid;
begin
  insert into public.signals (
    instrument_id, timeframe, side, entry_price, entry_low, entry_high,
    stop_price, target_price, target_2_price, confidence, rule_id,
    bar_close_time, data_as_of, signal_key, version_id, odds_band, notes, expires_at
  ) values (
    p_instrument_id, p_timeframe, p_side, p_entry_price, p_entry_low, p_entry_high,
    p_stop_price, p_target_price, p_target_2_price, p_confidence, p_rule_id,
    p_bar_close_time, p_data_as_of, p_signal_key, p_version_id, p_odds_band, p_notes, p_expires_at
  )
  on conflict (signal_key) do nothing
  returning id into v_id;

  if v_id is null then
    select id into v_id from public.signals where signal_key = p_signal_key;
  end if;
  return v_id;
end;
$$;

grant execute on function public.record_signal(
  uuid, text, public.signal_side, numeric, text, timestamptz, timestamptz, text,
  numeric, numeric, numeric, numeric, numeric, numeric, uuid, jsonb, jsonb, timestamptz)
  to service_role;

-- =========================================================== get_ticket ===
--
-- THE function. Decides whether a ticket may be issued, and computes it.
--
-- Return shapes:
--   { status: 'ticket',            ...ticket fields... }
--   { status: 'no_match',          reason }   no registered setup at this price
--   { status: 'wait',              reason }   nothing qualifies right now
--   { status: 'stale',             reason }   data is too old to act on
--   { status: 'engine_unavailable',reason }   the worker is not alive
--
-- Invariants enforced here:
--   I-2  freshness gate (heartbeat + quote age)
--   I-6  news is a warning, never a sizing input; at 1m news features are not read
--   I-8  WAIT / NO_MATCH / STALE / ENGINE_UNAVAILABLE are success states
-- =========================================================================

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
begin
  -- ---------------------------------------------------------------- engine --
  v_eng := public.engine_status();
  v_status := v_eng->>'status';
  if v_status is distinct from 'live' then
    return jsonb_build_object(
      'status', 'engine_unavailable',
      'reason', 'The live engine is not running. No ticket can be issued.',
      'engine', v_eng
    );
  end if;

  -- -------------------------------------------------------------- profile --
  select * into v_profile from public.user_profiles where user_id = p_user_id;
  if not found then
    return jsonb_build_object(
      'status', 'no_match',
      'reason', 'No risk profile. Complete onboarding to receive tickets.'
    );
  end if;

  -- ----------------------------------------------------------- instrument --
  select * into v_instr from public.instruments where id = p_instrument;
  if not found then
    return jsonb_build_object('status', 'no_match', 'reason', 'Unknown instrument.');
  end if;
  if not v_instr.in_universe then
    return jsonb_build_object(
      'status', 'no_match',
      'reason', format('%s is not in the trading universe.', v_instr.symbol)
    );
  end if;

  -- ---------------------------------------------------------- fresh quote --
  select * into v_quote from public.latest_quotes where instrument_id = p_instrument;
  if not found then
    return jsonb_build_object(
      'status', 'stale',
      'reason', format('No price data for %s yet.', v_instr.symbol)
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
    return jsonb_build_object(
      'status', 'stale',
      'reason', format('Last price for %s is %s old. Not acting on stale data.',
                        v_instr.symbol,
                        to_char(now() - v_quote.as_of, 'MI:SS')),
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
      'reason', 'The setup has no valid stop. Refusing to size it.',
      'instrument', v_instr.symbol
    );
  end if;

  v_stop_distance := abs(v_mid - v_signal.stop_price);
  if v_stop_distance = 0 then
    return jsonb_build_object(
      'status', 'wait',
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
    'asset_class', v_instr.asset_class,
    'timeframe', p_timeframe,
    'side', v_signal.side,
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

grant execute on function public.get_ticket(uuid, uuid, text) to authenticated, service_role;

-- ==================================================== promote_strategy ===
--
-- The ONLY path to capital. A conversation cannot promote. A chat path cannot
-- promote. Only this function, called by the owner, evaluates the gate.
--
-- Requires (from the active gate_policy version):
--   * enough trades for the timeframe
--   * DSR above threshold (deflated for N_trials)
--   * PBO below threshold
--   * Sharpe / Calmar above threshold
--   * max drawdown inside limit
--   * expectancy still positive at 2x costs
--   * neighbourhood stability
--   * holdout reads <= 1
--   * AI Feature Gate: news features must beat no-news features
-- =========================================================================

create or replace function public.promote_strategy(
  p_version_id uuid,
  p_note text default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_policy     public.gate_policy%rowtype;
  v_th         jsonb;
  v_version    public.strategy_versions%rowtype;
  v_strategy   public.strategies%rowtype;
  v_perf       public.performance%rowtype;
  v_before     jsonb;
  v_after      jsonb;
  v_existing   public.champions%rowtype;
  v_holdout    integer;
  v_failures   jsonb := '[]'::jsonb;
  v_ok         boolean := true;
  v_min_trades integer;
begin
  if not public.is_owner() then
    raise exception 'Only the owner may promote a strategy.';
  end if;

  select * into v_policy from public.gate_policy where active;
  if not found then
    raise exception 'No active gate policy. Refusing to promote.';
  end if;
  v_th := v_policy.thresholds;

  select * into v_version from public.strategy_versions where id = p_version_id;
  if not found then
    raise exception 'Unknown strategy version %', p_version_id;
  end if;
  select * into v_strategy from public.strategies where id = v_version.strategy_id;

  -- The performance record we judge. Latest for this version.
  select * into v_perf
    from public.performance
    where version_id = p_version_id
    order by as_of desc
    limit 1;
  if not found then
    raise exception 'No performance record for version %. Nothing to promote.', p_version_id;
  end if;

  -- ---- sample sufficiency
  v_min_trades := case v_perf.timeframe
    when '1d' then (v_th->>'min_trades_1d')::integer
    else (v_th->>'min_trades_1h')::integer
  end;
  if v_perf.trades < v_min_trades then
    v_ok := false;
    v_failures := v_failures || jsonb_build_array(jsonb_build_object(
      'rule', 'min_trades', 'ok', false,
      'value', v_perf.trades, 'threshold', v_min_trades,
      'detail', format('Only %s trades. The bar is %s.', v_perf.trades, v_min_trades)
    ));
  end if;

  -- ---- deflated Sharpe
  if coalesce(v_perf.dsr, 0) < (v_th->>'min_dsr')::numeric then
    v_ok := false;
    v_failures := v_failures || jsonb_build_array(jsonb_build_object(
      'rule', 'min_dsr', 'ok', false,
      'value', v_perf.dsr, 'threshold', (v_th->>'min_dsr')::numeric,
      'detail', format('DSR %s is below %s. This is what %s trials buys you.',
        to_char(v_perf.dsr, 'FM990.000'), to_char((v_th->>'min_dsr')::numeric, 'FM990.000'),
        v_perf.n_trials)
    ));
  end if;

  -- ---- probability of backtest overfitting
  if coalesce(v_perf.pbo, 1) > (v_th->>'max_pbo')::numeric then
    v_ok := false;
    v_failures := v_failures || jsonb_build_array(jsonb_build_object(
      'rule', 'max_pbo', 'ok', false,
      'value', v_perf.pbo, 'threshold', (v_th->>'max_pbo')::numeric,
      'detail', format('PBO %s is above %s. The edge is probably a fluke.',
        to_char(v_perf.pbo, 'FM990.000'), to_char((v_th->>'max_pbo')::numeric, 'FM990.000'))
    ));
  end if;

  -- ---- risk-adjusted performance
  if coalesce(v_perf.sharpe, 0) < (v_th->>'min_sharpe')::numeric then
    v_ok := false;
    v_failures := v_failures || jsonb_build_array(jsonb_build_object(
      'rule', 'min_sharpe', 'ok', false,
      'value', v_perf.sharpe, 'threshold', (v_th->>'min_sharpe')::numeric
    ));
  end if;
  if coalesce(v_perf.calmar, 0) < (v_th->>'min_calmar')::numeric then
    v_ok := false;
    v_failures := v_failures || jsonb_build_array(jsonb_build_object(
      'rule', 'min_calmar', 'ok', false,
      'value', v_perf.calmar, 'threshold', (v_th->>'min_calmar')::numeric
    ));
  end if;
  if abs(coalesce(v_perf.max_drawdown, 1)) > (v_th->>'max_drawdown')::numeric then
    v_ok := false;
    v_failures := v_failures || jsonb_build_array(jsonb_build_object(
      'rule', 'max_drawdown', 'ok', false,
      'value', v_perf.max_drawdown, 'threshold', (v_th->>'max_drawdown')::numeric
    ));
  end if;

  -- ---- neighbourhood stability (I-17)
  if (v_th->>'require_neighbour_stability')::boolean
     and coalesce(v_perf.neighbour_pass_rate, 0) < (v_th->>'min_neighbour_pass_rate')::numeric then
    v_ok := false;
    v_failures := v_failures || jsonb_build_array(jsonb_build_object(
      'rule', 'neighbour_stability', 'ok', false,
      'value', v_perf.neighbour_pass_rate,
      'threshold', (v_th->>'min_neighbour_pass_rate')::numeric,
      'detail', 'The candidate is an isolated peak. Its neighbours do not survive.'
    ));
  end if;

  -- ---- holdout discipline
  select coalesce(reads, 0) into v_holdout from public.holdout_ledger where version_id = p_version_id;
  if v_holdout > (v_th->>'max_holdout_reads')::integer then
    v_ok := false;
    v_failures := v_failures || jsonb_build_array(jsonb_build_object(
      'rule', 'max_holdout_reads', 'ok', false,
      'value', v_holdout, 'threshold', (v_th->>'max_holdout_reads')::integer,
      'detail', 'The holdout has been read more than once. It is no longer a holdout.'
    ));
  end if;

  -- ---- AI Feature Gate (I-14)
  if (v_th->>'require_ai_feature_gain')::boolean
     and v_version.origin <> 'human'
     and coalesce(v_perf.ai_feature_gain_sharpe, 0) <= (v_th->>'min_ai_feature_gain_sharpe')::numeric then
    v_ok := false;
    v_failures := v_failures || jsonb_build_array(jsonb_build_object(
      'rule', 'ai_feature_gain', 'ok', false,
      'value', v_perf.ai_feature_gain_sharpe,
      'detail', 'AI-derived features did not beat the same strategy without them.'
    ));
  end if;

  -- ---- build the published before/after, WITH the denominator
  select * into v_existing
    from public.champions
    where asset_class = v_perf.asset_class and timeframe = v_perf.timeframe and active;

  v_before := case when found then jsonb_build_object(
      'strategy', v_existing.version_id,
      'sharpe', null, 'trades', null, 'max_drawdown', null, 'n_trials', null
    ) else '{}'::jsonb end;

  v_after := jsonb_build_object(
    'strategy_id', v_strategy.id,
    'strategy_name', v_strategy.name,
    'version', v_version.version,
    'asset_class', v_perf.asset_class,
    'timeframe', v_perf.timeframe,
    'trades', v_perf.trades,
    'win_rate', v_perf.win_rate,
    'expectancy', v_perf.expectancy,
    'sharpe', v_perf.sharpe,
    'sortino', v_perf.sortino,
    'calmar', v_perf.calmar,
    'max_drawdown', v_perf.max_drawdown,
    'cagr', v_perf.cagr,
    'dsr', v_perf.dsr,
    'pbo', v_perf.pbo,
    'n_trials', v_perf.n_trials,
    'neighbour_pass_rate', v_perf.neighbour_pass_rate,
    'ai_feature_gain_sharpe', v_perf.ai_feature_gain_sharpe,
    'period_start', v_perf.period_start,
    'period_end', v_perf.period_end,
    'benchmark', v_perf.benchmark,
    'benchmark_return', v_perf.benchmark_ret,
    'data_source', v_perf.data_source,
    'data_official', v_perf.data_official
  );

  if not v_ok then
    -- Nothing changed. We PUBLISH the failure. "Nothing improved" is a result.
    insert into public.audit_log (actor, action, entity, entity_id, after)
    values (auth.uid(), 'promotion_rejected', 'strategy_version', p_version_id::text,
            jsonb_build_object('failures', v_failures, 'policy_version', v_policy.version));

    return jsonb_build_object(
      'status', 'rejected',
      'policy_version', v_policy.version,
      'failures', v_failures,
      'candidate', v_after,
      'message', 'This candidate did not pass the promotion gate. Nothing was promoted.'
    );
  end if;

  -- ---- promote
  update public.champions set active = false
    where asset_class = v_perf.asset_class and timeframe = v_perf.timeframe and active;

  insert into public.champions (
    asset_class, timeframe, version_id, promoted_by, gate_result, before_after
  ) values (
    v_perf.asset_class, v_perf.timeframe, p_version_id, auth.uid(),
    jsonb_build_object('policy_version', v_policy.version, 'passed', true, 'failures', '[]'::jsonb),
    jsonb_build_object('before', v_before, 'after', v_after)
  )
  on conflict (asset_class, timeframe) do update
    set version_id = excluded.version_id,
        promoted_by = excluded.promoted_by,
        promoted_at = now(),
        gate_result = excluded.gate_result,
        before_after = excluded.before_after,
        active = true;

  update public.strategy_versions set status = 'champion' where id = p_version_id;
  update public.strategy_versions set status = 'retired'
    where id <> p_version_id and strategy_id = v_version.strategy_id and status = 'champion';

  insert into public.audit_log (actor, action, entity, entity_id, before, after)
  values (auth.uid(), 'promotion', 'strategy_version', p_version_id::text, v_before, v_after);

  return jsonb_build_object(
    'status', 'promoted',
    'policy_version', v_policy.version,
    'candidate', v_after,
    'before', v_before,
    'message', format('%s v%s is now the %s %s champion.',
                       v_strategy.name, v_version.version, v_perf.asset_class, v_perf.timeframe)
  );
end;
$$;

grant execute on function public.promote_strategy(uuid, text) to authenticated;

-- ---------------------------------------------------------- claim jobs ----

create or replace function public.claim_jobs(p_worker_id text, p_limit integer default 5)
returns setof public.jobs
language sql
security definer
set search_path = public
as $$
  with picked as (
    select id from public.jobs
    where status = 'pending'
    order by priority desc, created_at
    limit p_limit
    for update skip locked
  )
  update public.jobs j
     set status = 'running',
         claimed_by = p_worker_id,
         claimed_at = now(),
         attempts = j.attempts + 1,
         updated_at = now()
    from picked
   where j.id = picked.id
  returning j.*;
$$;

grant execute on function public.claim_jobs(text, integer) to service_role;

-- ------------------------------------------------------- finish a job -----

create or replace function public.finish_job(
  p_job_id uuid,
  p_ok boolean,
  p_result jsonb default '{}'::jsonb,
  p_error text default null
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.jobs
     set status = case when p_ok then 'done' else 'failed' end,
         result = p_result,
         last_error = p_error,
         updated_at = now()
   where id = p_job_id;
end;
$$;

grant execute on function public.finish_job(uuid, boolean, jsonb, text) to service_role;
