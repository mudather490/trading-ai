-- =============================================================================
-- 0003_user_ops_schema.sql
-- Users, tickets, journal, news, positioning, jobs, audit, alerts.
-- =============================================================================

create type public.alert_severity as enum ('info', 'warning', 'critical');
create type public.alert_state as enum ('pending', 'sent', 'read', 'acked');

-- ---------------------------------------------------------- user_profiles --

create table public.user_profiles (
  user_id         uuid primary key references auth.users(id) on delete cascade,
  role            public.user_role not null default 'trusted',
  display_name    text,
  -- Risk budget used by the ticket sizing function
  capital_usd     numeric(18,4),
  risk_per_trade_pct numeric(6,4) not null default 0.005,   -- 0.5%
  max_position_pct   numeric(6,4) not null default 0.20,     -- 20% of equity
  max_gross_pct      numeric(6,4) not null default 1.00,
  max_correlated_pct numeric(6,4) not null default 0.30,
  max_concurrent     integer     not null default 5,
  max_tickets_per_day integer    not null default 10,
  venues         text[]      not null default '{}',
  timezone       text        not null default 'UTC',
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

create trigger user_profiles_updated_at before update on public.user_profiles
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------- tickets --

create table public.tickets (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  signal_id     uuid references public.signals(id) on delete set null,
  instrument_id uuid references public.instruments(id) on delete set null,
  timeframe     text        not null,
  side          public.signal_side not null,
  entry_low     numeric(24,10),
  entry_high    numeric(24,10),
  stop_price    numeric(24,10),
  target_price  numeric(24,10),
  target_2_price numeric(24,10),
  size_units    numeric(28,10),
  size_notional numeric(18,4),
  risk_amount   numeric(18,4),
  reward_risk   numeric(12,6),
  confidence    numeric(6,4),
  -- The denominator. A ticket without these is not publishable.
  metrics       jsonb       not null default '{}'::jsonb,
  strategy_id   uuid references public.strategies(id) on delete set null,
  version_id    uuid references public.strategy_versions(id) on delete set null,
  -- Provenance: which feed, which quality tier
  data_source   text,
  data_quality  public.data_quality,
  data_as_of    timestamptz not null,
  -- Odds band from the Time Machine (advisory)
  odds_band     jsonb       not null default '{}'::jsonb,
  -- Warnings, e.g. 'FOMC in 6h', 'spread too wide'
  warnings      jsonb       not null default '[]'::jsonb,
  state         public.ticket_state not null default 'active',
  expires_at    timestamptz,
  created_at    timestamptz not null default now()
);

create index tickets_user_idx on public.tickets (user_id, created_at desc);
create index tickets_state_idx on public.tickets (state) where state = 'active';

-- ---------------------------------------------------------------- journal --

create table public.journal (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  ticket_id     uuid references public.tickets(id) on delete set null,
  instrument_id uuid references public.instruments(id) on delete set null,
  action        text        not null,   -- 'took' | 'skipped' | 'invalidated' | 'partial'
  note          text,
  -- Filled in later: what actually happened
  entry_price   numeric(24,10),
  exit_price    numeric(24,10),
  quantity      numeric(28,10),
  pnl_usd       numeric(18,4),
  r_multiple    numeric(12,6),
  outcome_state text,        -- 'open' | 'closed' | 'unknown'
  created_at    timestamptz not null default now()
);

create index journal_user_idx on public.journal (user_id, created_at desc);

-- ------------------------------------------------------------------ news --

create table public.news (
  id            uuid primary key default gen_random_uuid(),
  source        text        not null,
  url           text,
  title         text        not null,
  summary       text,
  published_at  timestamptz not null,
  -- Deterministic stage
  event_type    text,
  -- Which instruments this touches (array of instrument ids)
  instruments   uuid[]      not null default '{}',
  -- Stage provenance
  stage         text        not null default 'deterministic',
  raw           jsonb       not null default '{}'::jsonb,
  -- Dedupe
  content_hash  text        not null unique,
  created_at    timestamptz not null default now()
);

create index news_published_idx on public.news (published_at desc);
create index news_instruments_idx on public.news using gin (instruments);

create table public.catalysts (
  id            uuid primary key default gen_random_uuid(),
  instrument_id uuid not null references public.instruments(id) on delete cascade,
  kind          text        not null,   -- 'earnings' | 'macro' | 'filing' | 'dividend' | 'economic'
  title         text        not null,
  occurs_at     timestamptz not null,
  -- How much this should affect a trade
  severity      public.event_severity not null default 'info',
  source        text,
  url           text,
  -- Confirmed by the deterministic calendar stage
  confirmed     boolean     not null default true,
  metadata      jsonb       not null default '{}'::jsonb,
  created_at    timestamptz not null default now()
);

create index catalysts_instrument_idx on public.catalysts (instrument_id, occurs_at);

-- Event study: what actually happened after these events, historically
create table public.event_stats (
  event_type    text        not null,
  asset_class   public.asset_class not null,
  horizon       text        not null,   -- '1h' | '1d' | '5d'
  n             integer     not null,
  mean_abnormal_return numeric(18,10),
  median_abnormal_return numeric(18,10),
  hit_rate      numeric(8,6),
  ci_low        numeric(18,10),
  ci_high       numeric(18,10),
  period_start  date,
  period_end    date,
  data_source   text,
  -- Below 30 observations we say so rather than pretending (condition 2, Phase 4)
  sufficient_sample boolean not null default false,
  as_of         timestamptz not null default now(),
  primary key (event_type, asset_class, horizon)
);

comment on table public.event_stats is
  'Historical abnormal return per event type. Always carries n and a CI. '
  'n < 30 => sufficient_sample = false and the UI says so.';

-- ----------------------------------------------------------- positioning --

create table public.positioning (
  id            uuid primary key default gen_random_uuid(),
  instrument_id uuid not null references public.instruments(id) on delete cascade,
  metric        text        not null,   -- 'taker_buy_ratio' | 'funding_rate' | 'open_interest'
                                          -- | 'long_short_ratio' | 'cot_net' | 'short_interest'
  value         numeric(28,10),
  -- Freshness honesty: real-time | daily | weekly | delayed
  granularity   text        not null,
  lag_note      text,
  source        text        not null,
  as_of         timestamptz not null,
  created_at    timestamptz not null default now()
);

create index positioning_instrument_idx on public.positioning (instrument_id, metric, as_of desc);

-- ----------------------------------------------------------------- alerts --

create table public.alerts (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid references auth.users(id) on delete cascade,
  kind          text        not null,   -- 'signal' | 'stop' | 'target' | 'news' | 'engine' | 'promotion'
  severity      public.alert_severity not null default 'info',
  title         text        not null,
  body          text,
  -- The deterministic rule that fired it (I-21: rules fire, AI writes the text)
  rule_id       text        not null,
  payload       jsonb       not null default '{}'::jsonb,
  -- Optional AI-written summary. Never the trigger.
  ai_summary    text,
  ai_run_id     uuid references public.ai_runs(id) on delete set null,
  state         public.alert_state not null default 'pending',
  -- Dedupe so we do not notify twice for the same event
  dedupe_key    text        not null unique,
  created_at    timestamptz not null default now(),
  sent_at       timestamptz
);

create index alerts_user_idx on public.alerts (user_id, created_at desc);
create index alerts_state_idx on public.alerts (state) where state = 'pending';

-- ------------------------------------------------------------------- jobs --

create table public.jobs (
  id            uuid primary key default gen_random_uuid(),
  kind          text        not null,   -- 'news_refresh' | 'research' | 'backfill' | 'retention' | 'backup'
  payload       jsonb       not null default '{}'::jsonb,
  priority      integer     not null default 0,
  status        text        not null default 'pending',  -- pending | running | done | failed
  attempts      integer     not null default 0,
  claimed_by    text,
  claimed_at    timestamptz,
  last_error    text,
  result        jsonb,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index jobs_pending_idx on public.jobs (priority desc, created_at)
  where status = 'pending';

create trigger jobs_updated_at before update on public.jobs
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------- worker heartbeat

create table public.worker_heartbeat (
  worker_id     text primary key,
  version       text,
  started_at    timestamptz not null default now(),
  last_seen_at  timestamptz not null default now(),
  streams       jsonb       not null default '{}'::jsonb,
  lag_ms        jsonb       not null default '{}'::jsonb,
  meta          jsonb       not null default '{}'::jsonb
);

-- ------------------------------------------------------------- audit log --

create table public.audit_log (
  id            bigserial primary key,
  actor         uuid references auth.users(id) on delete set null,
  action        text        not null,
  entity        text,
  entity_id     text,
  before        jsonb,
  after         jsonb,
  ip            inet,
  created_at    timestamptz not null default now()
);

create index audit_log_created_idx on public.audit_log (created_at desc);
create index audit_log_actor_idx on public.audit_log (actor, created_at desc);

-- ------------------------------------------------- conversations (chat) ---

create table public.conversations (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  title         text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create table public.messages (
  id            uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  user_id       uuid not null references auth.users(id) on delete cascade,
  role          text        not null,   -- 'user' | 'assistant' | 'system'
  content       text        not null,
  -- What the assistant actually retrieved. This is the audit trail for the
  -- "every number came from a tool result" rule (I-5).
  tool_calls    jsonb       not null default '[]'::jsonb,
  sources       jsonb       not null default '[]'::jsonb,
  -- Numbers that failed the grounded-number validator and were stripped
  stripped_numbers jsonb    not null default '[]'::jsonb,
  created_at    timestamptz not null default now()
);

create index messages_conversation_idx on public.messages (conversation_id, created_at);

create trigger conversations_updated_at before update on public.conversations
  for each row execute function public.set_updated_at();
