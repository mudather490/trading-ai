-- =============================================================================
-- 0002_ai_sidecar.sql
--
-- AI is a SIDECAR, never the chassis (I-11).
--
-- Every table here is written by the AI sidecar and read by the deterministic
-- core. Nothing in this schema may influence position sizing, promotion, or
-- order placement. The enforcement of that boundary is:
--   * these tables have no foreign key INTO signals/tickets/champions
--   * the RPC functions in 0005 refuse to read them for sizing decisions
--   * strategy_proposals only ever creates CANDIDATE versions (status draft)
-- =============================================================================

-- ------------------------------------------------------------- ai_runs -----

create table public.ai_runs (
  id            uuid primary key default gen_random_uuid(),
  -- Which capability ran: 'news_extract' | 'news_sentiment' | 'narrate'
  --                     | 'explain' | 'propose_mutation' | 'propose_code'
  --                     | 'rank_strategies' | 'alert_summary' | 'chat'
  capability    text        not null,
  -- Provider + pinned model. Free tiers rotate models, so the exact id matters.
  provider      text        not null,      -- 'gemini' | 'groq' | 'local' | 'openai'
  model_id      text        not null,
  -- Reproducibility (I-8): same inputs + same seed + same model => same output
  prompt_hash   text        not null,
  prompt_version text       not null default 'v1',
  seed          integer,
  temperature   numeric(4,3) not null default 0,
  input_hash    text        not null,
  input_preview text,
  output_json   jsonb,
  -- Schema validation (I-12): invalid output is dropped and COUNTED, never coerced
  valid         boolean     not null default false,
  invalid_reason text,
  -- Cost + latency
  tokens_in     integer,
  tokens_out    integer,
  latency_ms    integer,
  cost_usd      numeric(12,8) not null default 0,
  -- Free-tier quota tracking (A5)
  quota_bucket  text,
  -- Privacy: only PUBLIC market news may reach a free-tier provider (I-19)
  data_class    text        not null default 'public',
  -- Which user this run was on behalf of (nullable for background jobs)
  user_id       uuid references auth.users(id) on delete set null,
  created_at    timestamptz not null default now()
);

create index ai_runs_capability_idx on public.ai_runs (capability, created_at desc);
create index ai_runs_user_idx on public.ai_runs (user_id, created_at desc);
create index ai_runs_created_idx on public.ai_runs (created_at desc);

comment on table public.ai_runs is
  'Every AI call is logged with model, prompt hash, seed and validation result. '
  'data_class must be ''public'' for any free-tier provider (I-19).';

-- ------------------------------------------------------- news_features -----

create table public.news_features (
  id            uuid primary key default gen_random_uuid(),
  news_id       uuid not null,
  instrument_id uuid references public.instruments(id) on delete cascade,
  -- Structured extraction (E3: structure beats sentiment)
  event_type    text,                     -- earnings | m_and_a | regulatory | guidance | macro | ...
  impact_subject text,                    -- company | supplier | competitor | sector | macro
  time_horizon  text,                     -- intraday | swing | structural
  confidence    public.news_confidence,
  -- Sentiment, from the local FinBERT stage (free, private, unlimited)
  sentiment     numeric(8,6),
  sentiment_model text,
  -- Which stage produced each field
  extract_stage text        not null default 'llm',   -- 'deterministic' | 'llm'
  -- Whether the model had to reason about an IMPLICIT entity (E5)
  implicit_entity boolean not null default false,
  -- Provenance
  model_id      text,
  prompt_hash   text,
  run_id        uuid references public.ai_runs(id) on delete set null,
  -- Point-in-time: this is the field the join assertion checks (I-16)
  published_at  timestamptz not null,
  as_of         timestamptz not null default now(),
  unique (news_id, instrument_id)
);

create index news_features_instrument_idx on public.news_features (instrument_id, published_at desc);
create index news_features_pub_idx on public.news_features (published_at);

