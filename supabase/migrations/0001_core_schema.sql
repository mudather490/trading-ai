-- =============================================================================
-- 0001_core_schema.sql
-- Trading Command Center — core market, strategy and signal tables.
--
-- Design rules enforced here:
--   * No raw ticks. latest_quotes holds one row per instrument; bars hold OHLCV.
--   * Every price row carries `source` + `official` + `as_of` for provenance.
--   * Signals are immutable once written (bar_close_time, never revised).
-- =============================================================================

-- gen_random_uuid() is a core Postgres function since v13, so this schema needs
-- no extension. That keeps the migrations portable to any Postgres >= 13
-- (Supabase, PGlite, a local instance) with no superuser step.

-- ---------------------------------------------------------------- enums ----

create type public.asset_class as enum ('us_equity', 'crypto', 'forex', 'metal');
create type public.data_quality as enum ('official', 'unofficial', 'indicative', 'partial');
create type public.signal_side as enum ('buy', 'sell', 'flat');
create type public.signal_status as enum ('active', 'expired', 'invalidated', 'filled');
create type public.strategy_status as enum ('draft', 'challenger', 'champion', 'retired');
create type public.ticket_state as enum ('active', 'journaled', 'expired', 'invalidated');
create type public.user_role as enum ('owner', 'trusted');
create type public.event_severity as enum ('high', 'medium', 'low', 'info');
create type public.news_confidence as enum ('high', 'medium', 'low');

-- ------------------------------------------------------------- helpers ----

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ------------------------------------------------------------ instruments --

