-- =============================================================================
-- 0004_rls_policies.sql
--
-- Multi-user model: OWNER + TRUSTED PEOPLE.
--
--   SHARED READ   instruments, quotes, bars, signals, strategies, versions,
--                 performance, champions, news, catalysts, event_stats,
--                 positioning, ai_* artefacts  (the shared value)
--   USER-OWNED   user_profiles, tickets, journal, alerts, conversations, messages
--   OWNER-ONLY    audit_log, jobs, worker_heartbeat, strategy mutations
--   SERVICE ROLE  the live worker only (bypasses RLS by design)
--
-- The `anon` role can read NOTHING. Verified by supabase/tests/rls_test.sql.
-- =============================================================================

-- SECURITY DEFINER so a policy on user_profiles can call these without recursing.
create or replace function public.is_owner()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from public.user_profiles p
    where p.user_id = auth.uid() and p.role = 'owner'
  );
$$;

create or replace function public.is_owner_or_self(target uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select auth.uid() = target or public.is_owner();
$$;

revoke all on function public.is_owner() from public;
revoke all on function public.is_owner_or_self(uuid) from public;
grant execute on function public.is_owner() to authenticated, service_role;
grant execute on function public.is_owner_or_self(uuid) to authenticated, service_role;

-- ---------------------------------------------------------------- helpers --

-- Enable RLS everywhere. The worker uses the service role, which bypasses it.
do $$
declare t text;
begin
  foreach t in array array[
    'instruments','latest_quotes','bars','strategies','strategy_versions',
    'backtests','performance','champions','holdout_ledger','signals',
    'ai_runs','news_features','strategy_proposals','ai_rankings',
    'model_registry','prompt_registry',
    'user_profiles','tickets','journal','news','catalysts','event_stats',
    'positioning','alerts','jobs','worker_heartbeat','audit_log',
    'conversations','messages'
  ] loop
    execute format('alter table public.%I enable row level security', t);
  end loop;
end $$;

-- --------------------------------------------------------- shared reads ---
-- Any authenticated user may read the shared knowledge layer.
-- `anon` gets nothing (no policy => no rows).

do $$
declare t text;
begin
  foreach t in array array[
    'instruments','latest_quotes','bars','strategies','strategy_versions',
    'backtests','performance','champions','holdout_ledger','signals',
    'news_features','strategy_proposals','ai_rankings',
    'model_registry','prompt_registry',
    'news','catalysts','event_stats','positioning'
  ] loop
    execute format(
      'create policy %I on public.%I for select to authenticated using (true)', t, t);
  end loop;
end $$;

-- `ai_runs` holds prompts and raw model output: owner-only, not shared.
create policy ai_runs_owner_read on public.ai_runs
  for select to authenticated using (public.is_owner());

-- ------------------------------------------------------- user-owned -------

create policy user_profiles_read_own on public.user_profiles
  for select to authenticated using (public.is_owner_or_self(user_id));

-- A user may create their own profile on first sign-in.
create policy user_profiles_insert_own on public.user_profiles
  for insert to authenticated with check (auth.uid() = user_id);

create policy user_profiles_update_own on public.user_profiles
  for update to authenticated using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- Only the owner may change anyone's role or risk budget.
create policy user_profiles_update_owner on public.user_profiles
  for update to authenticated using (public.is_owner())
  with check (public.is_owner());

create policy tickets_own on public.tickets
  for all to authenticated
  using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy journal_own on public.journal
  for all to authenticated
  using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- Alerts are private to their recipient. A trusted user's alerts reveal their
-- trades, so the owner does NOT get blanket read access to them.
-- System alerts (user_id IS NULL) are owner-visible.
create policy alerts_own on public.alerts
  for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

create policy alerts_system_owner_read on public.alerts
  for select to authenticated using (user_id is null and public.is_owner());

create policy conversations_own on public.conversations
  for all to authenticated
  using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy messages_own on public.messages
  for all to authenticated
  using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- ------------------------------------------------------- owner-only -------

create policy audit_log_owner on public.audit_log
  for select to authenticated using (public.is_owner());

create policy jobs_owner on public.jobs
  for all to authenticated using (public.is_owner()) with check (public.is_owner());

create policy worker_heartbeat_owner on public.worker_heartbeat
  for select to authenticated using (public.is_owner());

-- Strategy mutation is owner-only: trusted users can READ and PROPOSE via
-- the proposals table, but cannot change the library or the champion.
create policy strategies_owner_write on public.strategies
  for all to authenticated using (public.is_owner()) with check (public.is_owner());

create policy strategy_versions_owner_write on public.strategy_versions
  for all to authenticated using (public.is_owner()) with check (public.is_owner());

create policy backtests_owner_write on public.backtests
  for all to authenticated using (public.is_owner()) with check (public.is_owner());

create policy performance_owner_write on public.performance
  for all to authenticated using (public.is_owner()) with check (public.is_owner());

create policy champions_owner_write on public.champions
  for all to authenticated using (public.is_owner()) with check (public.is_owner());

create policy signals_owner_write on public.signals
  for all to authenticated using (public.is_owner()) with check (public.is_owner());

create policy news_owner_write on public.news
  for all to authenticated using (public.is_owner()) with check (public.is_owner());

create policy catalysts_owner_write on public.catalysts
  for all to authenticated using (public.is_owner()) with check (public.is_owner());

create policy positioning_owner_write on public.positioning
  for all to authenticated using (public.is_owner()) with check (public.is_owner());

create policy ai_runs_owner_write on public.ai_runs
  for all to authenticated using (public.is_owner()) with check (public.is_owner());

create policy news_features_owner_write on public.news_features
  for all to authenticated using (public.is_owner()) with check (public.is_owner());

-- Trusted users may SUBMIT proposals; only the owner may resolve or edit them.
create policy strategy_proposals_insert on public.strategy_proposals
  for insert to authenticated with check (created_by = auth.uid());

create policy strategy_proposals_update_owner on public.strategy_proposals
  for update to authenticated using (public.is_owner()) with check (public.is_owner());

create policy ai_rankings_owner_write on public.ai_rankings
  for all to authenticated using (public.is_owner()) with check (public.is_owner());

create policy model_registry_owner_write on public.model_registry
  for all to authenticated using (public.is_owner()) with check (public.is_owner());

create policy prompt_registry_owner_write on public.prompt_registry
  for all to authenticated using (public.is_owner()) with check (public.is_owner());

create policy holdout_ledger_owner_write on public.holdout_ledger
  for all to authenticated using (public.is_owner()) with check (public.is_owner());