comment on table public.news_features is
  'Structured news features per instrument. Legal as a signal input at 1h/1d only; '
  'forbidden at 1m (condition 8). published_at is the point-in-time join key (I-16).';

-- -------------------------------------------------- strategy_proposals ----

create table public.strategy_proposals (
  id             uuid primary key default gen_random_uuid(),
  -- 'mutation' (Tier A: parameter mutation) | 'codegen' (Tier B: sandboxed code)
  tier           text        not null default 'mutation',
  parent_version_id uuid references public.strategy_versions(id) on delete set null,
  -- Tier A
  proposed_params jsonb,
  -- Tier B
  proposed_code   text,
  code_hash       text,
  language        text default 'python',
  -- Sandbox provenance: where it was smoke-tested and evaluated
  sandbox_stage  text,        -- 'ast_gate' | 'smoke_test' | 'full_eval' | 'rejected'
  sandbox_log    text,
  ast_verdict    jsonb,
  -- AI provenance
  model_id       text,
  prompt_hash    text,
  seed           integer,
  run_id         uuid references public.ai_runs(id) on delete set null,
  -- Trial accounting: the proposal CONSUMES a trial id the moment it is made (I-13)
  trial_id       uuid        not null default gen_random_uuid(),
  n_trials_after integer,
  status         text        not null default 'proposed',
  -- Who submitted it. Trusted users may propose; only the owner resolves.
  created_by     uuid references auth.users(id) on delete set null,
  -- What the deterministic gate said
  gate_result    jsonb       not null default '{}'::jsonb,
  created_at     timestamptz not null default now()
);

create unique index strategy_proposals_trial_idx on public.strategy_proposals (trial_id);
create index strategy_proposals_status_idx on public.strategy_proposals (status, created_at desc);

comment on table public.strategy_proposals is
  'AI-proposed strategy candidates. Every row consumes a trial id at creation time, '
  'so N_trials reflects the true size of the search space (A1).';

-- ------------------------------------------------------ ai_rankings --------

create table public.ai_rankings (
  id            uuid primary key default gen_random_uuid(),
  -- 'strategy_rank' | 'news_severity'
  capability    text        not null,
  scope         text,
  -- The AI's pick vs the deterministic pick
  ai_pick       text        not null,
  deterministic_pick text,
  -- Realised outcome used to score the pick
  outcome       jsonb       not null default '{}'::jsonb,
  -- Rolling score (AI Ranking Score, I-15). When it falls below the
  -- deterministic baseline, AI ranking is automatically demoted.
  score         numeric(12,6),
  baseline_score numeric(12,6),
  demoted       boolean     not null default false,
  demoted_at    timestamptz,
  period_start  date,
  period_end    date,
  notes         text,
  created_at    timestamptz not null default now()
);

create index ai_rankings_capability_idx on public.ai_rankings (capability, created_at desc);

-- ------------------------------------------------------ model_registry -----

create table public.model_registry (
  id            uuid primary key default gen_random_uuid(),
  provider      text        not null,
  model_id      text        not null,
  capability    text        not null,
  prompt_version text       not null default 'v1',
  prompt_hash   text,
  temperature   numeric(4,3) not null default 0,
  seed          integer,
  is_default    boolean     not null default false,
  free_tier     boolean     not null default false,
  quota_daily   integer,
  data_class    text        not null default 'public',
  enabled       boolean     not null default true,
  created_at    timestamptz not null default now(),
  unique (provider, model_id, capability, prompt_version)
);

comment on table public.model_registry is
  'Pinned models and prompt versions. Free-tier models rotate, so the exact id and '
  'prompt hash are recorded for every run (A8, I-11).';

-- ---------------------------------------------------- prompt_registry -----

create table public.prompt_registry (
  id            uuid primary key default gen_random_uuid(),
  name          text        not null,
  version       text        not null,
  template      text        not null,
  hash          text        not null,
  capability    text        not null,
  output_schema jsonb,
  created_at    timestamptz not null default now(),
  unique (name, version)
);