create table public.instruments (
  id            uuid primary key default gen_random_uuid(),
  symbol        text        not null unique,
  venue         text        not null,              -- 'alpaca' | 'binance' | 'oanda'
  asset_class   public.asset_class not null,
  display_name  text,
  currency      text        not null default 'USD',
  tick_size     numeric(20,10),
  session_tz    text        not null default 'UTC',
  -- Research universe membership
  in_universe   boolean     not null default false,
  -- Which data feed is authoritative for this symbol
  primary_source text,
  data_quality  public.data_quality not null default 'unofficial',
  -- Provenance notes, e.g. 'Alpaca IEX only; partial market coverage'
  notes         text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

comment on table public.instruments is
  'The tradable universe. in_universe=false instruments are tracked but never ticketed.';

create index instruments_universe_idx on public.instruments (in_universe)
  where in_universe;

-- --------------------------------------------------------- latest quotes ---

create table public.latest_quotes (
  instrument_id uuid primary key references public.instruments(id) on delete cascade,
  bid           numeric(24,10),
  ask           numeric(24,10),
  last          numeric(24,10),
  last_size     numeric(24,10),
  change_pct    numeric(12,6),
  volume        numeric(28,10),
  source        text        not null,
  official      boolean     not null default false,
  as_of         timestamptz not null,
  updated_at    timestamptz not null default now()
);

comment on table public.latest_quotes is
  'One row per instrument. Written by the live worker only. Never stores tick history.';

-- ------------------------------------------------------------------ bars ---

create table public.bars (
  instrument_id uuid        not null references public.instruments(id) on delete cascade,
  timeframe     text        not null,              -- '1m' | '5m' | '1h' | '1d'
  ts            timestamptz not null,              -- bar OPEN time, UTC
  open          numeric(24,10) not null,
  high          numeric(24,10) not null,
  low           numeric(24,10) not null,
  close         numeric(24,10) not null,
  volume        numeric(28,10),
  is_closed     boolean     not null default true,
  source        text        not null,
  official      boolean     not null default false,
  as_of         timestamptz not null,
  primary key (instrument_id, timeframe, ts)
);

comment on table public.bars is
  'OHLCV only. Retention: 1m=14d, 5m=90d, 1h=2y, 1d=forever. Applied by retention.sql.';

create index bars_ts_idx on public.bars (timeframe, ts desc);

-- ------------------------------------------------------------- strategies --

create table public.strategies (
  id          uuid primary key default gen_random_uuid(),
  name        text        not null,
  family      text        not null,   -- trend | breakout | meanrev | carry | crosssec | event | ensemble
  asset_class public.asset_class not null,
  description text,
  template_id text,                    -- set when created from a built-in template
  is_template boolean     not null default false,
  created_by  uuid references auth.users(id) on delete set null,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create table public.strategy_versions (
  id             uuid primary key default gen_random_uuid(),
  strategy_id    uuid not null references public.strategies(id) on delete cascade,
  version        integer     not null,
  params         jsonb       not null default '{}'::jsonb,
  code_ref       text,                 -- path in packages/strategy-core, or NULL for templates
  code_hash      text,                 -- sha256 of the code, for reproducibility
  status         public.strategy_status not null default 'draft',
  timeframes     text[]      not null default '{1h,1d}',
  risk_spec      jsonb       not null default '{}'::jsonb,
  -- Provenance of the parameters
  origin         text        not null default 'human',  -- 'human' | 'ai_mutation' | 'ai_codegen' | 'template'
  -- AI provenance (null for human-written versions)
  model_id       text,
  prompt_hash    text,
  seed           integer,
  parent_version_id uuid references public.strategy_versions(id) on delete set null,
  -- Trial accounting. EVERY candidate consumes exactly one trial id.
  trial_id       uuid        not null default gen_random_uuid(),
  created_by     uuid references auth.users(id) on delete set null,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (strategy_id, version)
);

create unique index strategy_versions_trial_idx on public.strategy_versions (trial_id);
create index strategy_versions_status_idx on public.strategy_versions (status);

comment on column public.strategy_versions.origin is
  'ai_mutation | ai_codegen candidates increment n_trials at creation (I-13).';

create table public.backtests (
  id            uuid primary key default gen_random_uuid(),
  version_id    uuid not null references public.strategy_versions(id) on delete cascade,
  label         text,
  period_start  date not null,
  period_end    date not null,
  timeframe     text        not null,
  metrics       jsonb       not null default '{}'::jsonb,
  trades        integer     not null default 0,
  data_source   text        not null,
  data_official boolean     not null default false,
  data_as_of    timestamptz,
  -- Trial accounting
  n_trials      integer     not null default 1,
  holdout_reads integer     not null default 0,
  -- Reproduction
  engine        text        not null default 'vectorbt',
  engine_version text,
  code_hash     text,
  params_hash   text,
  artifact_url  text,        -- equity curve / trades in Supabase Storage
  created_at    timestamptz not null default now()
);

create index backtests_version_idx on public.backtests (version_id, created_at desc);

create table public.performance (
  version_id    uuid not null references public.strategy_versions(id) on delete cascade,
  asset_class   public.asset_class not null,
  timeframe     text        not null,
  period_start  date        not null,
  period_end    date        not null,
  trades        integer     not null,
  win_rate      numeric(8,6),
  expectancy    numeric(18,10),
  profit_factor numeric(12,6),
  cagr          numeric(12,6),
  sharpe        numeric(12,6),
  sortino       numeric(12,6),
  calmar        numeric(12,6),
  max_drawdown  numeric(12,6),
  exposure_pct  numeric(8,6),
  -- Deflated statistics (I-17 neighbourhood stability, I-13 trial accounting)
  dsr           numeric(12,6),
  pbo           numeric(12,6),
  n_trials      integer     not null default 1,
  -- Neighbourhood stability: what fraction of the candidate's parameter
  -- neighbours also passed. An isolated peak scores near 0.
  neighbour_pass_rate numeric(6,4),
  -- AI Feature Gate (I-14): Sharpe gained by adding AI-derived news features
  -- versus the same strategy without them. Negative means the AI added noise.
  ai_feature_gain_sharpe numeric(12,6),
  -- Benchmark context: a crypto strategy is judged against BTC, not against zero.
  benchmark     text,
  benchmark_ret numeric(18,10),
  data_source   text        not null,
  data_official boolean     not null default false,
  as_of         timestamptz not null default now(),
  primary key (version_id, asset_class, timeframe, period_start, period_end)
);

comment on table public.performance is
  'Measured performance WITH its denominator. Every row carries n_trials and provenance.';

create table public.champions (
  asset_class   public.asset_class not null,
  timeframe     text        not null,
  version_id    uuid not null references public.strategy_versions(id) on delete cascade,
  promoted_by   uuid references auth.users(id) on delete set null,
  promoted_at   timestamptz not null default now(),
  gate_result   jsonb       not null default '{}'::jsonb,
  -- The published before/after comparison, with denominator (I-7, condition 3)
  before_after  jsonb       not null default '{}'::jsonb,
  active        boolean     not null default true,
  primary key (asset_class, timeframe)
);

create table public.holdout_ledger (
  version_id     uuid primary key references public.strategy_versions(id) on delete cascade,
  reads          integer     not null default 0,
  last_read_at   timestamptz,
  purpose        text,
  note           text
);

comment on table public.holdout_ledger is
  'Counts holdout reads. The protocol ERRORS above 1 read for a final candidate (I-16).';

-- --------------------------------------------------------------- signals ---

create table public.signals (
  id             uuid primary key default gen_random_uuid(),
  instrument_id  uuid not null references public.instruments(id) on delete cascade,
  version_id     uuid references public.strategy_versions(id) on delete set null,
  timeframe      text        not null,
  side           public.signal_side not null,
  status         public.signal_status not null default 'active',
  entry_price    numeric(24,10),
  entry_low      numeric(24,10),
  entry_high     numeric(24,10),
  stop_price     numeric(24,10),
  target_price   numeric(24,10),
  target_2_price numeric(24,10),
  confidence     numeric(6,4),
  rule_id        text        not null,
  -- Immutability: a signal is computed on a CLOSED bar and never revised.
  bar_close_time timestamptz not null,
  data_as_of     timestamptz not null,
  -- Deterministic idempotency key: one signal per (strategy, instrument, bar, rule)
  signal_key     text        not null unique,
  -- The odds band from the Time Machine (advisory only, never gates promotion)
  odds_band      jsonb       not null default '{}'::jsonb,
  notes          jsonb       not null default '{}'::jsonb,
  expires_at     timestamptz,
  created_at     timestamptz not null default now()
);

create index signals_instrument_idx on public.signals (instrument_id, timeframe, created_at desc);
create index signals_active_idx on public.signals (status) where status = 'active';

create trigger strategy_versions_updated_at before update on public.strategy_versions
  for each row execute function public.set_updated_at();
create trigger strategies_updated_at before update on public.strategies
  for each row execute function public.set_updated_at();
