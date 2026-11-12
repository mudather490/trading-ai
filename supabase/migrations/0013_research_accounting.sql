-- =============================================================================
-- 0013_research_accounting.sql
--
-- Two things, both about honesty in the research path.
--
-- 1. `research_runs`: a row for every research run, including the dry ones.
--
--    The deflated Sharpe ratio penalises a search by the number of
--    candidates it tried. Until now the runner reported the size of its own
--    grid, which is the smallest number that flatters it: a search that ran
--    three grids of ten candidates had tried thirty things, and the gate was
--    told "ten". Every run is now recorded, dry runs included, and the trial
--    count is the whole history.
--
-- 2. A new gate rule: `holdout_contaminated`.
--
--    The existing per-version holdout counter stops the SAME candidate from
--    being re-judged on a holdout it has already seen. It does not stop a
--    DIFFERENT candidate from being chosen by reading the same tail, which
--    is how a holdout stops being a holdout. The runner marks a candidate
--    whose dataset's tail was read by an earlier run, and the gate refuses
--    it with the reason published.
--
-- Nothing else about the gate changes: same thresholds, same owner-only
-- promotion, same published before/after.
-- =============================================================================

create table public.research_runs (
  id           uuid primary key default gen_random_uuid(),
  symbol       text        not null,
  timeframe    text        not null,
  template_id  text        not null,
  -- How many candidates this run evaluated. Each one is a trial.
  candidates   integer     not null default 0,
  trades       integer,
  sharpe       numeric,
  dsr          numeric,
  pbo          numeric,
  -- Did this run read the holdout tail of its dataset?
  holdout_read boolean     not null default false,
  dry_run      boolean     not null default true,
  note         text,
  created_at   timestamptz not null default now()
);

create index research_runs_dataset_idx
  on public.research_runs (symbol, timeframe, created_at desc);
create index research_runs_created_idx
  on public.research_runs (created_at desc);

comment on table public.research_runs is
  'Every research run, dry ones included. The trial count is the sum of the '
  'candidates over all rows, and the holdout of a dataset may be read once.';

alter table public.research_runs enable row level security;

create policy research_runs_read on public.research_runs
  for select to authenticated using (true);

create policy research_runs_owner_write on public.research_runs
  for all to authenticated
  using (public.is_owner()) with check (public.is_owner());

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

  -- ---- holdout contamination (I-21): the DATASET's holdout, read once
  --
  -- The per-version counter above stops the SAME candidate from being
  -- re-judged on a holdout it has already seen. It does not stop a
  -- DIFFERENT candidate from being chosen by reading the same tail.
  --
  -- That is the classic holdout-shopping mistake: run three templates over
  -- the same dataset, read the tail three times, keep whichever one looked
  -- good. The tail stopped being a holdout on the first read and became a
  -- validation set.
  --
  -- The research runner records every read in `research_runs` and marks the
  -- candidate when the tail of its dataset has been read before. The gate
  -- refuses those candidates, with the reason published.
  if coalesce(v_perf.notes->>'holdout_contaminated', 'false')::boolean then
    v_ok := false;
    v_failures := v_failures || jsonb_build_array(jsonb_build_object(
      'rule', 'holdout_contaminated', 'ok', false,
      'value', true, 'threshold', false,
      'detail', 'The holdout of this dataset was read by an earlier research run. A holdout that has been read twice is a validation set, and a candidate chosen on it is not out of sample.'
    ));
  end if;

  -- ---- AI Feature Gate (I-14)
  -- The AI feature gate applies to AI-WRITTEN candidates. A template
  -- candidate has no AI-derived features, so demanding a positive feature
  -- gain from it would reject it for the crime of having no AI in it.
  if (v_th->>'require_ai_feature_gain')::boolean
     and v_version.origin in ('ai_mutation', 'ai_codegen')
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

revoke all on function public.promote_strategy(uuid, text) from public, anon;
grant execute on function public.promote_strategy(uuid, text) to authenticated;
